"""Build step 2 of 2 — the metric layers every query is answered from.

Reads works_scope and edges from the store and materializes three tables under
data/metrics/:

  citation_counts/  citations received per in-scope work (open window and 3-year)
  bin_stats/        n_works, mean, median, stdev, p90, p99 per subfield x year x type
  work_metrics/     per-work ncs, percentile, decile, is_top10, is_top1

Stage 1 is the only expensive pass: 2.16B edges aggregated once. Stages 2 and 3 work
on 62M rows and are cheap, so re-deriving metrics after a definition change never
touches the edge list again. Each stage is committed atomically and checkpointed, so
an interrupted run resumes at the stage it stopped on. The whole build is ~80s.
"""

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


W = config.WINDOW_YEARS


# ---------------------------------------------------------------------------
# Stage 1 — citations received per in-scope work
# ---------------------------------------------------------------------------
# The citation universe is the in-scope population itself: a citation counts only if
# the citing work is also in scope. edges already holds only in-scope citing works,
# so the sole extra filter is the xpack exclusion (see build_universe).

# The edges path is filled in at build time, not at import, so set_data_dir()
# still points this query at the directory in use.
CITATION_COUNTS_SQL = f"""
SELECT
    e.cited_id                                       AS work_id,
    count(*)::INTEGER                                AS citations,
    count(*) FILTER (
        WHERE e.citing_year <= u.publication_year + {W - 1}
    )::INTEGER                                       AS citations_{W}y
FROM read_parquet('{{edges}}') e
JOIN universe u ON u.work_id = e.cited_id
{{citer_filter}}
GROUP BY 1
"""


# ---------------------------------------------------------------------------
# Stage 2 — bin statistics
# ---------------------------------------------------------------------------
# A bin is subfield x publication_year x (journal | conference). Uncited works must
# be present with a zero, so this reads the universe and left-joins the counts
# rather than aggregating citation_counts directly.

BIN_STATS_SQL = f"""
WITH works AS (
    SELECT u.publication_year, u.subfield, u.is_proceedings,
           COALESCE(c.citations, 0)        AS citations,
           COALESCE(c.citations_{W}y, 0)   AS citations_{W}y
    FROM universe u
    LEFT JOIN read_parquet('{{counts}}') c ON c.work_id = u.work_id
)
SELECT
    publication_year,
    subfield,
    is_proceedings,
    count(*)::BIGINT                                  AS n_works,
    count(*) FILTER (WHERE citations > 0)::BIGINT     AS n_cited_works,
    avg(citations)                                    AS mean_citations,
    median(citations)                                 AS median_citations,
    stddev_samp(citations)                            AS stdev_citations,
    quantile_cont(citations, 0.90)                    AS p90,
    quantile_cont(citations, 0.99)                    AS p99,
    max(citations)::INTEGER                           AS max_citations,
    avg(citations_{W}y)                               AS mean_citations_{W}y,
    quantile_cont(citations_{W}y, 0.90)               AS p90_{W}y,
    quantile_cont(citations_{W}y, 0.99)               AS p99_{W}y,
    (count(*) >= {config.RELIABILITY_THRESHOLD})      AS is_reliable
FROM works
GROUP BY 1, 2, 3
"""


# ---------------------------------------------------------------------------
# Stage 3 — per-work metrics
# ---------------------------------------------------------------------------
# The definitions:
#   ncs        = citations / bin mean            -> averages to MNCS
#   percentile = CUME_DIST() ascending, 0..1
#   decile     = NTILE(10) descending, 1 = top decile
#   is_top10   = citations >= bin p90            (>= keeps tied works, and integer
#                                                 citation counts tie often, so the
#                                                 world baseline lands above 10%)
# Metrics are computed for every work, including those in bins too small to be
# stable; bin_is_reliable is exposed so a caller can filter rather than being handed
# a NULL.

