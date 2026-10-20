"""Build step 3 of 3 — the resolve warehouse, so a query needs no OpenAlex API.

Step 1 wrote the resolve rows out per source file, in whatever order the snapshot
happened to hold them. This step sorts each one by the key it is looked up on and
publishes it under data/resolve/:

  works_attrs/        one row per work in OpenAlex: year, subfield, type, source, OA
  work_institutions/  work x institution, lineage-expanded
  work_authors/       work x author
  work_funders/       work x funder
  work_doi/           DOI -> work
  author_orcid/       ORCID -> author
  dim_*/              names for the numeric ids, from the entity snapshots

The sort is the whole point. A Parquet reader keeps the min and max of every column
in each row group, so a table sorted by institution_id lets a lookup for one
institution skip every row group that cannot contain it — 45,000 works come back out
of 275 million rows without reading the rest. Nothing here is an index; it is the
file layout doing the work, which is why the checks verify the ordering directly.

Each table is committed atomically and checkpointed, and its unsorted input is
deleted as soon as the sorted copy is published, so the peak disk cost is one table
rather than two full copies of the warehouse. That also means the layout cannot be
changed in place: a different sort key needs `oam.build(reset=True)`, the same as a
scope change. Takes about 25 minutes, nearly all of it work_authors.
"""

import json
import shutil
import time

import requests

from . import checks, config
from .duck import (StageCheckpoint, connect, copy_parquet, human_bytes,
                   human_time, table_size)

def _state_dir():
    """Checkpoint directory. Looked up live so set_data_dir() is honoured."""
    return config.RESOLVE_DIR / "_state"


def _tmp_dir():
    return config.RESOLVE_DIR / "_tmp"


# ---------------------------------------------------------------------------
# The fact tables
# ---------------------------------------------------------------------------
# Each entry is the sort key, and whether the rows need deduplicating. The sort key
# always ends in the work id so that rows for one lookup key arrive in a stable order
# and compress well; ids close together differ in few bits.
#
# works_attrs is sorted by the dimensions rather than by work_id because that is how
# it is filtered: a resolve arrives with work ids from a pair table and asks which of
# them match a year or a subfield, so the year has to prune the row groups. Hive
# partitioning on the year would do the same, but 0.03% of works have no publication
# year and would land in a null partition, which is a sharp edge for no gain.

FACTS = {
    "works_attrs":       ("publication_year, subfield, work_id", False),
    "work_institutions": ("institution_id, work_id", False),
    "work_authors":      ("author_id, work_id", False),
    "work_funders":      ("funder_id, work_id", False),
    "work_doi":          ("doi, work_id", False),
    "author_orcid":      ("orcid, author_id", True),
}

# The column whose ordering a lookup depends on, and which checks.py verifies.
SORT_KEY = {table: order.split(",")[0].strip() for table, (order, _) in FACTS.items()}


# ---------------------------------------------------------------------------
# The dimension tables
# ---------------------------------------------------------------------------
# Names for the ids the fact tables store, read from the small entity snapshots
# rather than the works files or the API: the entity snapshot belongs to the same
# release as everything else, so a rebuild cannot mix names from one quarter with
# works from another. All four together are a few MiB.
#
# Field and domain need no per-work column. OpenAlex nests them under the subfield,
# so resolving by field or domain is a join from the 252-row dim_topics onto the
# subfield already on works_attrs.
#
# An entity can in principle appear in more than one updated_date partition; the
# QUALIFY keeps the most recently updated row for each id.

_NUM = config.numeric_id_sql
_LATEST = "QUALIFY row_number() OVER (PARTITION BY id ORDER BY updated_date DESC) = 1"
_TOPIC_ID = "TRY_CAST(str_split({}, '/')[-1] AS SMALLINT)"

