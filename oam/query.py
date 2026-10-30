"""Answer questions about a set of works: get_metrics, aggregate, explain.

All three take an OpenAlex work id — 2741809807, "W2741809807" or the full
URL — and read the downloaded or built Parquet. Nothing here hits the network.
DOIs are not work ids; resolve them with oam.resolve(doi=[...]) first.
"""

import re

import pandas as pd

from . import config
from .duck import read_connection as connection
from .duck import require_tables

# A work id, and nothing else. A DOI also ends in digits, and treating it as a work
# id would silently score a different paper.
_WORK_ID = re.compile(r"(?:https?://openalex\.org/)?[Ww](\d+)\s*$|^(\d+)\s*$")

# Columns a caller may group by. Restricted because the value is interpolated into
# SQL, and because grouping by anything else is rarely meaningful.
GROUPABLE = ("publication_year", "subfield", "is_proceedings",
             "institutions_distinct_count", "is_in_top_10_percent",
             "is_in_top_1_percent", "cohort_is_reliable")


def work_ids(ids):
    """Normalise OpenAlex work ids to a de-duplicated list of ints, order kept.

    Accepts ints, whole floats (Parquet sometimes stores ids as DOUBLE),
    "W2741809807", "w2741809807" and "https://openalex.org/W2741809807".
    Blanks and nulls are dropped. Anything else raises, including a DOI or an
    institution id: both end in digits and would otherwise score the wrong paper.
    """
    out, seen = [], set()
    for raw in ids:
        if raw is None or (isinstance(raw, float) and raw != raw):
            continue
        if isinstance(raw, str) and not raw.strip():
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            value = _parse_work_id(raw)
        elif isinstance(raw, float) and not raw.is_integer():
            raise ValueError(f"not an OpenAlex work id: {raw!r}")
        else:
            value = int(raw)
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_work_id(raw):
    text = str(raw).strip()
    match = _WORK_ID.fullmatch(text)
    if not match:
        hint = ""
        low = text.lower()
        if low.startswith(("10.", "doi:")) or "doi.org" in low:
            hint = ". A DOI is not a work id; use oam.resolve(doi=[...])"
        raise ValueError(f"not an OpenAlex work id: {raw!r}{hint}")
    return int(match.group(1) or match.group(2))


def _register(con, ids):
    con.register("_ids", pd.DataFrame({"work_id": pd.array(work_ids(ids), dtype="int64")}))


def get_metrics(ids, explain=False):
    """Per-work metrics, one row per distinct input id.

    Ids that are not in the metric population come back with scored = False and
    null metrics rather than being dropped, so the result always lines up with the
    input. Pass explain=True to also learn why: it adds whether the work was found,
    whether it passed the scope rules, whether it is an xpack record, which rules
    failed, and one column per scope criterion. That scans works_status as well as
    work_metrics. A paper in scope is not scored when it is an xpack record, or
    when it has no publication year.
    """
    require_tables(("work_metrics",), "get_metrics()")
    if explain:
        require_tables(("works_status",), "get_metrics(explain=True)")
    con = connection()
    _register(con, ids)

    select = f"""
        SELECT
            i.work_id,
            m.work_id IS NOT NULL AS scored,
            m.publication_year,
            m.subfield,
            m.is_proceedings,
            m.institutions_distinct_count,
            m.citations,
            m.ncs,
            m.citation_normalized_percentile,
            m.is_in_top_10_percent,
            m.is_in_top_1_percent,
            m.openalex_fwci,
            m.openalex_citation_normalized_percentile,
            m.openalex_is_in_top_10_percent,
            m.openalex_is_in_top_1_percent,
            m.cohort_n_works,
            m.cohort_is_reliable
        FROM _ids i
        LEFT JOIN read_parquet('{config.table_glob("work_metrics")}') m
               ON m.work_id = i.work_id
    """
    metrics = con.execute(select).fetchdf()
    if not explain:
        return metrics

    why = _criteria(con)
    columns = (["work_id", "found", "in_scope", "is_xpack", "failed_criteria"]
               + list(_scope_criteria()))
    return metrics.merge(why[columns], on="work_id", how="left")