WORK_METRICS_SQL = f"""
WITH joined AS (
    SELECT
        u.work_id,
        u.publication_year,
        u.subfield,
        u.is_proceedings,
        u.n_institutions,
        COALESCE(c.citations, 0)        AS citations,
        COALESCE(c.citations_{W}y, 0)   AS citations_{W}y,
        b.n_works                       AS bin_n_works,
        b.mean_citations,
        b.median_citations,
        b.stdev_citations,
        b.p90,
        b.p99,
        b.mean_citations_{W}y,
        b.p90_{W}y,
        b.p99_{W}y,
        b.is_reliable                   AS bin_is_reliable
    FROM universe u
    LEFT JOIN read_parquet('{{counts}}') c ON c.work_id = u.work_id
    JOIN read_parquet('{{bins}}') b
      ON  b.publication_year = u.publication_year
     AND b.subfield          = u.subfield
     AND b.is_proceedings    = u.is_proceedings
),
ranked AS (
    SELECT
        j.*,
        CUME_DIST() OVER (
            PARTITION BY publication_year, subfield, is_proceedings
            ORDER BY citations
        ) AS percentile,
        NTILE(10) OVER (
            PARTITION BY publication_year, subfield, is_proceedings
            ORDER BY citations DESC
        )::SMALLINT AS decile
    FROM joined j
)
SELECT
    work_id,
    publication_year,
    subfield,
    is_proceedings,
    n_institutions,
    citations,
    citations_{W}y,
    (citations > 0)                                                     AS is_cited,
    CASE WHEN mean_citations   > 0 THEN citations / mean_citations   END AS ncs,
    CASE WHEN median_citations > 0 THEN citations / median_citations END AS norm_vs_median,
    CASE WHEN stdev_citations  > 0
         THEN (citations - mean_citations) / stdev_citations          END AS zscore,
    percentile,
    decile,
    (citations >= p90)                                                  AS is_top10,
    (citations >= p99)                                                  AS is_top1,
    CASE WHEN mean_citations_{W}y > 0
         THEN citations_{W}y / mean_citations_{W}y                    END AS ncs_{W}y,
    (citations_{W}y >= p90_{W}y)                                        AS is_top10_{W}y,
    (citations_{W}y >= p99_{W}y)                                        AS is_top1_{W}y,
    bin_n_works,
    bin_is_reliable
FROM ranked
"""


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------

def build_universe(con, include_xpack):
    """The metric population: in-scope works, xpack excluded unless asked for.

    xpack records arrived with the 2025-11-12 "walden" release and are a third of the
    corpus, but only ~44k of them are in scope. They are excluded by default so bin
    denominators stay comparable with pre-walden snapshots.

    A publication year is required because it is one of the three bin dimensions: a
    work without one cannot be normalised against a peer group.
    """
    scope = config.table_glob("works_scope")
    conditions = ["publication_year IS NOT NULL"]
    if not include_xpack:
        conditions.append("NOT is_xpac")
    con.execute(f"""
        CREATE TEMP TABLE universe AS
        SELECT work_id, publication_year, subfield, is_proceedings, n_institutions
        FROM read_parquet('{scope}')
        WHERE {' AND '.join(conditions)}
    """)
    con.execute(f"""
        CREATE TEMP TABLE excluded_citers AS
        SELECT work_id FROM read_parquet('{scope}')
        WHERE {'FALSE' if include_xpack else 'is_xpac'}
    """)
    return (con.execute("SELECT count(*) FROM universe").fetchone()[0],
            con.execute("SELECT count(*) FROM excluded_citers").fetchone()[0])


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

def build(stage=None, include_xpack=False):
    """Materialize any stage that is missing or built under a different config."""
    if not config.table_dir("works_scope").is_dir():
        print(f"✗ Store not found at {config.STORE_DIR} — run oam.build(steps=['store']) first.")
        return False

    _tmp_dir().mkdir(parents=True, exist_ok=True)
    state = StageCheckpoint(_state_dir())
    # The scope belongs here even though step 2 never applies it: works_scope and edges
    # arrive already filtered, so a store rebuilt under different criteria must
    # invalidate every layer derived from it.
    settings = {
        "include_xpack": include_xpack,
        "reliability_threshold": config.RELIABILITY_THRESHOLD,
        "window_years": W,
        "scope": config.scope_hash(config.built_scope()),
    }

    print("▶ Metric layers (build step 2)")
    print(f"  store  : {config.STORE_DIR}")
    print(f"  output : {config.METRICS_DIR}")
    print(f"  xpack  : {'included' if include_xpack else 'excluded from the population'}")
    print(f"  bins   : reliable at >= {config.RELIABILITY_THRESHOLD} works")

    con = connect(_tmp_dir())
    t0 = time.time()
    universe_rows, excluded_rows = build_universe(con, include_xpack)
    print(f"\n  population: {universe_rows:,} in-scope works "
          f"({excluded_rows:,} xpack works excluded)  [{human_time(time.time() - t0)}]")

    forced = stage or len(config.METRIC_TABLES) + 1
    counts = config.table_glob("citation_counts")
    bins = config.table_glob("bin_stats")

    plan = [
        ("citation_counts", "aggregating the edge list", None),
        ("bin_stats", "subfield x year x type", None),
        ("work_metrics", "normalising every work in its bin", "publication_year"),
    ]

    for n, (table, what, partition_by) in enumerate(plan, start=1):
        header = f"Stage {n}/{len(plan)}  {table}"
        if forced > n and state.done(table, settings):
            print(f"\n  ✓ {header} up to date ({state.data[table]['rows']:,} rows)")
            continue

        print(f"\n▶ {header} — {what}", flush=True)
        t0 = time.time()
        if table == "citation_counts":
            citer_filter = "" if include_xpack else (
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM excluded_citers x WHERE x.work_id = e.citing_id)"
            )
            sql = CITATION_COUNTS_SQL.format(
                citer_filter=citer_filter, edges=config.table_glob("edges"))
        elif table == "bin_stats":
            sql = BIN_STATS_SQL.format(counts=counts)
        else:
            sql = WORK_METRICS_SQL.format(counts=counts, bins=bins)

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