DIMS = {
    "dim_topics": ("subfields", f"""
        SELECT {_TOPIC_ID.format("id")}          AS subfield_id,
               display_name                      AS subfield_name,
               {_TOPIC_ID.format("field.id")}    AS field_id,
               field.display_name                AS field_name,
               {_TOPIC_ID.format("domain.id")}   AS domain_id,
               domain.display_name               AS domain_name
        FROM read_parquet({{files}}, union_by_name=true)
        {_LATEST}
        ORDER BY subfield_id
    """),
    "dim_institutions": ("institutions", f"""
        SELECT {_NUM("id")} AS institution_id, display_name, country_code, type, ror
        FROM read_parquet({{files}}, union_by_name=true)
        {_LATEST}
        ORDER BY institution_id
    """),
    "dim_sources": ("sources", f"""
        SELECT {_NUM("id")} AS source_id, display_name, issn_l, type, country_code,
               COALESCE(is_core, FALSE) AS is_core
        FROM read_parquet({{files}}, union_by_name=true)
        {_LATEST}
        ORDER BY source_id
    """),
    "dim_funders": ("funders", f"""
        SELECT {_NUM("id")} AS funder_id, display_name, country_code
        FROM read_parquet({{files}}, union_by_name=true)
        {_LATEST}
        ORDER BY funder_id
    """),
}


