"""Exporting and installing one archive, including the ways that must be refused."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import oam
from oam import config
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
    info = oam.download(source=tar)
    assert info["snapshot"] == "2026-09-23"
    assert "tier" not in info
    after = _scores(ids)
    for key in ("P", "TCS", "MNCS", "PP_top10"):
        assert after[key] == pytest.approx(before[key])

    again = oam.download(source=tar)
    assert again["snapshot"] == "2026-09-23"


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

    info = oam.download(source=other, replace=True)
    assert info["snapshot"] == "1999-01-01"


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


def test_download_without_a_record_says_so(tmp_path):
    oam.set_data_dir(tmp_path / "empty")
    with pytest.raises(RuntimeError, match="ZENODO_RECORD"):
        oam.download()


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
