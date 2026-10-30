"""Exporting and installing one archive, including the ways that must be refused."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

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
    monkeypatch.setattr(bundle, "_zenodo_file",
                        lambda record=None: ("https://zenodo.invalid/oam.tar", "oam-2026-09-23.tar"))

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


def _installed_from(tar, tmp_path):
    oam.set_data_dir(tmp_path / "installed")
    oam.download(source=tar)


def _zenodo_offers(monkeypatch, name, seen=None):
    def fake(record=None):
        if seen is not None:
            seen.append(record)
        return "https://zenodo.invalid/" + name, name
    monkeypatch.setattr(bundle, "_zenodo_file", fake)
    monkeypatch.setattr(bundle, "_materialize",
                        lambda src: pytest.fail(f"downloaded {src}"))


def test_latest_already_installed_downloads_nothing(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    _zenodo_offers(monkeypatch, "oam-2026-09-23.tar")
    assert oam.download() == "2026-09-23"


def test_newer_snapshot_needs_replace_before_downloading(built, tmp_path, monkeypatch):
    _installed_from(oam.export(tmp_path / "dist"), tmp_path)
    _zenodo_offers(monkeypatch, "oam-2027-01-15.tar")
    with pytest.raises(RuntimeError, match="replace=True"):
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
