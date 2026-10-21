"""Build step 2 of 3 — the metric layers every query is answered from.

Reads works_scope and edges from the store and materializes three tables under
data/metrics/:

  citation_counts/  in-scope citations, and the 4-year count without self-citations
  cohort_stats/     cohort size, mean, p90 and p99 of the 4-year count
  work_metrics/     per-work scores

Stage 1 is the expensive pass: the edge list aggregated once. Stages 2 and 3 work
on the scored works and are cheap, so a definition change never touches the edge
list again. Each stage is committed atomically and checkpointed, so an interrupted
run resumes at the stage it stopped on.
"""

import hashlib
import json
import shutil
import time

from . import checks, config, report
from .duck import (StageCheckpoint, connect, copy_parquet, human_bytes,
                   human_time, table_size)

def _state_dir():
    """Checkpoint directory. Looked up live so set_data_dir() is honoured."""
    return config.METRICS_DIR / "_state"


def _tmp_dir():
    return config.METRICS_DIR / "_tmp"


# Publication year and the three following years. The names say _4y; this constant
# is what the comparisons use.
_WITHIN = config.WINDOW_YEARS - 1


# ---------------------------------------------------------------------------
# Stage 1 — citations received per scored work
# ---------------------------------------------------------------------------
# A citation counts when the citing work is in scope. edges already holds only
# those, and joining citers drops xpack citers. The 4-year count also drops a
# self-citation, which is one where the two works share an author. The all-time
# count keeps self-citations, because it is the in-scope part of OpenAlex's
# cited_by_count.

CITATION_COUNTS_SQL = f"""
SELECT
    e.cited_id AS work_id,
    count(*)::INTEGER AS cited_by_count_in_scope,
    count(*) FILTER (
        WHERE e.citing_year <= u.publication_year + {_WITHIN}
          AND NOT list_has_any(c.author_ids, u.author_ids)
    )::INTEGER AS citations_4y
FROM read_parquet('{{edges}}') e
JOIN universe u ON u.work_id = e.cited_id
JOIN citers c ON c.work_id = e.citing_id
GROUP BY 1
"""


# ---------------------------------------------------------------------------
# Stage 2 — cohort statistics
# ---------------------------------------------------------------------------
# A cohort is subfield x publication year x (journal | conference). Uncited works
# must be present with a zero, so this reads the universe and left-joins the counts.

COHORT_STATS_SQL = """
WITH works AS (
    SELECT u.publication_year, u.subfield, u.is_proceedings,
           COALESCE(c.citations_4y, 0) AS citations_4y
    FROM universe u
    LEFT JOIN read_parquet('{counts}') c ON c.work_id = u.work_id
)
SELECT
    publication_year,
    subfield,
    is_proceedings,
    count(*)::BIGINT AS n_works,
    avg(citations_4y) AS mean_citations_4y,
    quantile_cont(citations_4y, 0.90) AS p90_4y,
    quantile_cont(citations_4y, 0.99) AS p99_4y,
    (count(*) >= {threshold}) AS is_reliable
FROM works
GROUP BY 1, 2, 3
"""


# ---------------------------------------------------------------------------
# Stage 3 — per-work metrics
# ---------------------------------------------------------------------------
# ncs_4y is citations divided by the cohort mean, and averages to 1.
# The percentile is the mid-rank: papers below, plus half of the papers tied
# with this one, over the cohort size. Tied papers get the same value, and an
# uncited paper is not given zero.
# Top 10% and top 1% require a citation. A cohort whose threshold is 0 would
# otherwise flag every paper, including the uncited ones.

