"""Run logging, and the end-of-build profile.

Two things that are about looking at the data rather than building or validating it:

  tee()           duplicates everything a run prints to a timestamped file under
                  data/logs/, so a 1.5h build can be followed with `tail -f` and read
                  back afterwards
  print_profile() what the built data actually contains — the descriptive counterpart
                  to checks.py, which only answers pass or fail

The profile costs about 4s: the scope funnel is the only wide scan, and it reads one
boolean column per scope criterion off works_status, plus in_scope and is_xpac.
"""

import json
import sys
import time
from contextlib import contextmanager

from . import config
from .duck import connect, human_bytes, table_size


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _Tee:
    """A write-through stdout that also appends to an open file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return False


@contextmanager
def tee(name):
    """Mirror everything printed inside the block to data/logs/<name>-<stamp>.log."""
    path = config.LOGS_DIR / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = sys.stdout
    with path.open("w", buffering=1) as fh:  # line buffered, so tail -f keeps up
        sys.stdout = _Tee(original, fh)
        try:
            print(f"▶ Logging to {path}\n  follow with:  tail -f {path}\n")
            yield path
        finally:
            sys.stdout = original
    print(f"\n  log: {path}")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _scalar(con, sql):
    return con.execute(sql).fetchone()


def profile():
    """Descriptive statistics for whatever is currently built."""
    status, scope = config.table_glob("works_status"), config.table_glob("works_scope")
    works, bins = config.table_glob("work_metrics"), config.table_glob("bin_stats")
    con = connect(config.DATA_DIR / "_tmp")
    out = {}

    snapshot = config.STORE_STATE_PATH
    out["snapshot"] = json.loads(snapshot.read_text()).get("date") if snapshot.exists() else None

    if config.table_dir("works_status").is_dir():
        # The criteria the store was built with, not necessarily the ones configured now.
        criteria = config.built_criteria()
        failures = ", ".join(
            f"count(*) FILTER (WHERE NOT {col}) AS {col}" for col, _ in criteria)
        row = _scalar(con, f"""
            SELECT count(*), count(*) FILTER (WHERE in_scope),
                   count(*) FILTER (WHERE is_xpac), {failures}
            FROM read_parquet('{status}')""")
        out["corpus"], out["in_scope"], out["xpack"] = row[0], row[1], row[2]
        out["funnel"] = sorted(zip((label for _, label in criteria), row[3:]),
                               key=lambda kv: -kv[1])

    if config.table_dir("bin_stats").is_dir():
        out["bins"] = _scalar(con, f"""
            SELECT count(*), count(*) FILTER (WHERE is_reliable),
                   count(DISTINCT subfield), min(publication_year), max(publication_year)
            FROM read_parquet('{bins}')""")

    if config.table_dir("work_metrics").is_dir():
        out["citations"] = _scalar(con, f"""
            SELECT count(*), 100.0 * avg(is_cited::INT), sum(citations)::BIGINT,
                   median(citations), quantile_cont(citations, 0.90),
                   quantile_cont(citations, 0.99), max(citations)
            FROM read_parquet('{works}')""")
        out["world"] = _scalar(con, f"""
            SELECT count(*), avg(ncs), 100.0 * avg(is_top10::INT), 100.0 * avg(is_top1::INT)
            FROM read_parquet('{works}')
            WHERE publication_year BETWEEN 2014 AND 2023 AND bin_is_reliable""")
        out["recent"] = con.execute(f"""
            SELECT publication_year, count(*), count(*) FILTER (WHERE is_proceedings),
                   avg(citations), 100.0 * avg(is_cited::INT)
            FROM read_parquet('{works}')
            WHERE publication_year BETWEEN {time.gmtime().tm_year - 6}
                                       AND {time.gmtime().tm_year - 2}
            GROUP BY 1 ORDER BY 1""").fetchall()

    # Counted rather than scanned: works_attrs holds one row per work, so the share
    # of the corpus each identifier covers says how much of it a resolve can find.
    if config.table_dir("works_attrs").is_dir():
        attrs = config.table_glob("works_attrs")
        out["resolve"] = _scalar(con, f"""
            SELECT (SELECT count(*) FROM read_parquet('{attrs}')),
                   (SELECT count(*) FROM read_parquet('{config.table_glob("work_doi")}')),
                   (SELECT count(DISTINCT institution_id)
                    FROM read_parquet('{config.table_glob("work_institutions")}')),
                   (SELECT count(*)
                    FROM read_parquet('{config.table_glob("author_orcid")}')),
                   (SELECT count(*) FROM read_parquet('{attrs}') WHERE is_oa)""")

    out["sizes"] = {t: table_size(config.table_dir(t))
                    for t in config.STORE_TABLES + config.METRIC_TABLES
                    + config.RESOLVE_TABLES + config.DIM_TABLES
                    if config.table_dir(t).is_dir()}
    return out


def print_profile(p=None):
    """Render the profile as a block of plain text."""
    p = p or profile()
    if not p.get("corpus"):
        print("· Nothing built yet.")
        return

    rule = "─" * 70
    print(f"{rule}\n  OAM data profile"
          + (f"{'snapshot ' + p['snapshot']:>52}" if p["snapshot"] else ""))

    corpus, in_scope, xpack = p["corpus"], p["in_scope"], p["xpack"]
    print(f"\n  Corpus                  {corpus:>15,} works")
    print(f"    in scope              {in_scope:>15,}   {in_scope / corpus * 100:5.2f}%")
    print(f"    xpack                 {xpack:>15,}   {xpack / corpus * 100:5.2f}%"
          f"   excluded from the population")

    print("\n  Why works fall out of scope (criteria overlap, so these do not sum)")
    for label, n in p["funnel"]:
        print(f"    {label:<38} {n:>13,}   {n / corpus * 100:5.1f}%")

    if "bins" in p:
        n_bins, reliable, subfields, first, last = p["bins"]
        print(f"\n  Bins                    {n_bins:>15,}   "
              f"{reliable:,} reliable at >= {config.RELIABILITY_THRESHOLD} works")
        print(f"    subfields x years     {subfields:>15,}   x {first}-{last}")

    if "citations" in p:
        n, pct_cited, total, p50, p90, p99, mx = p["citations"]
        print(f"\n  Citations received      {total:>15,}   across {n:,} scored works")
        print(f"    cited at least once   {pct_cited:>14.1f}%")
        print(f"    median / p90 / p99    {f'{p50:,.0f} / {p90:,.0f} / {p99:,.0f}':>15}"
              f"   max {mx:,}")

    if "world" in p:
        n, mncs, pp10, pp1 = p["world"]
        print(f"\n  World baselines, 2014-2023, reliable bins ({n:,} works)")
        # No reliable bins at all means the averages are null, which happens on a
        # build limited to a few source files rather than the whole snapshot.
        if mncs is None:
            print("    none — no bin reached the reliability threshold")
        else:
            print(f"    MNCS {mncs:.4f}      PP(top 10%) {pp10:.2f}%"
                  f"      PP(top 1%) {pp1:.2f}%")

    if p.get("recent"):
        print(f"\n  {'Year':<8}{'works':>12}{'conference':>13}{'mean cites':>13}{'cited':>9}")
        for year, works, conf, mean, cited in p["recent"]:
            print(f"  {year:<8}{works:>12,}{conf:>13,}{mean:>13.1f}{cited:>8.1f}%")

    if p.get("resolve") and p["resolve"][0]:
        n, dois, institutions, orcids, oa = p["resolve"]
        print(f"\n  Resolve warehouse       {n:>15,} works, in scope or not")
        print(f"    with a DOI            {dois:>15,}   {dois / n * 100:5.1f}%")
        print(f"    open access           {oa:>15,}   {oa / n * 100:5.1f}%")
        print(f"    institutions          {institutions:>15,}   resolvable, "
              f"lineage-expanded")
        print(f"    ORCIDs                {orcids:>15,}")

    print(f"\n  {'Table':<18}{'on disk':>12}")
    for table, size in p["sizes"].items():
        print(f"  {table:<18}{human_bytes(size):>12}")
    print(rule)
