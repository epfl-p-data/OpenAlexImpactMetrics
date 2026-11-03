"""Exporting and installing one archive, including the ways that must be refused."""

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import duckdb
import pytest
import requests

import oam
from oam import bundle, config
from oam.bundle import _safe_extract


def _scores(ids):
    frame = oam.aggregate(ids, by=None)
    return frame.iloc[0].to_dict()


def test_export_then_download_scores_the_same(built, tmp_path):
    ids = [101, 102, 103, 104, 201, 202]
    before = _scores(ids)

    tar = oam.export(tmp_path / "dist")
    assert tar.name == "oam-2026-09-23.tar"

    oam.set_data_dir(tmp_path / "installed")
    assert oam.download(source=tar) == "2026-09-23"
    after = _scores(ids)
    for key in ("P_full", "TCS_full", "MNCS_full", "PP_top10_full"):
        assert after[key] == pytest.approx(before[key])

    assert oam.download(source=tar) == "2026-09-23"


def test_old_format_is_refused_and_replaced(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    meta = json.loads(config.BUNDLE_PATH.read_text())
    meta["format"] = config.BUNDLE_FORMAT - 1
    config.BUNDLE_PATH.write_text(json.dumps(meta))
    with pytest.raises(RuntimeError, match="format"):
        oam.get_metrics([101])

    fetched = []
    remote = json.loads(config.BUNDLE_PATH.read_text())
    remote["format"] = config.BUNDLE_FORMAT
    monkeypatch.setattr(
        bundle, "_zenodo_file",
        lambda record=None: ("https://zenodo.invalid/oam.tar", "oam-2026-09-23.tar", 1000, 1))
    monkeypatch.setattr(bundle, "_remote_meta", lambda url, size: remote)

    def stop(src):
        fetched.append(src)
        raise RuntimeError("fetched")

    monkeypatch.setattr(bundle, "_materialize", stop)
    with pytest.raises(RuntimeError, match="fetched"):
        oam.download()
    assert fetched


def test_snapshot_mismatch_needs_replace(built, tmp_path):
    tar = oam.export(tmp_path / "dist")
    installed = tmp_path / "installed"
    oam.set_data_dir(installed)
    oam.download(source=tar)

    other = _retar(tar, tmp_path / "oam-1999-01-01.tar", snapshot="1999-01-01")
    with pytest.raises(RuntimeError, match="replace=True"):
        oam.download(source=other)
    # The installed snapshot is untouched.
    assert json.loads(config.STORE_STATE_PATH.read_text())["date"] == "2026-09-23"

    assert oam.download(source=other, replace=True) == "1999-01-01"


def test_bad_checksum_is_refused(tmp_path):
    tar = _tiny_tar(tmp_path / "oam-2026-09-23.tar", sha="0" * 64)
    oam.set_data_dir(tmp_path / "data")
    with pytest.raises(RuntimeError, match="checksum"):
        oam.download(source=tar)
    assert not (tmp_path / "data" / "metrics").exists()


def test_path_escape_is_refused(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    tar = tmp_path / "escape.tar"
    with tarfile.open(tar, "w") as archive:
        payload = json.dumps({"format": config.BUNDLE_FORMAT, "files": {}}).encode()
        info = tarfile.TarInfo("bundle.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        evil = tarfile.TarInfo("../evil.txt")
        body = b"nope"
        evil.size = len(body)
        archive.addfile(evil, io.BytesIO(body))
    with pytest.raises(RuntimeError, match="unsafe path"):
        _safe_extract(tar, dest)
    assert not (tmp_path / "evil.txt").exists()


def test_a_partial_download_of_another_version_is_not_resumed(tmp_path, monkeypatch):
    oam.set_data_dir(tmp_path / "data")
    stale = config.DATA_DIR / "_downloads" / "0123456789abcdef.tar"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"half of an older version")
    monkeypatch.setattr(bundle, "_download_file", lambda url, dest: dest.touch())
    path = bundle._materialize("https://zenodo.invalid/records/2/files/oam-2027-01-15.tar/content")
    assert list(stale.parent.iterdir()) == [path]


def test_download_without_a_record_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ZENODO_RECORD", None)
    oam.set_data_dir(tmp_path / "empty")
    with pytest.raises(RuntimeError, match="ZENODO_RECORD"):
        oam.download()


def test_source_and_record_together_are_refused(tmp_path):
    with pytest.raises(ValueError, match="source or record"):
        oam.download(source=tmp_path / "oam-2026-09-23.tar", record=1)


def test_export_with_edges_writes_both_and_leaves_the_main_archive(built, tmp_path):
    plain = oam.export(tmp_path / "plain")
    both_dir = tmp_path / "both"
    both = oam.export(both_dir, edges=True)
    edges = both_dir / "edges-2026-09-23.tar"
    assert both.name == "oam-2026-09-23.tar"
    assert edges.is_file()
    plain_files = _tar_meta(plain)["files"]
    both_files = _tar_meta(both)["files"]
    assert set(plain_files) == set(both_files)
    assert {name: spec["rows"] for name, spec in plain_files.items()} == {
        name: spec["rows"] for name, spec in both_files.items()
    }
    assert not any(name.startswith("store/edges/") for name in both_files)
    meta = _tar_meta(edges, "edges.json")
    assert meta["format"] == config.EDGES_FORMAT
    assert meta["snapshot"] == "2026-09-23"
    assert meta["scope_hash"] == _tar_meta(both)["scope_hash"]
    with tarfile.open(edges) as archive:
        assert archive.getnames()[-1] == "edges.json"


def test_download_edges_installs_them_and_a_second_call_does_nothing(built, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    assert oam.download(source=dist, edges=True) == "2026-09-23"
    # A file source is the main archive; the links are the one edges-*.tar beside it.
    assert oam.download(source=dist / "oam-2026-09-23.tar", edges=True) == "2026-09-23"

    installed_file = config.DATA_DIR / "store" / "edges" / "part-0.parquet"
    spec = oam.installed()["edges"]["files"]["store/edges/part-0.parquet"]
    rows = duckdb.connect().execute(
        f"SELECT count(*) FROM read_parquet('{installed_file.as_posix()}')"
    ).fetchone()[0]
    assert rows == spec["rows"]
    assert rows > 0
    assert oam.citation_links()["snapshot"] == "2026-09-23"

    monkeypatch.setattr(bundle, "_install", lambda *a, **k: pytest.fail("reinstalled"))
    assert oam.download(source=dist, edges=True) == "2026-09-23"


def test_edges_that_do_not_match_are_refused(built, tmp_path):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=dist)
    edges = dist / "edges-2026-09-23.tar"
    cases = (
        ({"snapshot": "1999-01-01"}, "snapshot"),
        ({"scope_hash": "0" * 12}, "scope"),
        ({"format": config.EDGES_FORMAT + 1}, "format"),
    )
    for change, match in cases:
        folder = tmp_path / match
        folder.mkdir()
        shutil.copy(dist / "oam-2026-09-23.tar", folder)
        _rewrite_manifest(edges, folder / "edges-2026-09-23.tar", "edges.json", **change)
        with pytest.raises(RuntimeError, match=match):
            oam.download(source=folder, edges=True)
        assert oam.citation_links() is None
        assert not (config.DATA_DIR / "store" / "edges").exists()


def test_edges_bad_checksum_is_refused(built, tmp_path):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=dist)
    info = oam.installed()
    body = b"not parquet"
    rel = "store/edges/part-0.parquet"
    meta = {
        "format": config.EDGES_FORMAT,
        "snapshot": info["snapshot"],
        "scope_hash": info["scope_hash"],
        "files": {rel: {"rows": 0, "bytes": len(body), "sha256": "0" * 64}},
    }
    folder = tmp_path / "bad"
    folder.mkdir()
    shutil.copy(dist / "oam-2026-09-23.tar", folder)
    with tarfile.open(folder / "edges-2026-09-23.tar", "w") as archive:
        _add(archive, rel, body)
        _add(archive, "edges.json", json.dumps(meta).encode())
    with pytest.raises(RuntimeError, match="checksum"):
        oam.download(source=folder, edges=True)
    assert oam.citation_links() is None
    assert not (config.DATA_DIR / "store" / "edges").exists()


def test_reinstalling_the_main_archive_keeps_the_links(built, tmp_path):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=dist, edges=True)
    before = oam.installed()["edges"]
    assert oam.download(source=dist) == "2026-09-23"
    assert oam.installed()["edges"] == before
    assert (config.DATA_DIR / "store" / "edges" / "part-0.parquet").is_file()


