"""Share a finished build, or install someone else's, without rebuilding it.

Two archives, so a Zenodo record stays well under its file and size limits:

  lite       work_metrics, works_status and the dimension tables. Enough to
             explain and score a list of OpenAlex ids. About the size of those
             tables, a few gigabytes.
  resolve    the lookup tables resolve() reads. Together with lite this is the
             full query bundle; the build-only tables (edges, citation_counts,
             works_scope) are never shipped.

export() compacts a table that is still thousands of part files into one
Parquet file sorted by work_id, which is how get_metrics and explain look
rows up. A table that is already one file is copied unchanged: the resolve
tables are sorted on their lookup key, and re-sorting them would make every
query read the whole file.

download() installs an archive into the data directory. With no source it
uses the Zenodo record in config.ZENODO_RECORD. source may also be a local
.tar, a directory of them, or an https URL.
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

# Scoring and naming. works_status is what explain() reads; the dimension
# tables are small enough that naming an id should not require the full bundle.
LITE_TABLES = ("work_metrics", "works_status") + config.DIM_TABLES
RESOLVE_EXPORT_TABLES = config.RESOLVE_TABLES

_TIERS = {
    "lite": LITE_TABLES,
    "resolve": RESOLVE_EXPORT_TABLES,
}


def installed():
    """The bundle.json in the data directory, or None when this is a local build."""
    if not config.BUNDLE_PATH.is_file():
        return None
    meta = json.loads(config.BUNDLE_PATH.read_text())
    return {
        "tier": meta.get("tier"),
        "snapshot": meta.get("snapshot"),
        "format": meta.get("format"),
        "oam_version": meta.get("oam_version"),
        "scope_hash": meta.get("scope_hash"),
        "installed": meta.get("installed") or ([meta["tier"]] if meta.get("tier") else []),
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

def export(path, tier="lite"):
    """Write `oam-<snapshot>-<tier>.tar` into the directory `path`.

    tier "lite" is the scoring bundle. tier "resolve" is the lookup bundle.
    Publish both; download("full") installs the pair. Returns the tar path.
    """
    if tier not in _TIERS:
        raise ValueError(f"tier must be 'lite' or 'resolve', not {tier!r}")
    if not config.STORE_STATE_PATH.is_file():
        raise RuntimeError(
            f"no snapshot record at {config.STORE_STATE_PATH}. "
            f"export() reads a finished build; run oam.build() first."
        )
    missing = [t for t in _TIERS[tier] if table_size(config.table_dir(t)) == 0]
    if missing:
        raise RuntimeError(
            f"export({tier!r}) needs {', '.join(missing)} built. Run oam.build() first."
        )
    license_src = config.REPO_DIR / "docs" / "openalex-LICENSE.txt"
    if not license_src.is_file():
        raise RuntimeError(f"OpenAlex licence text not found at {license_src}")

    snapshot = json.loads(config.STORE_STATE_PATH.read_text())
    out_dir = Path(path).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"oam-{snapshot.get('date')}-{tier}.tar"
    staging = out_dir / f".oam-export-{tier}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    print(f"▶ Exporting {tier} from {config.DATA_DIR}")
    print(f"  snapshot : {snapshot.get('date')}")
    print(f"  archive  : {dest}")
    try:
        files = {}
        written = []
        con = connect(staging / "_tmp")
        for table in _TIERS[tier]:
            rel = (config.table_dir(table).relative_to(config.DATA_DIR)
                   / "part-0.parquet").as_posix()
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            rows = _write_table(con, table, target)
            files[rel] = _file_record(target, rows)
            written.append((target, rel))
            print(f"  ✓ {table:<20} {rows:>15,} rows  {human_bytes(files[rel]['bytes'])}")

        if tier == "lite":
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
            "tier": tier,
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

def download(tier="lite", source=None, replace=False):
    """Install a published bundle into the data directory.

    tier    "lite" scores a list of ids. "full" adds resolve(). "resolve"
            installs only the lookup archive, which is only useful on top of lite.
    source  None uses config.ZENODO_RECORD. Otherwise a .tar path, a directory
            holding the archives, or an https URL of one archive.
    replace wipe a data directory built from a different snapshot first.
            Refused without this, so a download cannot mix two releases.
    """
    needed = _needed(tier)
    # An explicit file or URL can be a different snapshot, so it is never
    # waved through on the strength of whatever is already installed. Only the
    # Zenodo default, which is the one release this version knows, can be.
    if source is None and not replace and _satisfied(tier):
        print(f"✓ {tier} data already installed at {config.DATA_DIR}")
        return installed()
    if source is None and not replace and tier == "full" and _satisfied("lite"):
        needed = ["resolve"]

    staged = _locate(needed, source)
    metas = []
    for name, src in staged:
        path = _materialize(src, name)
        meta = _read_tar_meta(path)
        _check_format(meta, path)
        if meta.get("tier") != name:
            raise RuntimeError(
                f"{path} is a {meta.get('tier')} archive, expected {name}."
            )
        metas.append((name, path, meta))
    dates = {m["snapshot"] for _, _, m in metas}
    if len(dates) != 1:
        raise RuntimeError(
            f"these archives are from different snapshots: {sorted(dates)}"
        )
    if not replace and all(_archive_already_present(meta) for _, _, meta in metas):
        print(f"✓ {tier} data already installed at {config.DATA_DIR}")
        return installed()

    incoming = dates.pop()
    existing = _existing_snapshot()
    if existing and existing != incoming and not replace:
        raise RuntimeError(
            f"{config.DATA_DIR} holds snapshot {existing}, and this bundle is "
            f"{incoming}. Pass replace=True to replace it."
        )
    # Once, before the first archive lands. Wiping inside the loop would
    # delete the lite tables while installing the resolve archive.
    if existing and existing != incoming:
        _wipe_outputs()
    for name, path, meta in metas:
        _install_one(path, meta)
        print(f"✓ installed {name} (snapshot {incoming}) into {config.DATA_DIR}")
    return installed()


def _needed(tier):
    if tier == "lite":
        return ["lite"]
    if tier == "resolve":
        return ["resolve"]
    if tier == "full":
        return ["lite", "resolve"]
    raise ValueError("tier must be 'lite', 'resolve' or 'full'")


def _satisfied(tier):
    """True when this tier's files are already present at the recorded sizes."""
    info = installed()
    if not info:
        return False
    have = set(info["installed"])
    wanted = {"lite", "resolve"} if tier == "full" else {tier}
    if not wanted <= have:
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
    if meta.get("tier") not in set(info.get("installed") or []):
        return False
    for rel, spec in meta["files"].items():
        recorded = (info.get("files") or {}).get(rel)
        path = config.DATA_DIR / rel
        if not recorded or recorded.get("sha256") != spec.get("sha256"):
            return False
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return False
    return True