WORK_METRICS_SQL = f"""
WITH joined AS (
    SELECT
        u.work_id,
        u.publication_year,
        u.subfield,
        u.is_proceedings,
        u.institutions_distinct_count,
        u.cited_by_count,
        u.openalex_fwci,
        u.openalex_citation_normalized_percentile,
        u.openalex_is_in_top_10_percent,
        u.openalex_is_in_top_1_percent,
        COALESCE(c.citations_4y, 0) AS citations_4y,
        COALESCE(c.cited_by_count_in_scope, 0) AS cited_by_count_in_scope,
        b.n_works AS cohort_n_works,
        b.mean_citations_4y,
        b.p90_4y,
        b.p99_4y,
        b.is_reliable AS cohort_is_reliable
    FROM universe u
    LEFT JOIN read_parquet('{{counts}}') c ON c.work_id = u.work_id
    JOIN read_parquet('{{cohorts}}') b
      ON  b.publication_year = u.publication_year
     AND b.subfield = u.subfield
     AND b.is_proceedings = u.is_proceedings
)
SELECT
    work_id,
    publication_year,
    subfield,
    is_proceedings,
    institutions_distinct_count,
    citations_4y,
    CASE WHEN mean_citations_4y > 0
         THEN citations_4y / mean_citations_4y END AS ncs_4y,
    (rank() OVER w - 1
        + 0.5 * count(*) OVER (
            PARTITION BY publication_year, subfield, is_proceedings, citations_4y)
    ) / cohort_n_works AS citation_normalized_percentile_4y,
    (citations_4y >= p90_4y AND citations_4y >= 1) AS is_in_top_10_percent_4y,
    (citations_4y >= p99_4y AND citations_4y >= 1) AS is_in_top_1_percent_4y,
    publication_year + {_WITHIN} < {{snapshot_year}} AS window_is_complete,
    cited_by_count_in_scope,
    greatest(cited_by_count - cited_by_count_in_scope, 0) AS cited_by_count_outside_scope,
    openalex_fwci,
    openalex_citation_normalized_percentile,
    openalex_is_in_top_10_percent,
    openalex_is_in_top_1_percent,
    cohort_n_works,
    cohort_is_reliable
FROM joined
WINDOW w AS (
    PARTITION BY publication_year, subfield, is_proceedings
    ORDER BY citations_4y
)
"""


def _definition_hash():
    """Fingerprint of the three stage queries, so an edit rebuilds them."""
    text = CITATION_COUNTS_SQL + COHORT_STATS_SQL + WORK_METRICS_SQL
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------

def build_universe(con, include_xpack):
    """Scored works, and the in-scope works whose citations count.

    xpack records arrived with the 2025-11-12 release and are a third of the
    corpus, but almost none of them are in scope. They are excluded by default so
    cohort denominators stay comparable with earlier snapshots. A work without a
    publication year cannot be scored; it can still cite.
    """
    scope = config.table_glob("works_scope")
    keep = "TRUE" if include_xpack else "NOT is_xpac"
    con.execute(f"""
        CREATE TEMP TABLE universe AS
        SELECT work_id, publication_year, subfield, is_proceedings,
               institutions_distinct_count,
               COALESCE(author_ids, []) AS author_ids,
               cited_by_count,
               openalex_fwci, openalex_citation_normalized_percentile,
               openalex_is_in_top_10_percent, openalex_is_in_top_1_percent
        FROM read_parquet('{scope}')
        WHERE publication_year IS NOT NULL AND {keep}
    """)
    con.execute(f"""
        CREATE TEMP TABLE citers AS
        SELECT work_id, COALESCE(author_ids, []) AS author_ids
        FROM read_parquet('{scope}')
        WHERE {keep}
    """)
    excluded = "0" if include_xpack else f"""
        (SELECT count(*) FROM read_parquet('{scope}') WHERE is_xpac)"""
    return con.execute(f"""
        SELECT (SELECT count(*) FROM universe), {excluded}
    """).fetchone()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _checkpoint():
    """The stage record, without creating the directory when nothing is built."""
    path = _state_dir() / "stages.json"
    return json.loads(path.read_text()) if path.exists() else {}


def status():
    data = _checkpoint()
    # A download has no stage checkpoint; the bundle records the row counts.
    from .bundle import rows_for
    return {
        "built": all(table_size(config.table_dir(t)) > 0 for t in config.METRIC_TABLES),
        "tables": {
            t: {"rows": data.get(t, {}).get("rows", rows_for(t)),
                "bytes": table_size(config.table_dir(t)),
                "at": data.get(t, {}).get("at")}
            for t in config.METRIC_TABLES
        },
        "config": next(iter(data.values()), {}).get("config"),
    }