def load_entity_manifest(refresh=False):
    """The manifest covering every entity type, cached under data/snapshot/.

    Only the small entities are read from it. Like the works manifest it is refetched
    only on request, so a finished build keeps pointing at the release it was built
    from.
    """
    path = config.ENTITY_MANIFEST_PATH
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(config.ENTITY_MANIFEST_URL, timeout=300)
        response.raise_for_status()
        path.write_bytes(response.content)
        # Imported here: store is the step that owns the history file, and importing
        # it at module level would pull the store build into every resolve import.
        from .store import record_snapshot
        record_snapshot("entities", response.content, config.ENTITY_MANIFEST_URL,
                        response=response)

    manifest = json.loads(path.read_text())
    return manifest, {
        entity["entity"]: [f["url"].replace(config.S3_PREFIX, config.HTTPS_PREFIX)
                           for f in entity["files"]]
        for entity in manifest["entities"]
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def tables():
    """Every table the warehouse publishes, facts first."""
    return config.RESOLVE_TABLES + config.DIM_TABLES


def is_built():
    """Whether every table a local resolve reads is present."""
    return all(table_size(config.table_dir(t)) > 0 for t in tables())


def _checkpoint():
    """The stage record, without creating the directory when nothing is built."""
    path = _state_dir() / "stages.json"
    return json.loads(path.read_text()) if path.exists() else {}


def status():
    data = _checkpoint()
    # A download has no stage checkpoint; the bundle records the row counts.
    from .bundle import rows_for
    return {
        "built": is_built(),
        "tables": {
            t: {"rows": data.get(t, {}).get("rows", rows_for(t)),
                "bytes": table_size(config.table_dir(t)),
                "at": data.get(t, {}).get("at")}
            for t in tables()
        },
    }


def print_status():
    st = status()
    print("▶ Resolve warehouse (build step 3)")
    for table, info in st["tables"].items():
        if info["bytes"]:
            rows = f"{info['rows']:,}" if info["rows"] is not None else "—"
            when = f"  built {info['at']}" if info["at"] else ""
            print(f"  ✓ {table:<18} {rows:>15} rows  "
                  f"{human_bytes(info['bytes']):>9}{when}")
        else:
            print(f"  · {table:<18} not built")
    staged = table_size(config.RESOLVE_RAW_DIR)
    if staged:
        print(f"  · {'_resolve_raw':<18} {human_bytes(staged):>36}  still to consolidate")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _raw_glob(table):
    return (config.raw_table_dir(table) / "**" / "*.parquet").as_posix()


def _fact_sql(table):
    order, distinct = FACTS[table]
    return (f"SELECT {'DISTINCT ' if distinct else ''}* "
            f"FROM read_parquet('{_raw_glob(table)}') ORDER BY {order}")


def build(refresh_manifest=False):
    """Publish any table that is missing or was built from a different release."""
    snapshot = config.STORE_STATE_PATH
    if not snapshot.exists():
        print(f"✗ Store not found at {config.STORE_DIR} — run oam.build(steps=['store']) first.")
        return False

    built_from = json.loads(snapshot.read_text())
    settings = {
        "snapshot": built_from.get("date"),
        "projection": built_from.get("projection", 1),
    }

    _tmp_dir().mkdir(parents=True, exist_ok=True)
    state = StageCheckpoint(_state_dir())
    con = connect(_tmp_dir())

    print("▶ Resolve warehouse (build step 3)")
    print(f"  store    : {config.STORE_DIR}")
    print(f"  output   : {config.RESOLVE_DIR}")
    print(f"  coverage : every work in the corpus, in scope or not")

    total = len(tables())
    for n, table in enumerate(config.RESOLVE_TABLES, start=1):
        order, _ = FACTS[table]
        header = f"Stage {n}/{total}  {table}"
        if state.done(table, {**settings, "order": order}):
            print(f"\n  ✓ {header} up to date ({state.data[table]['rows']:,} rows)")
            continue

        if not table_size(config.raw_table_dir(table)):
            print(f"\n✗ {header}: nothing staged at {config.raw_table_dir(table)}")
            print("  Step 1 writes it and step 3 consumes it, so it cannot be "
                  "regenerated on its own.")
            print("  Rebuild from scratch:  oam.build(reset=True)")
            return False

        print(f"\n▶ {header} — sorting by {order}", flush=True)
        t0 = time.time()
        rows = copy_parquet(con, _fact_sql(table), config.table_dir(table))
        state.record(table, rows, time.time() - t0, {**settings, "order": order})
        # The sorted copy is published, so the input is now dead weight.
        shutil.rmtree(config.raw_table_dir(table), ignore_errors=True)
        print(f"  ✓ {rows:,} rows  "
              f"{human_bytes(table_size(config.table_dir(table)))}  "
              f"[{human_time(time.time() - t0)}]  staging dropped", flush=True)

    entity_files = {}
    pending = [t for t in config.DIM_TABLES if not state.done(t, settings)]
    if pending:
        manifest, entity_files = load_entity_manifest(refresh=refresh_manifest)
        if manifest.get("date") != settings["snapshot"]:
            print(f"\n✗ The entity manifest is release {manifest.get('date')} but the "
                  f"store was built from {settings['snapshot']}.")
            print("  Names and works have to come from the same release.")
            print("  Refetch it:  oam.build(steps=['resolve'], refresh_manifest=True)")
            return False

    for n, table in enumerate(config.DIM_TABLES, start=len(config.RESOLVE_TABLES) + 1):
        entity, sql = DIMS[table]
        header = f"Stage {n}/{total}  {table}"
        if table not in pending:
            print(f"\n  ✓ {header} up to date ({state.data[table]['rows']:,} rows)")
            continue

        print(f"\n▶ {header} — from the {entity} snapshot "
              f"({len(entity_files[entity])} files)", flush=True)
        t0 = time.time()
        files = "[" + ", ".join(f"'{u}'" for u in entity_files[entity]) + "]"
        rows = copy_parquet(con, sql.format(files=files), config.table_dir(table))
        state.record(table, rows, time.time() - t0, settings)
        print(f"  ✓ {rows:,} rows  "
              f"{human_bytes(table_size(config.table_dir(table)))}  "
              f"[{human_time(time.time() - t0)}]", flush=True)

    print()
    if not checks.report_resolve():
        return False

    print(f"\n✓ Resolve warehouse complete — "
          f"{human_bytes(sum(table_size(config.table_dir(t)) for t in tables()))} "
          f"across {total} tables.")
    return True


def reset():
    """Drop the warehouse and its checkpoint. The staged input is part of the store."""
    for table in tables():
        shutil.rmtree(config.table_dir(table), ignore_errors=True)
    shutil.rmtree(_state_dir(), ignore_errors=True)
    shutil.rmtree(_tmp_dir(), ignore_errors=True)
    print(f"✓ Reset {config.RESOLVE_DIR}")
