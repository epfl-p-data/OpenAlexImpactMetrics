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
GROUPABLE = ("publication_year", "subfield", "is_proceedings", "n_institutions",
             "decile", "is_top10", "is_top1", "is_cited", "bin_is_reliable")


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


def _window_columns(window):
    """Which materialized columns back citations/ncs/is_top10/is_top1."""
    if window is None:
        return "citations", "ncs", "is_top10", "is_top1"
    if window != config.WINDOW_YEARS:
        raise ValueError(
            f"window={window} is not materialized; the build stores an open window "
            f"and a {config.WINDOW_YEARS}-year window"
        )
    w = config.WINDOW_YEARS
    return f"citations_{w}y", f"ncs_{w}y", f"is_top10_{w}y", f"is_top1_{w}y"


def get_metrics(ids, window=None, explain=False):
    """Per-work metrics, one row per distinct input id.

    Ids that are not in the metric population come back with in_scope = False and
    null metrics rather than being dropped, so the result always lines up with the
    input. Pass explain=True to also learn why: it adds `found` and one column per scope
    criterion, at the cost of scanning works_status (510M rows) instead of only
    work_metrics.

    window=3 swaps in the fixed 3-year citation window everywhere.
    """
    require_tables(("work_metrics",), "get_metrics()")
    if explain:
        require_tables(("works_status",), "get_metrics(explain=True)")
    citations, ncs, top10, top1 = _window_columns(window)
    con = connection()
    _register(con, ids)

    select = f"""
        SELECT
            i.work_id,
            m.work_id IS NOT NULL   AS in_scope,
            m.publication_year,
            m.subfield,
            m.is_proceedings,
            m.n_institutions,
            m.{citations}           AS citations,
            m.{ncs}                 AS ncs,
            m.percentile,
            m.decile,
            m.{top10}               AS is_top10,
            m.{top1}                AS is_top1,
            m.bin_n_works,
            m.bin_is_reliable
        FROM _ids i
        LEFT JOIN read_parquet('{config.table_glob("work_metrics")}') m
               ON m.work_id = i.work_id
    """
    metrics = con.execute(select).fetchdf()
    if not explain:
        return metrics

    return metrics.merge(
        _criteria(con)[["work_id", "found"] + list(_scope_criteria())],
        on="work_id", how="left",
    )


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
            s.is_xpac,
            CASE WHEN s.work_id IS NULL THEN NULL ELSE list_filter([
                {failed}
            ], x -> x IS NOT NULL) END AS failed_criteria
        FROM _ids i
        LEFT JOIN read_parquet('{config.table_glob("works_status")}') s
               ON s.work_id = i.work_id
    """).fetchdf()


def aggregate(ids, by="publication_year", counting="full", window=None,
              reliable_only=True):
    """Citation-impact indicators for a set of works.

    by          column name, list of column names, or None for a single row
    counting    "full" gives every work weight 1; "frac" weights it 1/n_institutions,
                so a work shared with nine other institutions counts as a tenth
    window      None for the open citation window, 3 for the fixed 3-year one
    reliable_only
                keep only works whose bin has at least
                config.RELIABILITY_THRESHOLD works; small bins give unstable means

    Returns P (works), P_weighted, TCS, MCS, MNCS, PP_top10 and PP_top1 per group.
    Ids that are absent or out of scope contribute nothing; compare P against the
    number of ids you passed to see the coverage.
    """
    require_tables(("work_metrics",), "aggregate()")
    if counting not in ("full", "frac"):
        raise ValueError("counting must be 'full' or 'frac'")

    groups = [by] if isinstance(by, str) else list(by or [])
    unknown = [g for g in groups if g not in GROUPABLE]
    if unknown:
        raise ValueError(f"cannot group by {unknown}; choose from {list(GROUPABLE)}")

    citations, ncs, top10, top1 = _window_columns(window)
    weight = "1.0" if counting == "full" else "1.0 / nullif(n_institutions, 0)"
    filters = ["m.work_id IS NOT NULL"]
    if reliable_only:
        filters.append("m.bin_is_reliable")
    if counting == "frac":
        filters.append("m.n_institutions > 0")

    selected = "".join(f"{g}, " for g in groups)
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
            count(*)                                   AS P,
            sum(w)                                     AS P_weighted,
            sum({citations} * w)                       AS TCS,
            sum({citations} * w) / sum(w)              AS MCS,
            sum({ncs} * w) / sum(w)                    AS MNCS,
            100.0 * sum({top10}::INT * w) / sum(w)     AS PP_top10,
            100.0 * sum({top1}::INT * w) / sum(w)      AS PP_top1
        FROM works
        {grouping}
        {ordering}
    """).fetchdf()