def test_default_download_never_fetches_the_links(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    seen = []

    def fake(record=None, prefix="oam-"):
        seen.append(prefix)
        name = "oam-2026-09-23.tar" if prefix == "oam-" else "edges-2026-09-23.tar"
        return "https://zenodo.invalid/" + name, name, 1000, 1

    monkeypatch.setattr(bundle, "_zenodo_file", fake)
    monkeypatch.setattr(
        bundle, "_remote_meta",
        lambda url, size, member="bundle.json": json.loads(config.BUNDLE_PATH.read_text()))
    monkeypatch.setattr(bundle, "_materialize", lambda src: pytest.fail(f"downloaded {src}"))
    assert oam.download() == "2026-09-23"
    assert seen == ["oam-"]
    assert not (config.DATA_DIR / "store" / "edges").exists()


def test_a_listing_with_both_files_resolves_each(monkeypatch):
    payload = {
        "id": 5,
        "files": [
            {"key": "oam-2026-09-23.tar", "size": 10, "links": {"self": "https://z/oam"}},
            {"key": "edges-2026-09-23.tar", "size": 20, "links": {"self": "https://z/edges"}},
        ],
    }
    monkeypatch.setattr(bundle.requests, "get", lambda *a, **k: _Json(payload))
    assert bundle._zenodo_file()[1] == "oam-2026-09-23.tar"
    url, name, size, record_id = bundle._zenodo_file(prefix="edges-")
    assert (url, name, size, record_id) == ("https://z/edges", "edges-2026-09-23.tar", 20, 5)


def test_a_listing_without_citation_links_says_so(monkeypatch):
    payload = {
        "id": 5,
        "files": [
            {"key": "oam-2026-09-23.tar", "size": 10, "links": {"self": "https://z/oam"}},
        ],
    }
    monkeypatch.setattr(bundle.requests, "get", lambda *a, **k: _Json(payload))
    with pytest.raises(RuntimeError, match="no citation links"):
        bundle._zenodo_file(prefix="edges-")


def test_download_edges_reports_a_record_without_them(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)

    def fake(record=None, prefix="oam-"):
        if prefix == "edges-":
            raise RuntimeError(
                "Zenodo record 1 has no citation links (no edges-*.tar). "
                "This version was published without them."
            )
        return "https://zenodo.invalid/oam.tar", "oam-2026-09-23.tar", 1000, 1

    monkeypatch.setattr(bundle, "_zenodo_file", fake)
    monkeypatch.setattr(
        bundle, "_remote_meta",
        lambda url, size, member="bundle.json": json.loads(config.BUNDLE_PATH.read_text()))
    with pytest.raises(RuntimeError, match="no citation links"):
        oam.download(edges=True)


def test_https_source_cannot_carry_the_links():
    with pytest.raises(RuntimeError, match="https URL"):
        oam.download(source="https://example.invalid/oam-2026-09-23.tar", edges=True)


def test_unreachable_zenodo_keeps_installed_links(built, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=dist, edges=True)

    def offline(record=None, prefix="oam-"):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(bundle, "_zenodo_file", offline)
    assert oam.download(edges=True) == "2026-09-23"
    assert oam.citation_links() is not None


def test_status_names_the_optional_links(built, tmp_path, capsys):
    dist = tmp_path / "dist"
    oam.export(dist, edges=True)
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=dist)
    oam.status()
    assert "oam.download(edges=True)" in capsys.readouterr().out
    oam.download(source=dist, edges=True)
    oam.status()
    text = capsys.readouterr().out
    assert "citation links :" in text
    assert "oam.download(edges=True)" not in text


