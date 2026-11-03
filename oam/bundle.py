"""Share a finished build, or install someone else's, without rebuilding it.

One archive holds every table a query reads: the scores, the scope status,
the dimension tables and the lookup tables resolve() uses. The citation
links travel beside it, in ``edges-<snapshot>.tar``, and only
``download(edges=True)`` installs them. citation_counts and works_scope stay
behind either way: a download cannot rebuild the scores.

export() compacts a table that is still thousands of part files into one
Parquet file sorted by work_id, which is how get_metrics and explain look
rows up. A table that is already one file is copied unchanged: the resolve
tables are sorted on their lookup key, and re-sorting them would make every
query read the whole file. The citation links have no work_id column, so
they keep the order the build wrote.

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
        "analysis": meta.get("analysis"),
        "files": meta.get("files") or {},
        "edges": meta.get("edges"),
    }


def citation_links():
    """The installed citation-link manifest, or None when its files are absent."""
    info = installed()
    edges = (info or {}).get("edges")
    if not edges or not edges.get("files"):
        return None
    for rel, spec in edges["files"].items():
        path = config.DATA_DIR / rel
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return None
    return edges


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

def export(path, edges=False):
    """Write `oam-<snapshot>.tar` into the directory `path`. Returns the tar path.

    edges  also write `edges-<snapshot>.tar` beside it, the citation links.
           ``export_edges`` writes that file on its own.
    """
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

        from .metrics import recorded_analysis
        meta = {
            "format": config.BUNDLE_FORMAT,
            "oam_version": _version(),
            "snapshot": snapshot.get("date"),
            "record_count": snapshot.get("record_count"),
            "scope": snapshot.get("scope"),
            "scope_hash": config.scope_hash(snapshot.get("scope")),
            "analysis": recorded_analysis(),
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
    if edges:
        export_edges(path)
    return dest


def export_edges(path):
    """Write `edges-<snapshot>.tar` into the directory `path`. Returns the tar path.

    One Parquet file of the citation links, in the order the build wrote them,
    plus ``edges.json``. The main archive is left untouched.
    """
    if not config.STORE_STATE_PATH.is_file():
        raise RuntimeError(
            f"no snapshot record at {config.STORE_STATE_PATH}. "
            f"export_edges() reads a finished build; run oam.build() first."
        )
    if table_size(config.table_dir("edges")) == 0:
        raise RuntimeError(
            "export_edges() needs the citation links. Run oam.build() first."
        )

    snapshot = json.loads(config.STORE_STATE_PATH.read_text())
    out_dir = Path(path).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"edges-{snapshot.get('date')}.tar"
    staging = out_dir / f".oam-export-edges-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    print(f"▶ Exporting citation links from {config.table_dir('edges')}")
    print(f"  snapshot : {snapshot.get('date')}")
    print(f"  archive  : {dest}")
    try:
        con = connect(staging / "_tmp")
        rel = (config.table_dir("edges").relative_to(config.DATA_DIR)
               / "part-0.parquet").as_posix()
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = _write_table(con, "edges", target)
        files = {rel: _file_record(target, rows)}
        print(f"  ✓ {'edges':<20} {rows:>15,} rows  {human_bytes(files[rel]['bytes'])}")

        meta = {
            "format": config.EDGES_FORMAT,
            "snapshot": snapshot.get("date"),
            "scope_hash": config.scope_hash(snapshot.get("scope")),
            "oam_version": _version(),
            "files": files,
        }
        manifest = staging / "edges.json"
        manifest.write_text(json.dumps(meta, indent=2) + "\n")

        tmp_tar = dest.with_suffix(".tar.partial")
        if tmp_tar.exists():
            tmp_tar.unlink()
        with tarfile.open(tmp_tar, "w") as archive:
            archive.add(target, arcname=rel)
            target.unlink()
            # Last, so a range request can read it without the whole archive.
            archive.add(manifest, arcname="edges.json")
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

def download(source=None, replace=False, record=None, edges=False):
    """Install the published data into the data directory. Returns its snapshot date.

    source  None downloads from Zenodo. Otherwise a .tar path, a directory
            containing one, or an https URL.
    record  the Zenodo record to download from. None uses config.ZENODO_RECORD,
            the all-versions record, which is always the latest snapshot. The
            record id of one version pins that snapshot.
    replace wipe a data directory built from a different snapshot first.
            Refused without this, so a download cannot mix two releases.
    edges   also install the citation links. From Zenodo that is the
            ``edges-<snapshot>.tar`` on the same record. From a local source it
            is the one ``edges-*.tar`` beside the main archive. An https URL
            cannot carry them. 9.2 GiB on top of the main archive for the
            2026-10-14 snapshot.
    """
    if source is not None and record is not None:
        raise ValueError("pass source or record, not both")
    if edges and _is_url(source):
        raise RuntimeError(
            "edges=True installs the citation links from Zenodo, or from a "
            "local directory next to the main archive. An https URL is only "
            "the main archive."
        )
    snapshot = _download_archive(source, replace, record)
    if edges:
        _install_edges(source, record)
    return snapshot


def _download_archive(source, replace, record):
    """Install the main archive. Returns the installed snapshot date."""
    if source is None:
        src = _from_zenodo(record, replace)
        if src is None:
            print(f"✓ data already installed at {config.DATA_DIR}")
            return installed()["snapshot"]
    else:
        src = _locate(source)
    path = _materialize(src)
    meta = _read_tar_meta(path)
    _check_format(meta, path)
    if not replace and _archive_already_present(meta):
        print(f"✓ data already installed at {config.DATA_DIR}")
        return installed()["snapshot"]

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
    return installed()["snapshot"]


def _is_url(source):
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def _install_edges(source, record):
    """Install the citation links that match the archive already in place."""
    if source is None:
        _install_edges_from_zenodo(record)
        return
    path = _locate_edges(source)
    meta = _read_tar_meta(path, "edges.json")
    _check_edges(meta, path)
    if _edges_satisfied(meta):
        print(f"✓ citation links already installed at {config.DATA_DIR}")
        return
    print(f"▶ Installing citation links from {Path(path).name}")
    _install(path, meta, manifest="edges.json", record=_record_edges)
    _report_edges(meta)


def _install_edges_from_zenodo(record):
    try:
        url, name, size, record_id = _zenodo_file(record, prefix="edges-")
        meta = _remote_meta(url, size, member="edges.json")
    except (requests.ConnectionError, requests.Timeout):
        if _edges_satisfied():
            print("  Zenodo could not be reached, so the installed citation links were kept.")
            return
        raise
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "error"
        asked = record or config.ZENODO_RECORD
        raise RuntimeError(f"Zenodo record {asked} returned HTTP {code}.") from exc
    _check_edges(meta, name)
    if _edges_satisfied(meta):
        print(f"✓ citation links already installed at {config.DATA_DIR}")
        return
    print(f"▶ Downloading {name} "
          f"(record {record_id}, snapshot {meta.get('snapshot')}, {human_bytes(size)})")
    path = _materialize(url)
    meta = _read_tar_meta(path, "edges.json")
    _check_edges(meta, path)
    _install(path, meta, manifest="edges.json", record=_record_edges)
    _report_edges(meta)


def _report_edges(meta):
    rows = sum(spec.get("rows") or 0 for spec in meta["files"].values())
    print(f"✓ citation links installed into {config.DATA_DIR} ({rows:,} rows)")


def _locate_edges(source):
    """The one edges-*.tar next to a local main archive."""
    as_path = _as_path(source)
    if as_path is None:
        raise RuntimeError("citation links need a local file or directory, or Zenodo")
    directory = as_path if as_path.is_dir() else as_path.parent
    matches = sorted(directory.glob("edges-*.tar"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one edges-*.tar in {directory}, found {len(matches)}"
        )
    return matches[0]


def _check_edges(meta, path):
    """Refuse links this OAM cannot read, or links from another snapshot or scope."""
    name = Path(path).name
    if meta.get("format") != config.EDGES_FORMAT:
        raise RuntimeError(
            f"{name} is citation-link format {meta.get('format')}; "
            f"this OAM reads format {config.EDGES_FORMAT}."
        )
    info = installed()
    if not info:
        raise RuntimeError(
            f"citation links need the main archive installed in {config.DATA_DIR} first."
        )
    if meta.get("snapshot") != info.get("snapshot"):
        raise RuntimeError(
            f"{name} is snapshot {meta.get('snapshot')}, and {config.DATA_DIR} holds "
            f"{info.get('snapshot')}. The citation links have to be the same snapshot."
        )
    if meta.get("scope_hash") != info.get("scope_hash"):
        raise RuntimeError(
            f"{name} was built with a different scope than the data in {config.DATA_DIR}."
        )


def _edges_satisfied(meta=None):
    """True when the recorded citation links are on disk, and match `meta` when given."""
    info = installed()
    edges = (info or {}).get("edges")
    if not edges or edges.get("format") != config.EDGES_FORMAT or not edges.get("files"):
        return False
    recorded = edges["files"]
    if meta is not None:
        if edges.get("snapshot") != meta.get("snapshot") or edges.get("scope_hash") != meta.get("scope_hash"):
            return False
        offered = meta.get("files") or {}
        if set(recorded) != set(offered):
            return False
        for rel, spec in offered.items():
            if recorded[rel].get("sha256") != spec.get("sha256"):
                return False
    for rel, spec in recorded.items():
        path = config.DATA_DIR / rel
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return False
    return True


def _record_edges(meta):
    """Keep the citation-link manifest inside the installed bundle.json."""
    if not config.BUNDLE_PATH.is_file():
        raise RuntimeError(
            f"citation links need the main archive installed in {config.DATA_DIR} first."
        )
    current = json.loads(config.BUNDLE_PATH.read_text())
    current["edges"] = meta
    config.BUNDLE_PATH.write_text(json.dumps(current, indent=2) + "\n")


def _satisfied():
    """True when the recorded files are already present at the recorded sizes."""
    info = installed()
    if not info or info.get("format") != config.BUNDLE_FORMAT or not info["files"]:
        return False
    for rel, spec in info["files"].items():
        path = config.DATA_DIR / rel
        if not path.is_file() or path.stat().st_size != spec.get("bytes"):
            return False
    return True


def _archive_already_present(meta):
    """True when this exact archive is already what the data directory holds."""
    info = installed()
    if not info or info.get("format") != config.BUNDLE_FORMAT:
        return False
    if info.get("snapshot") != meta.get("snapshot"):
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


def _remote_meta(url, size, member="bundle.json"):
    """A manifest from the end of a remote tar, without downloading it.

    The member is written last, and on the published archive it sits inside the
    final megabyte. The server has to honour the range request.
    """
    if not size:
        raise RuntimeError(f"the archive at {url} has no size")
    window = min(size, 1024 * 1024)
    start = size - window
    resp = requests.get(url, headers={"Range": f"bytes={start}-"}, timeout=(30, 60))
    resp.raise_for_status()
    if resp.status_code != 206:
        raise RuntimeError(f"the archive server did not honour a range request for {url}")
    data = resp.content
    idx = data.rfind(b'{\n  "format"')
    if idx < 0:
        raise RuntimeError(f"could not read {member} from {url}")
    meta, _ = json.JSONDecoder().raw_decode(data[idx:].decode("utf-8"))
    return meta


def _from_zenodo(record, replace):
    """The URL to fetch, or None when this exact bundle is already installed.

    Identity is the checksum of each file, read from the remote bundle.json
    before the transfer. A connection error keeps the installed data. A missing
    record is an error. A different snapshot is refused until replace=True.
    """
    try:
        url, name, size, record_id = _zenodo_file(record)
        meta = _remote_meta(url, size)
    except (requests.ConnectionError, requests.Timeout):
        if not replace and _satisfied():
            print("  Zenodo could not be reached, so a newer snapshot was not looked for.")
            return None
        raise
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "error"
        asked = record or config.ZENODO_RECORD
        raise RuntimeError(f"Zenodo record {asked} returned HTTP {code}.") from exc
    _check_format(meta, name)
    if not replace and _archive_already_present(meta):
        return None
    incoming = meta.get("snapshot")
    existing = _existing_snapshot()
    if existing and incoming and existing != incoming and not replace:
        raise RuntimeError(
            f"{config.DATA_DIR} holds snapshot {existing}, and Zenodo offers "
            f"{incoming}. Pass replace=True to switch to {incoming}, or record= the "
            f"id of the {existing} version (listed on the Zenodo page) to keep it."
        )
    print(f"▶ Downloading {name} "
          f"(record {record_id}, snapshot {incoming}, {human_bytes(size)})")
    return url


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


def _zenodo_file(record=None, prefix="oam-"):
    """URL, filename, size and resolved record id of the one ``prefix*.tar`` on a record.

    Zenodo redirects an all-versions record to its newest version and serves a
    version's own record as it is, so the same request covers both. ``oam-`` is
    the main archive. ``edges-`` is the optional citation links, and a record
    without that file is an error only when they were asked for.
    """
    record = record or config.ZENODO_RECORD
    if not record:
        raise RuntimeError(
            "No Zenodo record is configured (oam.config.ZENODO_RECORD). "
            "Pass record= a Zenodo record id, or source= a local .tar file or an https URL."
        )
    resp = requests.get(f"{config.ZENODO_API}{record}", timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    matches = []
    for entry in payload.get("files") or []:
        key = str(entry.get("key") or entry.get("filename") or "")
        if not Path(key).name.startswith(prefix) or not key.endswith(".tar"):
            continue
        links = entry.get("links") or {}
        url = links.get("self") or links.get("content")
        if url:
            matches.append((url, Path(key).name, int(entry.get("size") or 0)))
    if len(matches) != 1:
        kind = f"{prefix}*.tar"
        if prefix == "edges-" and len(matches) == 0:
            raise RuntimeError(
                f"Zenodo record {record} has no citation links (no {kind}). "
                f"This version was published without them."
            )
        raise RuntimeError(
            f"Zenodo record {record} does not have exactly one {kind} "
            f"({len(matches)} found)."
        )
    return matches[0] + (payload.get("id") or record,)


def _materialize(src):
    """A local path, downloading an URL into the data directory's cache first.

    The cache file is named after the URL, so a partial download is only resumed
    from the same file, never from another Zenodo version. Leftovers are removed.
    """
    if not isinstance(src, str) or not src.startswith(("http://", "https://")):
        return Path(src)
    cache = config.DATA_DIR / "_downloads"
    dest = cache / f"{hashlib.sha256(src.encode()).hexdigest()[:16]}.tar"
    for old in cache.glob("*.tar"):
        if old != dest:
            old.unlink()
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


def _read_tar_meta(path, member="bundle.json"):
    with tarfile.open(path, "r") as archive:
        try:
            info = archive.getmember(member)
        except KeyError:
            raise RuntimeError(f"{path} has no {member}; it is not an OAM archive")
        return json.load(archive.extractfile(info))


def _check_format(meta, path):
    if meta.get("format") != config.BUNDLE_FORMAT:
        raise RuntimeError(
            f"{Path(path).name} is bundle format {meta.get('format')}; "
            f"this OAM reads format {config.BUNDLE_FORMAT}. "
            f"Upgrade OAM, or pass record= the id of a version this OAM can read."
        )


def _install(path, meta, manifest="bundle.json", record=None):
    staging = config.DATA_DIR / f"_incoming-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"▶ Unpacking and checking {len(meta['files'])} files", flush=True)
    try:
        _safe_extract(path, staging, manifest)
        _verify(staging, meta)
        _clear_replaced_tables(meta)
        for rel in meta["files"]:
            src = staging / rel
            if not src.exists():
                continue
            dest = config.DATA_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
        if record is None:
            _write_bundle(meta)
        else:
            record(meta)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # A file the caller pointed at is kept. This only removes the cache copy.
    cached = config.DATA_DIR / "_downloads" / Path(path).name
    if cached.resolve() == Path(path).resolve() and cached.exists():
        cached.unlink()


def _safe_extract(path, dest, manifest="bundle.json"):
    """Extract, refusing links and paths that would leave `dest`.

    The manifest stays out of the data directory. ``download`` records it in
    ``bundle.json`` instead.
    """
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
            if member.name == manifest:
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
    """Write bundle.json, keeping citation links that still match this snapshot.

    A main archive does not carry the links. An entry already on disk is kept
    when it is the same snapshot and the same scope, and dropped otherwise.
    """
    meta = {key: value for key, value in meta.items() if key != "edges"}
    previous = None
    if config.BUNDLE_PATH.is_file():
        previous = json.loads(config.BUNDLE_PATH.read_text())
    edges = (previous or {}).get("edges")
    same = (
        edges
        and edges.get("snapshot") == meta.get("snapshot")
        and edges.get("scope_hash") == meta.get("scope_hash")
    )
    if same:
        meta["edges"] = edges
    elif edges:
        shutil.rmtree(config.table_dir("edges"), ignore_errors=True)
    config.BUNDLE_PATH.write_text(json.dumps(meta, indent=2) + "\n")
