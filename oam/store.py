"""Build step 1 of 3 — the store, read straight off the OpenAlex snapshot.

Streams every works part file listed in the snapshot manifest from S3, reading only
the columns OAM needs (~10% of each file), and reduces each one into three Parquet
tables under data/store/:

  works_status/  one row per work in OpenAlex, with one flag per scope criterion
  works_scope/   one row per in-scope work, with its bin dimensions
  edges/         one row per citation made by an in-scope work

The same pass also writes the resolve warehouse rows — every work's attributes, and
its institutions, authors, funders, DOI and ORCIDs — unsorted under
data/store/_resolve_raw/, for step 3 to sort and publish.

Each source file is processed independently and committed atomically, so the run is
fully resumable: rerun after any interruption and it continues where it stopped.

The ~2,446 files transfer about 67 GiB between them and roughly 1% of the time is spent
computing, so the runtime is however long that takes on the link available — about
1h40m at 100 Mbit/s. Run steps 2 and 3 afterwards.

The reading is PyArrow's (oam/arrow.py) and the computing is DuckDB's. The split is
there because DuckDB cannot project a single leaf out of a LIST(STRUCT) and so would
fetch all of authorships, which is most of what the build transfers; everything after
the read is ordinary SQL over the Arrow table.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

from . import config
from .duck import (FileCheckpoint, clean_stale_tmp, connect, copy_parquet,
                   human_bytes, human_time, table_size)

MAX_ATTEMPTS = 4  # the last attempt downloads the file and reads it locally


def _arrow():
    """PyArrow, imported on use so a download-only install does not need it."""
    try:
        from . import arrow
    except ImportError as exc:
        raise RuntimeError(
            "Building the store reads the OpenAlex snapshot with PyArrow. "
            'Install it with: pip install "openalex-metrics[build]"'
        ) from exc
    return arrow


def _state_dir():
    """Checkpoint directory. Looked up live so set_data_dir() is honoured."""
    return config.STORE_DIR / "_state"


def _tmp_dir():
    return config.STORE_DIR / "_tmp"


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
# One SELECT over each source file yields everything the three tables need: the bin
# dimensions, the reference list, and one boolean per criterion in config.SCOPE.
#
# The bin dimensions below are fixed, unlike the criteria: subfield, publication year
# and journal-versus-conference are the metric definition itself, so they are derived
# whether or not the matching criterion is kept. They reuse config's SQL fragments so a
# dimension and the criterion built on it cannot drift apart.
#
# is_xpac is carried through but is not a criterion. The 2025-11-12 "walden" release
# folded xpack records into works; they are a third of the corpus and virtually never in
# scope, so the flag lets step 2 exclude them from bin denominators without rebuilding
# the store.

DIMENSIONS_SQL = f"""
        TRY_CAST(substr(id, 23) AS BIGINT) AS work_id,
        CAST(publication_year AS INTEGER) AS publication_year,
        {config.SUBFIELD_SQL} AS subfield,
        {config.IS_CORE_SQL} AS is_core,
        {config.IS_PROCEEDINGS_SQL} AS is_proceedings,
        COALESCE(institutions_distinct_count, 0) AS n_institutions,
        type AS work_type,
        COALESCE(is_xpac, FALSE) AS is_xpac,
        list_transform(
            COALESCE(referenced_works, []),
            x -> TRY_CAST(substr(x, 23) AS BIGINT)
        ) AS refs"""


# ---------------------------------------------------------------------------
# Resolve dimensions
# ---------------------------------------------------------------------------
# What a query resolves on: who wrote a work, where they worked, who paid, where it
# was published. All of it is per-work in the same pass, because the expensive part is
# reaching the bytes, not the SQL.
#
# `authorships` dominates the cost: its leaves are most of what the build transfers,
# and it is the reason the files are read by PyArrow rather than by DuckDB, which
# cannot project a leaf out of a LIST(STRUCT) and would fetch the whole 14.9% column
# for any one of them. Measured, not assumed — see finding 2 in
# docs/openalex-works-schema.txt. Everything else here is under 1.2% combined.
#
# Since the column is being read either way, every field in it is free, so the
# author-level is_corresponding flag is used in preference to the top-level
# corresponding_author_ids array it is derived from (verified identical on a sample
# file) and no other column need be read.
#
# Institutions are stored lineage-expanded, so a query for a parent institution picks
# up its labs and hospitals without a recursive join, matching what the OpenAlex API
# does for authorships.institutions.lineage. OpenAlex includes an institution in its
# own lineage, so the expanded list is a superset of the direct one and is_direct
# records which of the two a row came from.

_NUM = config.numeric_id_sql
_AUTHORS = "COALESCE(authorships, [])"
_CORRESPONDING = f"list_filter({_AUTHORS}, a -> COALESCE(a.is_corresponding, FALSE))"


def _lineage_of(authorships):
    """Distinct ids of every institution in the lineage of every institution on
    `authorships`. Two flattens because the nesting is authorship -> institution ->
    lineage and flatten() removes one level at a time."""
    per_institution = f"list_transform(COALESCE(i.lineage, []), l -> {_NUM('l')})"
    per_authorship = f"flatten(list_transform(a.institutions, i -> {per_institution}))"
    return f"list_distinct(flatten(list_transform({authorships}, a -> {per_authorship})))"


RESOLVE_DIMENSIONS_SQL = f"""
        {config.DOI_SQL.format("doi")} AS doi,
        {_NUM("primary_location.source.id")} AS source_id,
        open_access.oa_status AS oa_status,
        COALESCE(open_access.is_oa, FALSE) AS is_oa,
        list_distinct(list_transform(
            COALESCE(funders, []), f -> {_NUM("f.id")}
        )) AS funder_ids,
        list_distinct(list_transform({_AUTHORS}, a -> {_NUM("a.author.id")})) AS author_ids,
        list_distinct(list_transform(
            {_CORRESPONDING}, a -> {_NUM("a.author.id")}
        )) AS corresponding_author_ids,
        {_lineage_of(_AUTHORS)} AS institution_ids,
        list_distinct(flatten(list_transform(
            {_AUTHORS}, a -> list_transform(a.institutions, i -> {_NUM("i.id")})
        ))) AS direct_institution_ids,
        {_lineage_of(_CORRESPONDING)} AS corresponding_institution_ids,
        list_distinct(list_transform(
            list_filter({_AUTHORS}, a -> a.author.orcid IS NOT NULL),
            a -> {{'author_id': {_NUM("a.author.id")},
                   'orcid': {config.ORCID_SQL.format("a.author.orcid")}}}
        )) AS author_orcids"""


def projection_sql(relation):
    """The per-file projection over `relation`, one boolean column per criterion.

    `relation` is the registered Arrow table rather than a read_parquet() call, but
    the SQL is the same either way: the projection PyArrow applies is on leaf columns
    only, so what it hands over still has the nesting the snapshot has.

    The outer SELECT exists only because in_scope cannot reference the criterion
    aliases from the select list that defines them.
    """
    criteria = ",\n".join(f"        ({sql}) AS {name}"
                          for name, (sql, _) in config.SCOPE.items())
    return f"""