def _installed_from(tar, tmp_path):
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=tar)


def _zenodo_offers(monkeypatch, name, seen=None, meta=None):
    def fake(record=None):
        if seen is not None:
            seen.append(record)
        return "https://zenodo.invalid/" + name, name, 1000, 23770181
    monkeypatch.setattr(bundle, "_zenodo_file", fake)

    def remote(url, size):
        if meta is not None:
            return meta
        return json.loads(config.BUNDLE_PATH.read_text())
    monkeypatch.setattr(bundle, "_remote_meta", remote)
    monkeypatch.setattr(bundle, "_materialize",
                        lambda src: pytest.fail(f"downloaded {src}"))


def test_latest_already_installed_downloads_nothing(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    _zenodo_offers(monkeypatch, "oam-2026-09-23.tar")
    assert oam.download() == "2026-09-23"


def test_newer_snapshot_needs_replace_before_downloading(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    meta = json.loads(config.BUNDLE_PATH.read_text())
    meta["snapshot"] = "2027-01-15"
    _zenodo_offers(monkeypatch, "oam-2027-01-15.tar", meta=meta)
    with pytest.raises(RuntimeError, match="replace=True"):
        oam.download()


def test_same_snapshot_with_a_different_checksum_is_not_installed(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    meta = json.loads(config.BUNDLE_PATH.read_text())
    for spec in meta["files"].values():
        spec["sha256"] = "0" * 64
    fetched = []
    monkeypatch.setattr(
        bundle, "_zenodo_file",
        lambda record=None: ("https://zenodo.invalid/oam.tar", "oam-2026-09-23.tar", 1000, 1))
    monkeypatch.setattr(bundle, "_remote_meta", lambda url, size: meta)

    def stop(src):
        fetched.append(src)
        raise RuntimeError("fetched")

    monkeypatch.setattr(bundle, "_materialize", stop)
    with pytest.raises(RuntimeError, match="fetched"):
        oam.download()
    assert fetched


def test_a_missing_record_raises(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    response = requests.Response()
    response.status_code = 404

    def missing(record=None):
        raise requests.HTTPError("404", response=response)

    monkeypatch.setattr(bundle, "_zenodo_file", missing)
    with pytest.raises(RuntimeError, match="99999999"):
        oam.download(record=99999999)


def test_wrong_format_is_refused_before_download(monkeypatch):
    monkeypatch.setattr(
        bundle, "_zenodo_file",
        lambda record=None: ("https://zenodo.invalid/oam.tar", "oam-2026-09-23.tar", 1000, 1))
    monkeypatch.setattr(
        bundle, "_remote_meta",
        lambda url, size: {"format": config.BUNDLE_FORMAT - 1, "snapshot": "2026-09-23", "files": {}})
    monkeypatch.setattr(bundle, "_download_file", lambda *a, **k: pytest.fail("downloaded"))
    with pytest.raises(RuntimeError, match="format"):
        oam.download()


def test_record_pins_a_version(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    seen = []
    _zenodo_offers(monkeypatch, "oam-2026-09-23.tar", seen)
    oam.download(record=23493155)
    assert seen == [23493155]


def test_installed_data_is_kept_when_zenodo_is_unreachable(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)

    def offline(record=None):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(bundle, "_zenodo_file", offline)
    assert oam.download() == "2026-09-23"


def _tiny_tar(path, sha):
    body = b"not parquet"
    rel = "metrics/work_metrics/part-0.parquet"
    digest = hashlib.sha256(body).hexdigest()
    meta = {
        "format": config.BUNDLE_FORMAT,
        "snapshot": "2026-09-23",
        "files": {rel: {"rows": 0, "bytes": len(body), "sha256": sha or digest}},
    }
    with tarfile.open(path, "w") as archive:
        _add(archive, "bundle.json", json.dumps(meta).encode())
        _add(archive, rel, body)
    return path


def _retar(src, dest, snapshot):
    with tarfile.open(src, "r") as archive:
        meta = json.load(archive.extractfile(archive.getmember("bundle.json")))
        members = [m for m in archive.getmembers() if m.name != "bundle.json"]
        files = {m.name: archive.extractfile(m).read() for m in members}
    meta["snapshot"] = snapshot
    with tarfile.open(dest, "w") as archive:
        _add(archive, "bundle.json", json.dumps(meta).encode())
        for name, body in files.items():
            _add(archive, name, body)
    return dest


def _add(archive, name, body):
    info = tarfile.TarInfo(name)
    info.size = len(body)
    archive.addfile(info, io.BytesIO(body))


def _tar_meta(path, member="bundle.json"):
    with tarfile.open(path, "r") as archive:
        return json.load(archive.extractfile(archive.getmember(member)))


def _rewrite_manifest(src, dest, member, **changes):
    """A copy of `src` whose manifest member has `changes` applied. The member stays last."""
    with tarfile.open(src, "r") as archive:
        meta = json.load(archive.extractfile(archive.getmember(member)))
        members = [item for item in archive.getmembers() if item.name != member]
        files = {item.name: archive.extractfile(item).read() for item in members}
    meta.update(changes)
    with tarfile.open(dest, "w") as archive:
        for name, body in files.items():
            _add(archive, name, body)
        _add(archive, member, json.dumps(meta).encode())
    return dest


class _Json:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload
