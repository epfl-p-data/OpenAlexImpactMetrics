"""Invariants the built data must satisfy.

Run at the end of every build, and standalone with ``oam.check()``. Each check is a
scalar query plus the rule it must obey, and each one exists because it catches a
failure the others miss:

  * a row count matching the manifest proves nothing about the contents, so the id
    parse and scope rate are checked separately — if OpenAlex changed the id format
    or dropped a column, every count would still line up while every value was wrong
  * MNCS averaging to 1.0 is insensitive to duplicated works, since a duplicate is
    normalised the same way twice, so uniqueness is checked directly
  * conversely the duplicate and range checks say nothing about whether the
    citations survived stages 1 to 3, which is what the citation totals compare
  * and none of them would notice the resolve warehouse losing the sort order every
    lookup depends on, which stays correct while becoming slow, so the row-group
    boundaries are read out of the Parquet footers and checked

The fast battery reads only column statistics and the two 62M-row tables (~3s).
--deep adds a re-scan of all 2.16B edges (~21s) to re-derive the citation totals
from scratch rather than trusting the stored counts.
"""

import json

from . import config
from .duck import connect, table_size

# Works with no publication year cannot be binned, so they are not scored. They can
# still cast citations, which is why the citing side of the edge list is not filtered
# on year and the deep check must not filter it either.
_SCORED = "NOT is_xpac AND publication_year IS NOT NULL"


def _universe(include_xpack):
    return "TRUE" if include_xpack else "NOT is_xpac"


def _checks(include_xpack, deep):
    status, scope = config.table_glob("works_status"), config.table_glob("works_scope")
    counts, bins = config.table_glob("citation_counts"), config.table_glob("bin_stats")
    works = config.table_glob("work_metrics")
    scored = f"{_universe(include_xpack)} AND publication_year IS NOT NULL"

    snapshot = config.STORE_STATE_PATH
    expected = json.loads(snapshot.read_text()).get("record_count") if snapshot.exists() else None

    # The last element is the tables a check reads. A downloaded bundle does not
    # include the build-only ones, and those checks are skipped rather than failed.
    status_tables = ("works_status",)
    scope_tables = ("works_scope",)
    metrics_tables = ("work_metrics",)
    built_tables = ("work_metrics", "works_scope", "citation_counts")

    checks = []
    if expected:
        checks.append((
            "works_status  every work in the snapshot is present",
            f"SELECT count(*) - {expected} FROM read_parquet('{status}')",
            lambda v: v == 0, "rows vs the manifest record_count", status_tables))

    checks += [
        ("works_status  every OpenAlex id parsed",
         f"SELECT count(*) FROM read_parquet('{status}') WHERE work_id IS NULL",
         lambda v: v == 0, "NULL ids mean the id format changed and substr() missed the number",
         status_tables),

        # Deliberately not a band: the rate is a property of whatever scope is
        # configured, so only a collapse to zero is wrong for every scope.
        ("works_status  scope admits something (%)",
         f"SELECT round(100.0 * avg(in_scope::INT), 2) FROM read_parquet('{status}')",
         lambda v: v > 0, "a zero rate means a criterion rejected the entire corpus",
         status_tables),

        ("works_scope   no duplicate works",
         f"SELECT count(*) - count(DISTINCT work_id) FROM read_parquet('{scope}')",
         lambda v: v == 0, "duplicates survive the row-count and MNCS checks",
         scope_tables),

        ("works_scope   every work has a subfield",
         f"SELECT count(*) FROM read_parquet('{scope}') WHERE subfield IS NULL",
         lambda v: v == 0, "subfield is a bin dimension and is a scope criterion",
         scope_tables),

        ("bin_stats     statistics are ordered",
         f"SELECT count(*) FROM read_parquet('{bins}') "
         f"WHERE n_works <= 0 OR median_citations > p90 OR p90 > p99 OR mean_citations < 0",
         lambda v: v == 0, "median <= p90 <= p99 must hold in every bin",
         ("bin_stats",)),

        ("work_metrics  one row per scored work",
         f"SELECT (SELECT count(*) FROM read_parquet('{works}')) "
         f"- (SELECT count(*) FROM read_parquet('{scope}') WHERE {scored})",
         lambda v: v == 0, "the bin join must not drop or duplicate anyone",
         ("work_metrics", "works_scope")),

        ("work_metrics  no duplicate works",
         f"SELECT count(*) - count(DISTINCT work_id) FROM read_parquet('{works}')",
         lambda v: v == 0, "a duplicate would be double counted in every aggregate",
         metrics_tables),

        ("work_metrics  world MNCS is 1.0",
         f"SELECT avg(ncs) FROM read_parquet('{works}')",
         lambda v: abs(v - 1.0) <= 0.001, "a mean of ratios to bin means can only be 1",
         metrics_tables),

        ("work_metrics  percentile and decile in range",
         f"SELECT count(*) FROM read_parquet('{works}') "
         f"WHERE percentile <= 0 OR percentile > 1 OR decile NOT BETWEEN 1 AND 10",
         lambda v: v == 0, "CUME_DIST is (0,1] and NTILE(10) is 1..10",
         metrics_tables),

        ("work_metrics  scored works in reliable bins have an ncs",
         f"SELECT count(*) FROM read_parquet('{works}') WHERE bin_is_reliable AND ncs IS NULL",
         lambda v: v == 0, "a reliable bin has a positive mean, so ncs must exist",
         metrics_tables),

        ("citations     stage 3 kept every counted citation",
         f"SELECT (SELECT sum(citations) FROM read_parquet('{works}')) - "
         f"(SELECT sum(c.citations) FROM read_parquet('{counts}') c "
         f" JOIN read_parquet('{scope}') s ON s.work_id = c.work_id WHERE {scored})",
         lambda v: v == 0, "the left join onto the counts must lose nothing",
         built_tables),
    ]

    checks += _resolve_checks()

    if deep:
        citing = _universe(include_xpack)
        checks.append((
            "citations     agree with a full re-scan of the edge list",
            f"SELECT (SELECT sum(citations) FROM read_parquet('{works}')) - ("
            f"  WITH cited AS (SELECT work_id FROM read_parquet('{scope}') WHERE {scored}),"
            f"       citer AS (SELECT work_id FROM read_parquet('{scope}') WHERE {citing})"
            f"  SELECT count(*) FROM read_parquet('{config.table_glob('edges')}') e"
            f"  JOIN cited t ON t.work_id = e.cited_id JOIN citer s ON s.work_id = e.citing_id)",
            lambda v: v == 0, "re-derives the counts from 2.16B edges instead of trusting them",
            ("work_metrics", "works_scope", "edges")))

    return checks


# ---------------------------------------------------------------------------
# The resolve warehouse
# ---------------------------------------------------------------------------
# Only checked when it is built, since it is an optional layer. Two of the four are
# counts, which catch a table that lost or gained rows on the way from step 1 through
# the sort in step 3. The other two are about the layout rather than the contents: a
# lookup is fast only because the table is sorted on its key, and that is a property
# no count would notice going wrong.

def _sorted_key_sql(table, column):
    """Row-group boundaries where the sort key goes backwards.

    Read from the Parquet footers rather than the data, so this costs nothing: every
    row group records the min and max of each column, and a sorted file is one where
    no group starts below where the previous one ended. That is exactly the property
    a lookup relies on to skip row groups, so it is worth testing directly.
    """
    return f"""
        SELECT count(*) FROM (
            SELECT stats_min AS low,
                   lag(stats_max) OVER (ORDER BY file_name, row_group_id) AS previous_high
            FROM parquet_metadata('{config.table_glob(table)}')
            WHERE path_in_schema = '{column}'
        ) WHERE previous_high > low
    """


def _resolve_checks():
    from . import resolve_store  # imported here: the warehouse is optional

    if not resolve_store.is_built():
        return []

    status, scope = config.table_glob("works_status"), config.table_glob("works_scope")
    attrs = config.table_glob("works_attrs")
    keys = resolve_store.SORT_KEY

    out_of_order = "\n        UNION ALL\n".join(
        _sorted_key_sql(table, column) for table, column in keys.items())
    # works_attrs is excluded: it is keyed on the publication year, which 0.03% of
    # works legitimately lack. The others are keyed on an identifier, and a row
    # without one is a row nothing can ever match.
    null_keys = "\n        UNION ALL\n".join(
        f"SELECT count(*) FROM read_parquet('{config.table_glob(table)}') "
        f"WHERE {column} IS NULL"
        for table, column in keys.items() if table != "works_attrs")

    return [
        ("works_attrs   every work in the corpus is present",
         f"SELECT (SELECT count(*) FROM read_parquet('{attrs}')) "
         f"- (SELECT count(*) FROM read_parquet('{status}'))",
         lambda v: v == 0, "a resolve must be able to return any work the API would",
         ("works_attrs", "works_status")),

        ("works_attrs   in_scope agrees with works_scope",
         f"SELECT (SELECT count(*) FROM read_parquet('{attrs}') WHERE in_scope) "
         f"- (SELECT count(*) FROM read_parquet('{scope}'))",
         lambda v: v == 0, "both come from the same pass and cannot disagree",
         ("works_attrs", "works_scope")),

        ("resolve       every lookup key is sorted",
         f"SELECT (SELECT sum(c) FROM ({out_of_order}) t(c))",
         lambda v: v == 0,
         "an unsorted table still answers correctly, but reads whole instead of "
         "skipping row groups",
         tuple(keys)),

        ("resolve       ids are never null",
         f"SELECT (SELECT sum(c) FROM ({null_keys}) t(c))",
         lambda v: v == 0, "step 1 filters these out, so a null means it did not",
         tuple(k for k in keys if k != "works_attrs")),
    ]