def print_status():
    st = status()
    print("▶ Metric layers (build step 2)")
    for table, info in st["tables"].items():
        if info["bytes"]:
            rows = f"{info['rows']:,}" if info["rows"] is not None else "—"
            when = f"  built {info['at']}" if info["at"] else ""
            print(f"  ✓ {table:<16} {rows:>13} rows  "
                  f"{human_bytes(info['bytes']):>9}{when}")
        else:
            print(f"  · {table:<16} not built")
    if st["config"]:
        print(f"  config: {st['config']}")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _settings(include_xpack):
    snapshot = json.loads(config.STORE_STATE_PATH.read_text())["date"]
    return {
        "include_xpack": include_xpack,
        "reliability_threshold": config.RELIABILITY_THRESHOLD,
        "window_years": config.WINDOW_YEARS,
        "scope": config.scope_hash(config.built_scope()),
        "snapshot": snapshot,
        "definitions": _definition_hash(),
    }, int(snapshot[:4])


def build(stage=None, include_xpack=False):
    """Materialize any stage that is missing or built under a different config."""
    if not config.table_dir("works_scope").is_dir():
        print(f"✗ Store not found at {config.STORE_DIR} — run oam.build(steps=['store']) first.")
        return False

    _tmp_dir().mkdir(parents=True, exist_ok=True)
    state = StageCheckpoint(_state_dir())
    # The scope belongs here even though step 2 never applies it: works_scope and
    # edges arrive already filtered, so a store rebuilt under different criteria
    # must invalidate every layer derived from it.
    settings, snapshot_year = _settings(include_xpack)

    print("▶ Metric layers (build step 2)")
    print(f"  store    : {config.STORE_DIR}")
    print(f"  output   : {config.METRICS_DIR}")
    print(f"  xpack    : {'included' if include_xpack else 'excluded from the population'}")
    print(f"  cohorts  : reliable at >= {config.RELIABILITY_THRESHOLD} works")

    con = connect(_tmp_dir())
    t0 = time.time()
    universe_rows, excluded_rows = build_universe(con, include_xpack)
    print(f"\n  population: {universe_rows:,} in-scope works "
          f"({excluded_rows:,} xpack works excluded)  [{human_time(time.time() - t0)}]")

    forced = stage or len(config.METRIC_TABLES) + 1
    counts = config.table_glob("citation_counts")
    cohorts = config.table_glob("cohort_stats")

    plan = [
        ("citation_counts", "aggregating the edge list", None),
        ("cohort_stats", "subfield x year x type", None),
        ("work_metrics", "scoring every work in its cohort", "publication_year"),
    ]

    for n, (table, what, partition_by) in enumerate(plan, start=1):
        header = f"Stage {n}/{len(plan)}  {table}"
        if forced > n and state.done(table, settings):
            print(f"\n  ✓ {header} up to date ({state.data[table]['rows']:,} rows)")
            continue

        print(f"\n▶ {header} — {what}", flush=True)
        t0 = time.time()
        if table == "citation_counts":
            sql = CITATION_COUNTS_SQL.format(edges=config.table_glob("edges"))
        elif table == "cohort_stats":
            sql = COHORT_STATS_SQL.format(
                counts=counts, threshold=config.RELIABILITY_THRESHOLD)
        else:
            sql = WORK_METRICS_SQL.format(
                counts=counts, cohorts=cohorts, snapshot_year=snapshot_year)

        rows = copy_parquet(con, sql, config.table_dir(table), partition_by)
        state.record(table, rows, time.time() - t0, settings)
        print(f"  ✓ {rows:,} rows  "
              f"{human_bytes(table_size(config.table_dir(table)))}  "
              f"[{human_time(time.time() - t0)}]", flush=True)

    print()
    if not checks.report(include_xpack=include_xpack):
        return False

    print()
    report.print_profile()
    print("\n✓ Metric layers complete and self-consistent.")
    return True


def reset():
    """Drop the metric layers and their checkpoint."""
    for table in config.METRIC_TABLES:
        shutil.rmtree(config.table_dir(table), ignore_errors=True)
    shutil.rmtree(_state_dir(), ignore_errors=True)
    print(f"✓ Reset {config.METRICS_DIR}")
