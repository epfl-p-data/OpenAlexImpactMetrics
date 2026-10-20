"""Share a finished build, or install someone else's, without rebuilding it.

One archive holds every table a query reads: the scores, the scope status,
the dimension tables and the lookup tables resolve() uses. The build-only
tables (edges, citation_counts, works_scope) stay behind; a download cannot
rebuild the data, and nothing queries them.

export() compacts a table that is still thousands of part files into one
Parquet file sorted by work_id, which is how get_metrics and explain look
rows up. A table that is already one file is copied unchanged: the resolve
tables are sorted on their lookup key, and re-sorting them would make every
query read the whole file.

download() installs that archive into the data directory. With no source it
uses the latest version of the Zenodo record in config.ZENODO_RECORD, or the
version given by record. source may also be a local .tar, a directory
containing one, or an https URL.
"""

import hashlib
import json
import os
import shutil
import tarfile
import time
from pathlib import Path

import requests

from . import config
from .duck import connect, copy_parquet, human_bytes, table_size

# Everything a query reads. works_status is what explain() reads.
BUNDLE_TABLES = (("work_metrics", "works_status") + config.DIM_TABLES
                 + config.RESOLVE_TABLES)


def installed():
    """The bundle.json in the data directory, or None when this is a local build."""
    if not config.BUNDLE_PATH.is_file():
        return None
    meta = json.loads(config.BUNDLE_PATH.read_text())
    return {
        "snapshot": meta.get("snapshot"),
        "format": meta.get("format"),
        "oam_version": meta.get("oam_version"),
        "scope_hash": meta.get("scope_hash"),
        "files": meta.get("files") or {},
    }


def rows_for(table):
    """Row count recorded for `table` when it arrived in a bundle, else None."""
    info = installed()
    if not info:
        return None
    prefix = (config.table_dir(table).relative_to(config.DATA_DIR).as_posix()) + "/"
    total, found = 0, False
    for rel, spec in info["files"].items():
        if rel.startswith(prefix) and rel.endswith(".parquet") and spec.get("rows") is not None:
            found = True
            total += spec["rows"]
    return total if found else None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(path):
    """Write `oam-<snapshot>.tar` into the directory `path`. Returns the tar path."""
    if not config.STORE_STATE_PATH.is_file():
        raise RuntimeError(
            f"no snapshot record at {config.STORE_STATE_PATH}. "
            f"export() reads a finished build; run oam.build() first."
        )
    missing = [t for t in BUNDLE_TABLES if table_size(config.table_dir(t)) == 0]
    if missing:
        raise RuntimeError(
            f"export() needs {', '.join(missing)} built. Run oam.build() first."
        )
    license_src = config.PACKAGE_DIR / "LICENSE-OpenAlex.txt"
    if not license_src.is_file():
        raise RuntimeError(f"OpenAlex licence text not found at {license_src}")

    snapshot = json.loads(config.STORE_STATE_PATH.read_text())
    out_dir = Path(path).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"oam-{snapshot.get('date')}.tar"
    staging = out_dir / f".oam-export-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    print(f"▶ Exporting {config.DATA_DIR}")
    print(f"  snapshot : {snapshot.get('date')}")
    print(f"  archive  : {dest}")
    try:
        files = {}
        written = []
        con = connect(staging / "_tmp")
        for table in BUNDLE_TABLES:
            rel = (config.table_dir(table).relative_to(config.DATA_DIR)
                   / "part-0.parquet").as_posix()
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            rows = _write_table(con, table, target)
            files[rel] = _file_record(target, rows)
            written.append((target, rel))
            print(f"  ✓ {table:<20} {rows:>15,} rows  {human_bytes(files[rel]['bytes'])}")

        rel = config.STORE_STATE_PATH.relative_to(config.DATA_DIR).as_posix()
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.STORE_STATE_PATH, target)
        files[rel] = _file_record(target, None)
        written.append((target, rel))

        rel = "LICENSE-OpenAlex.txt"
        target = staging / rel
        shutil.copy2(license_src, target)
        files[rel] = _file_record(target, None)
        written.append((target, rel))

        meta = {
            "format": config.BUNDLE_FORMAT,
            "oam_version": _version(),
            "snapshot": snapshot.get("date"),
            "record_count": snapshot.get("record_count"),
            "scope": snapshot.get("scope"),
            "scope_hash": config.scope_hash(snapshot.get("scope")),
            "files": files,
        }
        bundle_path = staging / "bundle.json"
        bundle_path.write_text(json.dumps(meta, indent=2) + "\n")
        written.append((bundle_path, "bundle.json"))

        tmp_tar = dest.with_suffix(".tar.partial")
        if tmp_tar.exists():
            tmp_tar.unlink()
        with tarfile.open(tmp_tar, "w") as archive:
            for src, arc in written:
                archive.add(src, arcname=arc)
                if src.suffix == ".parquet":
                    src.unlink()
        os.replace(tmp_tar, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"\n✓ {dest.name}  {human_bytes(dest.stat().st_size)}")
    return dest