def explain(ids):
    """Why each id is or isn't in scope: found, each criterion, and what failed."""
    require_tables(("works_status",), "explain()")
    if not config.STORE_STATE_PATH.is_file():
        raise RuntimeError(
            f"explain() needs {config.STORE_STATE_PATH} to know which scope was "
            f"built. It is part of oam.download()."
        )
    con = connection()
    _register(con, ids)
    return _criteria(con)


def _scope_criteria():
    """The criteria works_status actually holds, as built rather than as configured."""
    return tuple(config.built_scope())


def _criteria(con):
    """Join the already-registered _ids against works_status. Assumes _register."""
    criteria = _scope_criteria()
    failed = ",\n                ".join(
        f"CASE WHEN NOT COALESCE(s.{c}, FALSE) THEN '{c}' END" for c in criteria
    )
    return con.execute(f"""
        SELECT
            i.work_id,
            s.work_id IS NOT NULL AS found,
            COALESCE(s.in_scope, FALSE) AS in_scope,
            s.publication_year,
            {", ".join(f"s.{c}" for c in criteria)},
            s.is_xpac AS is_xpack,
            CASE WHEN s.work_id IS NULL THEN NULL ELSE list_filter([
                {failed}
            ], lambda x: x IS NOT NULL) END AS failed_criteria
        FROM _ids i
        LEFT JOIN read_parquet('{config.table_glob("works_status")}') s
               ON s.work_id = i.work_id
    """).fetchdf()


def aggregate(ids, by="publication_year", counting="full", reliable_only=True):
    """Citation-impact indicators for a set of works.

    by          column name, list of column names, or None for a single row
    counting    "full" gives every work weight 1; "frac" weights it by
                1/institutions_distinct_count, so a work shared with nine other
                institutions counts as a tenth
    reliable_only
                keep only works whose cohort has at least
                config.RELIABILITY_THRESHOLD works; small cohorts give unstable means

    Every column is named for the counting, and P_full is always the number of
    works. The scores are whatever the data was built with: by default every
    citation up to the snapshot, self-citations included. Ids that are absent or
    out of scope contribute nothing; compare P_full against the number of ids
    you passed to see the coverage.
    """
    require_tables(("work_metrics",), "aggregate()")
    if counting not in ("full", "frac"):
        raise ValueError("counting must be 'full' or 'frac'")

    groups = [by] if isinstance(by, str) else list(by or [])
    unknown = [g for g in groups if g not in GROUPABLE]
    if unknown:
        raise ValueError(f"cannot group by {unknown}; choose from {list(GROUPABLE)}")

    weight = "1.0" if counting == "full" else "1.0 / nullif(institutions_distinct_count, 0)"
    filters = ["m.work_id IS NOT NULL"]
    if reliable_only:
        filters.append("m.cohort_is_reliable")
    if counting == "frac":
        filters.append("m.institutions_distinct_count > 0")

    selected = "".join(f"{g}, " for g in groups)
    weighted = "sum(w) AS P_frac," if counting == "frac" else ""
    grouping = f"GROUP BY {', '.join(str(i + 1) for i in range(len(groups)))}" if groups else ""
    ordering = f"ORDER BY {', '.join(str(i + 1) for i in range(len(groups)))}" if groups else ""

    con = connection()
    _register(con, ids)
    return con.execute(f"""
        WITH works AS (
            SELECT m.*, {weight} AS w
            FROM _ids i
            JOIN read_parquet('{config.table_glob("work_metrics")}') m
              ON m.work_id = i.work_id
            WHERE {' AND '.join(filters)}
        )
        SELECT
            {selected}
            count(*) AS P_full,
            {weighted}
            sum(citations * w) AS TCS_{counting},
            sum(citations * w) / sum(w) AS MCS_{counting},
            sum(ncs * w) / sum(w) AS MNCS_{counting},
            100.0 * sum(is_in_top_10_percent::INT * w) / sum(w) AS PP_top10_{counting},
            100.0 * sum(is_in_top_1_percent::INT * w) / sum(w) AS PP_top1_{counting}
        FROM works
        {grouping}
        {ordering}
    """).fetchdf()