def _existing_snapshot():
    if config.STORE_STATE_PATH.is_file():
        return json.loads(config.STORE_STATE_PATH.read_text()).get("date")
    info = installed()
    return info["snapshot"] if info else None


def _locate(needed, source):
    """(tier name, path-or-url) for each archive still to install."""
    if source is None:
        return [(name, _zenodo_url(name)) for name in needed]

    as_path = _as_path(source)
    if as_path is not None and as_path.is_dir():
        found = []
        for name in needed:
            matches = sorted(as_path.glob(f"oam-*-{name}.tar"))
            if len(matches) != 1:
                raise RuntimeError(
                    f"expected one oam-*-{name}.tar in {as_path}, found {len(matches)}"
                )
            found.append((name, matches[0]))
        return found

    if len(needed) != 1:
        raise RuntimeError(
            "download('full') needs both archives. Pass a directory that contains "
            "them, or source=None to take them from Zenodo."
        )
    if as_path is not None:
        if not as_path.is_file():
            raise RuntimeError(f"no such file: {as_path}")
        return [(needed[0], as_path)]
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        return [(needed[0], source)]
    raise RuntimeError(
        "source must be a .tar file, a directory of them, or an https URL"
    )


def _as_path(source):
    if isinstance(source, Path):
        return source.expanduser()
    if isinstance(source, str) and not source.startswith(("http://", "https://")):
        return Path(source).expanduser()
    return None


def _zenodo_url(tier_name):
    record = config.ZENODO_RECORD
    if not record:
        raise RuntimeError(
            "No Zenodo record is configured yet (oam.config.ZENODO_RECORD). "
            "Pass source= a local .tar file or an https URL."
        )
    resp = requests.get(f"{config.ZENODO_API}{record}", timeout=60)
    resp.raise_for_status()
    suffix = f"-{tier_name}.tar"
    matches = []
    for entry in resp.json().get("files") or []:
        key = entry.get("key") or entry.get("filename") or ""
        if not str(key).endswith(suffix):
            continue
        links = entry.get("links") or {}
        url = links.get("self") or links.get("content")
        if url:
            matches.append(url)
    if len(matches) != 1:
        raise RuntimeError(
            f"Zenodo record {record} does not have exactly one file ending in {suffix} "
            f"({len(matches)} found)."
        )
    return matches[0]


def _materialize(src, tier_name):
    """A local path, downloading an URL into the data directory's cache first.

    The cache name is the tier, not the URL's last segment: Zenodo's download
    links often both end in ``content`` and would overwrite each other.
    """
    if not isinstance(src, str) or not src.startswith(("http://", "https://")):
        return Path(src)
    dest = config.DATA_DIR / "_downloads" / f"oam-{tier_name}.tar"
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


def _install_one(path, meta):
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
        _merge_bundle(meta)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # The downloaded tar has done its job. A file the caller pointed at is kept
    # by _caller_files; this only removes the cache copy, which lives here.
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


def _merge_bundle(meta):
    current = {}
    if config.BUNDLE_PATH.is_file():
        current = json.loads(config.BUNDLE_PATH.read_text())
    if current.get("snapshot") not in (None, meta.get("snapshot")):
        current = {}
    files = dict(current.get("files") or {})
    files.update(meta["files"])
    installed_tiers = set(current.get("installed") or [])
    if current.get("tier") in ("lite", "resolve"):
        installed_tiers.add(current["tier"])
    installed_tiers.add(meta["tier"])
    merged = {
        "format": meta["format"],
        "oam_version": meta.get("oam_version"),
        "snapshot": meta.get("snapshot"),
        "record_count": meta.get("record_count") or current.get("record_count"),
        "scope": meta.get("scope") or current.get("scope"),
        "scope_hash": meta.get("scope_hash") or current.get("scope_hash"),
        "tier": "full" if {"lite", "resolve"} <= installed_tiers else meta["tier"],
        "installed": sorted(installed_tiers),
        "files": files,
    }
    config.BUNDLE_PATH.write_text(json.dumps(merged, indent=2) + "\n")