WITH raw AS (
    SELECT{DIMENSIONS_SQL},
{RESOLVE_DIMENSIONS_SQL.strip()},
{criteria}
    FROM {relation}
)
SELECT *, ({" AND ".join(config.SCOPE)}) AS in_scope
FROM raw
"""


SELECTS = {
    "works_status": f"""
        SELECT work_id, publication_year,
               {", ".join(config.SCOPE)},
               in_scope, is_xpac
        FROM src
    """,
    "works_scope": """
        SELECT work_id, publication_year, subfield, is_proceedings, is_core,
               n_institutions, work_type, is_xpac
        FROM src
        WHERE in_scope
    """,
    # Citations are kept only for in-scope citing works, matching the citation
    # universe. citing_year is denormalized so fixed windows need no join later.
    "edges": """
        SELECT work_id AS citing_id, unnest(refs) AS cited_id, publication_year AS citing_year
        FROM src
        WHERE in_scope
    """,
}


# The resolve tables, unlike the three above, cover the whole corpus rather than the
# in-scope population: a resolve has to return the same works the OpenAlex API would,
# and get_metrics already reports in_scope per work rather than dropping anything. The
# flag is carried on works_attrs so a caller can narrow it down without a join.
RESOLVE_SELECTS = {
    "works_attrs": """
        SELECT work_id, publication_year, CAST(subfield AS SMALLINT) AS subfield,
               work_type, is_proceedings, source_id, oa_status, is_oa,
               in_scope, is_xpac
        FROM src
    """,
    "work_institutions": """
        SELECT institution_id, work_id,
               list_contains(direct_institution_ids, institution_id)        AS is_direct,
               list_contains(corresponding_institution_ids, institution_id) AS is_corresponding
        FROM (
            SELECT work_id, direct_institution_ids, corresponding_institution_ids,
                   unnest(institution_ids) AS institution_id
            FROM src
        )
        WHERE institution_id IS NOT NULL
    """,
    "work_authors": """
        SELECT author_id, work_id,
               list_contains(corresponding_author_ids, author_id) AS is_corresponding
        FROM (
            SELECT work_id, corresponding_author_ids, unnest(author_ids) AS author_id
            FROM src
        )
        WHERE author_id IS NOT NULL
    """,
    "work_funders": """
        SELECT funder_id, work_id
        FROM (SELECT work_id, unnest(funder_ids) AS funder_id FROM src)
        WHERE funder_id IS NOT NULL
    """,
    "work_doi": """
        SELECT doi, work_id FROM src WHERE doi IS NOT NULL
    """,
    # An author carries the same ORCID on every work, so this is deduplicated per
    # source file here and once more globally in step 3.
    "author_orcid": """
        SELECT DISTINCT pair.orcid AS orcid, pair.author_id AS author_id
        FROM (SELECT unnest(author_orcids) AS pair FROM src)
        WHERE pair.orcid IS NOT NULL AND pair.author_id IS NOT NULL
    """,
}


def _output_path(table, key):
    """Where one source file's rows for `table` are written.

    The resolve tables land in the raw staging area, because step 3 replaces them
    with a sorted copy and then deletes them.
    """
    base = config.raw_table_dir(table) if table in config.RESOLVE_TABLES \
        else config.table_dir(table)
    return base / key


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def record_snapshot(name, content, url, response=None, fetched_at=None):
    """Append one fingerprint of a manifest to the snapshot history.

    Written whenever a manifest is fetched, and also for a backfill of a file that
    was cached before this existed. date changes with each OpenAlex release; etag
    and sha256 also catch a republish that keeps the same date. A backfill has no
    response, so etag and last_modified are null.

    Returns the record it appended.
    """
    manifest = json.loads(content)
    record_count = manifest.get("record_count")
    if record_count is None:
        record_count = (manifest.get("meta") or {}).get("record_count")
    etag = last_modified = None
    if response is not None:
        etag = (response.headers.get("ETag") or "").strip().strip('"') or None
        last_modified = response.headers.get("Last-Modified")
    entry = {
        "manifest": name,
        "date": manifest.get("date"),
        "record_count": record_count,
        "etag": etag,
        "last_modified": last_modified,
        "sha256": hashlib.sha256(content).hexdigest(),
        "url": url,
        "fetched_at": fetched_at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    path = config.SNAPSHOT_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return entry


def load_manifest(refresh=False):
    """The snapshot manifest, cached under data/snapshot/.

    Refetched only on request. A new quarterly snapshot invalidates the existing
    store, so the cached copy is what keeps a finished build valid.
    """
    path = config.MANIFEST_PATH
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(config.MANIFEST_URL, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        record_snapshot("works", resp.content, config.MANIFEST_URL, response=resp)

    manifest = json.loads(path.read_text())
    files = []
    for entry in manifest["files"]:
        url = entry["url"]
        files.append({
            # updated_date=YYYY-MM-DD/part_NNNN.parquet — the checkpoint key, and
            # the path each table mirrors, so the store can be moved freely.
            "key": url.split("/works/", 1)[1],
            "url": url.replace(config.S3_PREFIX, config.HTTPS_PREFIX),
            "records": entry["meta"]["record_count"],
            "bytes": entry["meta"]["content_length"],
        })
    return manifest, files


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _connection():
    # No httpfs: PyArrow does the reading, so this connection only ever touches the
    # registered Arrow table and the local files it writes.
    con = getattr(_thread_local, "con", None)
    if con is None:
        con = connect(_tmp_dir(), config.WORKER_MEMORY_LIMIT, config.WORKER_THREADS)
        _thread_local.con = con
    return con


def process_file(entry, expected_records):
    """Reduce one source part file into every table. Returns a state record."""
    con = _connection()
    started = time.time()
    local_copy = None
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if attempt == MAX_ATTEMPTS:
                # Remote reads keep failing: pull the whole file down, read locally.
                local_copy = _tmp_dir() / f"src-{threading.get_ident()}.parquet"
                subprocess.run(
                    ["aws", "s3", "cp",
                     entry["url"].replace(config.HTTPS_PREFIX, config.S3_PREFIX),
                     str(local_copy), "--no-sign-request", "--quiet"],
                    check=True,
                )
                source = local_copy.as_posix()
            else:
                source = entry["url"]

            # Materialising src frees the Arrow table straight away, so a worker
            # holds the raw projection and the reduced one only while it crosses over.
            snapshot = _arrow().read_part(source)
            try:
                con.register("snapshot", snapshot)
                con.execute("DROP TABLE IF EXISTS src;")
                con.execute("CREATE TEMP TABLE src AS " + projection_sql("snapshot"))
            finally:
                con.unregister("snapshot")
                del snapshot

            counts = {
                table: copy_parquet(con, sql, _output_path(table, entry["key"]))
                for table, sql in {**SELECTS, **RESOLVE_SELECTS}.items()
            }
            con.execute("DROP TABLE IF EXISTS src;")

            if counts["works_status"] != expected_records:
                raise RuntimeError(
                    f"row count mismatch: wrote {counts['works_status']} works_status "
                    f"rows, manifest says {expected_records}"
                )

            return {
                "key": entry["key"],
                "records": counts["works_status"],
                "in_scope": counts["works_scope"],
                "edges": counts["edges"],
                **{table: counts[table] for table in config.RESOLVE_TABLES},
                "src_bytes": entry["bytes"],
                "seconds": round(time.time() - started, 1),
                "attempts": attempt,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        except Exception as exc:  # noqa: BLE001 - retried, then recorded and skipped
            last_error = exc
            try:
                con.execute("DROP TABLE IF EXISTS src;")
            except Exception:
                _thread_local.con = None  # connection is unusable; rebuild it
                con = _connection()
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(2 ** attempt, 30))
        finally:
            if local_copy is not None and local_copy.exists():
                local_copy.unlink()

    raise last_error


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status():
    """What the store contains right now, without touching the network."""
    if not config.MANIFEST_PATH.exists():
        size = table_size(config.table_dir("works_status"))
        if size:
            return {"built": False, "downloaded": True,
                    "tables": {"works_status": size},
                    "reason": "downloaded bundle, no local build"}
        return {"built": False, "reason": "no manifest cached"}
    _, files = load_manifest()
    state = FileCheckpoint(_state_dir())
    records = state.total("records")
    return {
        "built": len(state.completed) == len(files),
        "files": len(files),
        "files_done": len(state.completed),
        "works": records,
        "in_scope": state.total("in_scope"),
        "edges": state.total("edges"),
        "tables": {t: table_size(config.table_dir(t)) for t in config.STORE_TABLES},
        "raw_resolve": {t: state.total(t) for t in config.RESOLVE_TABLES},
        "raw_bytes": table_size(config.RESOLVE_RAW_DIR),
    }


def print_status():
    st = status()
    print("▶ Store (build step 1)")
    if st.get("downloaded"):
        from .bundle import rows_for
        rows = rows_for("works_status")
        rows_s = f"{rows:,} rows, " if rows else ""
        print(f"  · downloaded — {rows_s}"
              f"{human_bytes(st['tables']['works_status'])} of works_status")
        return
    if not st.get("files"):
        print(f"  · not built — {st.get('reason', 'nothing processed yet')}")
        return
    print(f"  files      : {st['files_done']:,}/{st['files']:,}")
    print(f"  works      : {st['works']:,}"
          + (f"  ({st['in_scope']:,} in scope, "
             f"{st['in_scope'] / st['works'] * 100:.2f}%)" if st["works"] else ""))
    print(f"  edges      : {st['edges']:,}")
    for table, size in st["tables"].items():
        print(f"  {table:<13} {human_bytes(size):>9}")
    if st["raw_bytes"]:
        print(f"  {'_resolve_raw':<13} {human_bytes(st['raw_bytes']):>9}  "
              f"staged for step 3")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _scope_diff(before, after):
    """How two {criterion: sql} mappings differ, one line each."""
    lines = [f"+ {name}  added" for name in after if name not in before]
    lines += [f"- {name}  removed" for name in before if name not in after]
    lines += [f"~ {name}  {before[name]}  ->  {after[name]}"
              for name in after if name in before and before[name] != after[name]]
    return lines


def build(workers=4, limit=None, smallest=False, refresh_manifest=False):
    """Process every pending source file. Safe to rerun; a finished store is a no-op."""
    _arrow()
    manifest, files = load_manifest(refresh=refresh_manifest)
    _tmp_dir().mkdir(parents=True, exist_ok=True)
    state = FileCheckpoint(_state_dir())

    # A new snapshot release and an edited scope both invalidate everything already
    # written, and neither can be merged in. Works move between updated_date partitions,
    # so resuming across a release would skip rewritten files as "done" and leave stale
    # rows for works that have moved; resuming across a scope change would leave a store
    # holding two different definitions of in_scope. Either means a full rebuild, which
    # is why this stops rather than warns.
    meta_path = config.STORE_STATE_PATH
    built_from = {
        "date": manifest.get("date"),
        "record_count": manifest.get("record_count"),
        "scope": config.scope_sql(),
        "projection": config.PROJECTION_VERSION,
    }
    if meta_path.exists():
        previous = json.loads(meta_path.read_text())
        if previous.get("date") != built_from["date"]:
            print(f"✗ Snapshot changed: {previous.get('date')} → {built_from['date']}")
            print(f"  The store holds {previous.get('date')} data and cannot be topped "
                  f"up from a newer release.")
            print("  Rebuild from scratch:  oam.build(reset=True)    (re-reads 67 GiB)")
            return False
        if previous.get("scope") != built_from["scope"]:
            print("✗ Scope changed since the store was built:")
            for line in _scope_diff(previous.get("scope") or {}, built_from["scope"]):
                print(f"    {line}")
            print("  Every table was filtered by the old definition, so topping up would "
                  "blend the two.")
            print("  Rebuild from scratch:  oam.build(reset=True)    (re-reads 67 GiB)")
            return False
        # A store written by an older OAM is missing whole tables and columns for the
        # files it has already marked done, and those files will never be revisited.
        if previous.get("projection", 1) != built_from["projection"]:
            print(f"✗ Projection changed: version {previous.get('projection', 1)} "
                  f"→ {built_from['projection']}")
            print("  This store was written before the current set of columns, so the "
                  "files it has already\n  processed are missing some of them.")
            print("  Rebuild from scratch:  oam.build(reset=True)    (re-reads 67 GiB)")
            return False
    else:
        meta_path.write_text(json.dumps(built_from, indent=2))

    total_bytes = sum(f["bytes"] for f in files)
    total_records = sum(f["records"] for f in files)
    pending = [f for f in files if f["key"] not in state.completed]

    print("▶ Store (build step 1)")
    print(f"  snapshot   : {manifest.get('date')}")
    print(f"  scope      : {len(config.SCOPE)} criteria [{config.scope_hash()}]  "
          f"{', '.join(config.SCOPE)}")
    print(f"  output     : {config.STORE_DIR}")
    print(f"  files      : {len(files):,} ({human_bytes(total_bytes)} of source Parquet)")
    print(f"  completed  : {len(state.completed):,}   pending: {len(pending):,}")

    if not pending:
        print("\n✓ Store complete — every manifest file already processed.")
        return True

    stale = clean_stale_tmp(*(config.table_dir(t) for t in config.STORE_TABLES),
                            config.RESOLVE_RAW_DIR)
    if stale:
        print(f"  cleaned    : {stale} uncommitted temp file(s) from a previous run")

    # Largest first keeps workers busy and surfaces memory problems early; smallest
    # first is for a quick end-to-end check.
    pending.sort(key=lambda f: f["bytes"] if smallest else -f["bytes"])
    if limit:
        pending = pending[:limit]
        print(f"  limited to : {len(pending):,} file(s) this run")

    expected = {f["key"]: f["records"] for f in files}
    run_started = time.time()
    bytes_at_start = state.total("src_bytes")
    processed = failed = 0
    print(f"\n▶ Processing with {workers} worker(s)\n", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_file, f, expected[f["key"]]): f for f in pending}
        try:
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    rec = future.result()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    state.record_failure(entry["key"], exc)
                    print(f"  ✗ {entry['key']}  {str(exc)[:160]}", flush=True)
                    continue

                state.record(rec)
                processed += 1

                done_bytes = state.total("src_bytes")
                elapsed = time.time() - run_started
                rate = (done_bytes - bytes_at_start) / elapsed if elapsed > 0 else 0
                eta = human_time((total_bytes - done_bytes) / rate) if rate > 0 else "?"
                scope_pct = rec["in_scope"] / rec["records"] * 100 if rec["records"] else 0
                print(f"  ✓ {rec['key']:<46} {rec['records']:>7,} works  "
                      f"{scope_pct:4.1f}% in scope  {rec['edges']:>9,} edges  "
                      f"{rec['seconds']:>5.1f}s"
                      f"{'  (retried)' if rec['attempts'] > 1 else ''}", flush=True)
                if processed % 25 == 0 or processed == len(pending):
                    print(f"  ── {len(state.completed):,}/{len(files):,} files  "
                          f"{done_bytes / total_bytes * 100:5.1f}%  "
                          f"{human_bytes(rate)}/s  ETA {eta}", flush=True)
        except KeyboardInterrupt:
            print("\n⚠ Interrupted. Committed files are safe; rerun to continue.", flush=True)
            pool.shutdown(wait=False, cancel_futures=True)
            sys.exit(130)

    done_records = state.total("records")
    missing = len(files) - len(state.completed)

    print(f"\n{'─' * 70}")
    print(f"  processed this run : {processed:,} file(s) in "
          f"{human_time(time.time() - run_started)}")
    if failed:
        print(f"  failed this run    : {failed:,} (see {state.failed_path})")
    print(f"  files complete     : {len(state.completed):,}/{len(files):,}")
    print(f"  works              : {done_records:,}/{total_records:,}")
    print(f"  in scope           : {state.total('in_scope'):,}")
    print(f"  citation edges     : {state.total('edges'):,}")
    print(f"  work-institutions  : {state.total('work_institutions'):,}")
    print(f"  work-authors       : {state.total('work_authors'):,}")
    for table in config.STORE_TABLES:
        print(f"  {table:<19} {human_bytes(table_size(config.table_dir(table))):>9}")
    print(f"  {'_resolve_raw':<19} "
          f"{human_bytes(table_size(config.RESOLVE_RAW_DIR)):>9}  staged for step 3")

    if missing:
        print(f"\n⚠ {missing:,} file(s) still pending — rerun to finish.")
        return False
    if done_records != total_records:
        print(f"\n✗ Record total mismatch: {done_records:,} written vs "
              f"{total_records:,} in manifest")
        return False

    print(f"\n✓ Store complete: every manifest file processed, {done_records:,} works verified.")
    return True


def reset():
    """Drop the store, its resolve staging area and its checkpoint."""
    for table in config.STORE_TABLES:
        shutil.rmtree(config.table_dir(table), ignore_errors=True)
    shutil.rmtree(config.RESOLVE_RAW_DIR, ignore_errors=True)
    shutil.rmtree(_state_dir(), ignore_errors=True)
    shutil.rmtree(_tmp_dir(), ignore_errors=True)
    print(f"✓ Reset {config.STORE_DIR}")