def _evaluate(battery):
    """Run a list of checks, as (name, passed, value, why). Does not print."""
    con = connect(config.DATA_DIR / "_tmp")
    results = []
    for name, sql, passes, why in battery:
        value = con.execute(sql).fetchone()[0]
        results.append((name, value is not None and passes(value), value, why))
    return results


def _render(results, label=""):
    """Print a line per check and return True if all of them passed."""
    failed = [r for r in results if not r[1]]
    print(f"▶ Checks{label}")
    for name, ok, value, why in results:
        shown = f"{value:.4f}" if isinstance(value, float) else f"{value:,}"
        print(f"  {'✓' if ok else '✗'} {name:<54} {shown:>14}")
        if not ok:
            print(f"      {why}")
    if failed:
        print(f"\n✗ {len(failed)} of {len(results)} checks failed.")
        return False
    print(f"\n✓ All {len(results)} checks passed.")
    return True


def _present(tables):
    return all(table_size(config.table_dir(t)) > 0 for t in tables)


def _include_xpack(include_xpack):
    if include_xpack is not None:
        return include_xpack
    path = config.METRICS_DIR / "_state" / "stages.json"
    if not path.is_file():
        return False
    recorded = json.loads(path.read_text())
    return next(iter(recorded.values()), {}).get("config", {}).get("include_xpack", False)


def run(deep=False, include_xpack=None):
    """Every check that has its tables, as (name, passed, value, why). Does not print."""
    include_xpack = _include_xpack(include_xpack)
    runnable = [item[:4] for item in _checks(include_xpack, deep) if _present(item[4])]
    return _evaluate(runnable)


def report(deep=False, include_xpack=None):
    """Run everything that can run, print a line each, and return True if all passed.

    Checks whose tables are absent — a downloaded bundle has no edge list — are
    skipped and named, not failed.
    """
    include_xpack = _include_xpack(include_xpack)
    battery = _checks(include_xpack, deep)
    runnable = [item[:4] for item in battery if _present(item[4])]
    skipped = [item[0] for item in battery if not _present(item[4])]
    if not runnable:
        print(f"✗ nothing to check in {config.DATA_DIR}.")
        print("  oam.download() installs the scoring tables; oam.build() builds everything.")
        return False

    ok = _render(_evaluate(runnable), " (deep)" if deep else "")
    if skipped:
        print(f"  {len(skipped)} checks skipped; they need tables a download does not include.")
    return ok


def report_resolve():
    """Just the warehouse invariants, for the end of build step 3.

    Step 3 runs after the battery at the end of step 2, so without this the layer it
    just built would go unverified until the next ``oam.check()``.
    """
    battery = _resolve_checks()
    if not battery:
        print("✗ resolve warehouse is not built — nothing to check.")
        return False
    return _render(_evaluate([item[:4] for item in battery]), " (resolve warehouse)")