def _write_table(con, table, target):
    """One Parquet file for `table`. A single existing file is copied as-is."""
    parts = list(config.table_dir(table).rglob("*.parquet"))
    if len(parts) == 1:
        shutil.copy2(parts[0], target)
        return _footer_rows(con, target)

    glob = config.table_glob(table)
    # hive_partitioning keeps a column that exists only as a directory name.
    # work_metrics stores publication_year that way, and queries read it.
    # updated_date is the snapshot layout, not a column anything reads.
    src = f"read_parquet('{glob}', hive_partitioning=true)"
    columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    exclude = " EXCLUDE (updated_date)" if "updated_date" in columns else ""
    order = " ORDER BY work_id" if "work_id" in columns else ""
    print(f"  · compacting {table} ({len(parts):,} files)", flush=True)
    return copy_parquet(con, f"SELECT *{exclude} FROM {src}{order}", target)


def _footer_rows(con, path):
    safe = Path(path).as_posix().replace("'", "''")
    row = con.execute(
        f"SELECT sum(num_rows) FROM parquet_file_metadata('{safe}')"
    ).fetchone()
    return int(row[0] or 0)


def _file_record(path, rows):
    return {"rows": rows, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version():
    from . import __version__
    return __version__


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(source=None, replace=False, record=None):
    """Install the published data into the data directory.

    source  None downloads from Zenodo. Otherwise a .tar path, a directory
            containing one, or an https URL.
    record  the Zenodo record to download from. None uses config.ZENODO_RECORD,
            the all-versions record, which is always the latest snapshot. The
            record id of one version pins that snapshot.
    replace wipe a data directory built from a different snapshot first.
            Refused without this, so a download cannot mix two releases.
    """
    if source is not None and record is not None:
        raise ValueError("pass source or record, not both")
    if source is None:
        src = _from_zenodo(record, replace)
        if src is None:
            print(f"✓ data already installed at {config.DATA_DIR}")
            return installed()
    else:
        src = _locate(source)
    path = _materialize(src)
    meta = _read_tar_meta(path)
    _check_format(meta, path)
    if not replace and _archive_already_present(meta):
        print(f"✓ data already installed at {config.DATA_DIR}")
        return installed()

    incoming = meta.get("snapshot")
    existing = _existing_snapshot()
    if existing and existing != incoming and not replace:
        raise RuntimeError(
            f"{config.DATA_DIR} holds snapshot {existing}, and this bundle is "
            f"{incoming}. Pass replace=True to replace it."
        )
    if existing and existing != incoming:
        _wipe_outputs()
    _install(path, meta)
    print(f"✓ installed snapshot {incoming} into {config.DATA_DIR}")
    return installed()


def _satisfied():
    """True when the recorded files are already present at the recorded sizes."""
    info = installed()
    if not info or not info["files"]:
        return False
    for rel, spec in info["files"].items():
        path = config.DATA_DIR / rel
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return False
    return True


def _archive_already_present(meta):
    """True when this exact archive is already what the data directory holds."""
    info = installed()
    if not info or info.get("snapshot") != meta.get("snapshot"):
        return False
    # snapshot.json is what queries trust. A hand-edited copy must not be
    # treated as the bundle it no longer describes.
    if _existing_snapshot() != meta.get("snapshot"):
        return False
    recorded = info.get("files") or {}
    if set(recorded) != set(meta["files"]):
        return False
    for rel, spec in meta["files"].items():
        path = config.DATA_DIR / rel
        if recorded[rel].get("sha256") != spec.get("sha256"):
            return False
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return False
    return True


def _existing_snapshot():
    if config.STORE_STATE_PATH.is_file():
        return json.loads(config.STORE_STATE_PATH.read_text()).get("date")
    info = installed()
    return info["snapshot"] if info else None


def _from_zenodo(record, replace):
    """The URL of the Zenodo archive to fetch, or None when it is already installed.

    The snapshot is read from the file name before anything is fetched, so a
    data directory holding another snapshot is refused without first pulling
    12 GiB. When Zenodo cannot be reached, complete installed data is kept.
    """
    try:
        url, name = _zenodo_file(record)
    except requests.RequestException:
        if not replace and _satisfied():
            print("  Zenodo could not be reached, so a newer snapshot was not looked for.")
            return None
        raise
    offered = _snapshot_of(name)
    existing = _existing_snapshot()
    if not replace and existing == offered and _satisfied():
        return None
    if existing and offered and existing != offered and not replace:
        raise RuntimeError(
            f"{config.DATA_DIR} holds snapshot {existing}, and Zenodo offers "
            f"{offered}. Pass replace=True to switch to {offered}, or record= the "
            f"id of the {existing} version (listed on the Zenodo page) to keep it."
        )
    return url


def _snapshot_of(name):
    """oam-2026-10-14.tar -> 2026-10-14, the name export() gives an archive."""
    stem = Path(name).name
    if stem.startswith("oam-") and stem.endswith(".tar"):
        return stem[len("oam-"):-len(".tar")]
    return None


def _locate(source):
    """A local .tar path, or an https URL, for the one archive to install."""
    as_path = _as_path(source)
    if as_path is not None and as_path.is_dir():
        matches = sorted(as_path.glob("oam-*.tar"))
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one oam-*.tar in {as_path}, found {len(matches)}"
            )
        return matches[0]
    if as_path is not None:
        if not as_path.is_file():
            raise RuntimeError(f"no such file: {as_path}")
        return as_path
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        return source
    raise RuntimeError("source must be a .tar file, a directory containing one, or an https URL")


def _as_path(source):
    if isinstance(source, Path):
        return source.expanduser()
    if isinstance(source, str) and not source.startswith(("http://", "https://")):
        return Path(source).expanduser()
    return None


def _zenodo_file(record=None):
    """(download URL, file name) of the one oam-*.tar in a Zenodo record.

    Zenodo redirects an all-versions record to its newest version and serves a
    version's own record as it is, so the same request covers both.
    """
    record = record or config.ZENODO_RECORD
    if not record:
        raise RuntimeError(
            "No Zenodo record is configured (oam.config.ZENODO_RECORD). "
            "Pass record= a Zenodo record id, or source= a local .tar file or an https URL."
        )
    resp = requests.get(f"{config.ZENODO_API}{record}", timeout=60)
    resp.raise_for_status()
    matches = []
    for entry in resp.json().get("files") or []:
        key = str(entry.get("key") or entry.get("filename") or "")
        if not Path(key).name.startswith("oam-") or not key.endswith(".tar"):
            continue
        links = entry.get("links") or {}
        url = links.get("self") or links.get("content")
        if url:
            matches.append((url, key))
    if len(matches) != 1:
        raise RuntimeError(
            f"Zenodo record {record} does not have exactly one oam-*.tar "
            f"({len(matches)} found)."
        )
    return matches[0]


def _materialize(src):
    """A local path, downloading an URL into the data directory's cache first.

    The cache name is fixed: Zenodo's download link ends in ``content``.
    """
    if not isinstance(src, str) or not src.startswith(("http://", "https://")):
        return Path(src)
    dest = config.DATA_DIR / "_downloads" / "oam.tar"
    _download_file(src, dest)
    return dest


def _download_file(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as resp:
        if resp.status_code == 416 and have:
            print(f"  already downloaded {dest.name}")
            return
        if resp.status_code == 200:
            have = 0
        resp.raise_for_status()
        total = _total_size(resp, have)
        mode = "ab" if resp.status_code == 206 else "wb"
        written = have
        last = 0.0
        print(f"▶ Downloading {dest.name}")
        with dest.open(mode) as fh:
            for chunk in resp.iter_content(1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
                now = time.time()
                if now - last >= 2:
                    last = now
                    _progress(written, total)
        _progress(written, total)
        print()


def _total_size(resp, have):
    header = resp.headers.get("Content-Range")
    if header and "/" in header and header.rsplit("/", 1)[-1].isdigit():
        return int(header.rsplit("/", 1)[-1])
    length = resp.headers.get("Content-Length")
    if length and length.isdigit():
        return have + int(length) if resp.status_code == 206 else int(length)
    return None


def _progress(written, total):
    if total:
        print(f"\r  {written / total * 100:5.1f}%  {human_bytes(written)}"
              f" / {human_bytes(total)}", end="", flush=True)
    else:
        print(f"\r  {human_bytes(written)}", end="", flush=True)


def _read_tar_meta(path):
    with tarfile.open(path, "r") as archive:
        try:
            member = archive.getmember("bundle.json")
        except KeyError:
            raise RuntimeError(f"{path} has no bundle.json; it is not an OAM bundle")
        return json.load(archive.extractfile(member))


def _check_format(meta, path):
    if meta.get("format") != config.BUNDLE_FORMAT:
        raise RuntimeError(
            f"{Path(path).name} is bundle format {meta.get('format')}; "
            f"this OAM reads format {config.BUNDLE_FORMAT}."
        )


def _install(path, meta):
    staging = config.DATA_DIR / f"_incoming-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _safe_extract(path, staging)
        _verify(staging, meta)
        _clear_replaced_tables(meta)
        for rel in meta["files"]:
            src = staging / rel
            if not src.exists():
                continue
            dest = config.DATA_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
        _write_bundle(meta)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # A file the caller pointed at is kept. This only removes the cache copy.
    cached = config.DATA_DIR / "_downloads" / Path(path).name
    if cached.resolve() == Path(path).resolve() and cached.exists():
        cached.unlink()


def _safe_extract(path, dest):
    """Extract, refusing links and paths that would leave `dest`."""
    dest = dest.resolve()
    with tarfile.open(path, "r") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"refusing a link in the archive: {member.name}")
            rel = Path(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError(f"refusing an unsafe path in the archive: {member.name}")
            target = (dest / rel).resolve()
            if dest != target and dest not in target.parents:
                raise RuntimeError(f"refusing an unsafe path in the archive: {member.name}")
            if member.name == "bundle.json":
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                shutil.copyfileobj(extracted, out)


def _verify(root, meta):
    for rel, spec in meta["files"].items():
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"the archive is missing {rel}")
        size = path.stat().st_size
        if size != spec["bytes"]:
            raise RuntimeError(f"{rel} is {size} bytes, the archive says {spec['bytes']}")
        digest = _sha256(path)
        if digest != spec["sha256"]:
            raise RuntimeError(f"checksum mismatch for {rel}")


def _wipe_outputs():
    """Drop every built or previously installed table. The snapshot does not match."""
    for name in ("store", "metrics", "resolve", "snapshot"):
        shutil.rmtree(config.DATA_DIR / name, ignore_errors=True)
    if config.BUNDLE_PATH.exists():
        config.BUNDLE_PATH.unlink()


def _clear_replaced_tables(meta):
    """Remove a table's old files before the archive's single file takes its place.

    A local build keeps work_metrics as thousands of part files. Extracting one
    part-0.parquet beside them would make queries read both.
    """
    dirs = set()
    for rel in meta["files"]:
        if rel.endswith(".parquet"):
            dirs.add(str(Path(rel).parent))
    for rel in dirs:
        shutil.rmtree(config.DATA_DIR / rel, ignore_errors=True)


def _write_bundle(meta):
    config.BUNDLE_PATH.write_text(json.dumps(meta, indent=2) + "\n")
