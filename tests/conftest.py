"""A synthetic store, small enough to build the real metric tables in seconds.

Three cohorts, so the scores can be checked by hand. The default build counts
every citation, self-citations included. The snapshot year is 2026.

  2014, subfield 11, journal, four works, citations 4, 3, 1 and 0.
      mean 2. Reliable when the threshold is 3.
  2018, subfield 22, conference, two works, citations 4 and 0.
      mean 2. Not reliable at that threshold.
  2024, subfield 33, journal, two uncited works. The threshold is 0.

An xpack work cites one of them and must not count. One citation is a
self-citation. A work that fails the English rule is in the status table and
not in the metric population.
"""

import json

import duckdb
import pytest

import oam
from oam import config

# citing_id, cited_id, citing_year. The default build keeps every one of these.
_EDGES = [
    # 101 ends at 4 citations. 2018 is outside a 4-year window.
    (103, 101, 2014),
    (104, 101, 2016),
    (201, 101, 2017),
    (103, 101, 2018),
    # 102 ends at 3. The citation from 101 is a self-citation: they share an author.
    (103, 102, 2014),
    (104, 102, 2015),
    (101, 102, 2015),
    # 103 ends at 1.
    (104, 103, 2014),
    # 901 is xpack, so this citation of 104 must be dropped. 104 stays uncited.
    (901, 104, 2014),
    # 201 ends at 4. 2025 is outside a 4-year window.
    (202, 201, 2018),
    (202, 201, 2019),
    (202, 201, 2020),
    (202, 201, 2025),
]


def _copy(con, select, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Data directory with the store, the metric tables and the resolve tables."""
    data = tmp_path_factory.mktemp("oam-data")
    oam.set_data_dir(data)
    _write_store(data)
    threshold = config.RELIABILITY_THRESHOLD
    config.RELIABILITY_THRESHOLD = 3
    try:
        assert oam.build(steps="metrics"), "metric build failed its own checks"
    finally:
        config.RELIABILITY_THRESHOLD = threshold
    _write_resolve(data)
    return data


@pytest.fixture(autouse=True)
def _use_built(built):
    """Each test starts on the synthetic data, whatever the previous one did."""
    oam.set_data_dir(built)
    return built


def _write_store(data):
    criteria = list(config.SCOPE)
    # 401 fails English and so is out of scope. 901 is in scope but xpack, which
    # the metric build leaves out of the comparison. 101 and 102 share author 1.
    # cited_by_count is OpenAlex's own count: 101's is below the in-scope count,
    # so the outside-scope count is clamped to 0.
    rows = [
        # work_id, year, subfield, proceedings, institutions, authors,
        # cited_by_count, fwci, oa percentile, oa top10, oa top1, xpack, english
        (101, 2014, 11, False, 1, [1], 2, 1.5, 0.80, True, False, False, True),
        (102, 2014, 11, False, 2, [1], 10, 0.4, 0.20, False, False, False, True),
        (103, 2014, 11, False, 1, [3], 5, 0.9, 0.50, False, False, False, True),
        (104, 2014, 11, False, 4, [4], 0, 0.1, 0.10, False, False, False, True),
        (201, 2018, 22, True, 1, [5], 4, 2.0, 0.95, True, True, False, True),
        (202, 2018, 22, True, 5, [6], 1, 0.2, 0.30, False, False, False, True),
        (301, 2024, 33, False, 1, [7], 0, None, None, None, None, False, True),
        (302, 2024, 33, False, 1, [8], 0, None, None, None, None, False, True),
        (901, 2014, 11, False, 1, [9], 0, None, None, None, None, True, True),
        (401, 2014, 11, False, 1, [10], 0, None, None, None, None, False, False),
    ]
    # in_scope is every criterion, and English is the only one that varies.
    in_scope_ids = {r[0] for r in rows if r[12]}

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE works (
            work_id BIGINT, publication_year INTEGER, subfield INTEGER,
            is_proceedings BOOLEAN, institutions_distinct_count INTEGER,
            author_ids BIGINT[], cited_by_count INTEGER,
            openalex_fwci DOUBLE, openalex_citation_normalized_percentile DOUBLE,
            openalex_is_in_top_10_percent BOOLEAN, openalex_is_in_top_1_percent BOOLEAN,
            is_xpac BOOLEAN, is_english BOOLEAN
        )
    """)
    con.executemany("INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    other = ", ".join(f"TRUE AS {name}" for name in criteria if name != "is_english")
    _copy(con, f"""
        SELECT work_id, publication_year, is_english, {other},
               is_english AS in_scope, is_xpac
        FROM works
    """, config.table_dir("works_status") / "part-0.parquet")
    _copy(con, f"""
        SELECT work_id, publication_year, subfield, is_proceedings,
               TRUE AS is_core, institutions_distinct_count, author_ids,
               cited_by_count, openalex_fwci, openalex_citation_normalized_percentile,
               openalex_is_in_top_10_percent, openalex_is_in_top_1_percent,
               'article' AS work_type, is_xpac
        FROM works
        WHERE work_id IN ({", ".join(str(i) for i in sorted(in_scope_ids))})
    """, config.table_dir("works_scope") / "part-0.parquet")

    con.execute("CREATE TABLE edges (citing_id BIGINT, cited_id BIGINT, citing_year INTEGER)")
    con.executemany("INSERT INTO edges VALUES (?, ?, ?)", _EDGES)
    _copy(con, "SELECT citing_id, cited_id, citing_year FROM edges",
          config.table_dir("edges") / "part-0.parquet")

    state = {
        "date": "2026-09-23",
        "record_count": len(rows),
        "scope": config.scope_sql(),
    }
    config.STORE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.STORE_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _write_resolve(data):
    """The lookup tables, written directly: step 3 only sorts what step 1 extracted."""
    con = duckdb.connect()
    _copy(con, """
        SELECT * FROM (VALUES
            (101, 2014, 11, 'article', FALSE, 1, 'gold', TRUE, TRUE, FALSE),
            (102, 2014, 11, 'article', FALSE, 1, 'closed', FALSE, TRUE, FALSE),
            (103, 2014, 11, 'article', FALSE, 1, 'green', TRUE, TRUE, FALSE),
            (104, 2014, 11, 'article', FALSE, 1, 'closed', FALSE, TRUE, FALSE),
            (201, 2018, 22, 'article', TRUE,  2, 'hybrid', TRUE, TRUE, FALSE),
            (202, 2018, 22, 'article', TRUE,  2, 'bronze', TRUE, TRUE, FALSE),
            (301, 2024, 33, 'article', FALSE, 1, 'closed', FALSE, TRUE, FALSE),
            (302, 2024, 33, 'article', FALSE, 1, 'closed', FALSE, TRUE, FALSE),
            (901, 2014, 11, 'article', FALSE, 1, 'closed', FALSE, TRUE, TRUE),
            (401, 2014, 11, 'article', FALSE, 1, 'closed', FALSE, FALSE, FALSE)
        ) t(work_id, publication_year, subfield, work_type, is_proceedings,
            source_id, oa_status, is_oa, in_scope, is_xpac)
        ORDER BY publication_year, subfield, work_id
    """, config.table_dir("works_attrs") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (5001, 101, TRUE, TRUE),
            (5001, 102, TRUE, FALSE),
            (5002, 103, TRUE, FALSE),
            (5001, 201, TRUE, TRUE)
        ) t(institution_id, work_id, is_direct, is_corresponding)
        ORDER BY institution_id, work_id
    """, config.table_dir("work_institutions") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (7001, 101, TRUE)
        ) t(author_id, work_id, is_corresponding)
        ORDER BY author_id, work_id
    """, config.table_dir("work_authors") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES (8001, 101)) t(funder_id, work_id) ORDER BY 1, 2
    """, config.table_dir("work_funders") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES ('10.1000/oam-101', 101)) t(doi, work_id) ORDER BY 1
    """, config.table_dir("work_doi") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES ('0000-0001-0000-0001', 7001)) t(orcid, author_id) ORDER BY 1
    """, config.table_dir("author_orcid") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (11, 'Artificial Intelligence', 17, 'Computer science', 3, 'Physical sciences'),
            (22, 'Algebra', 26, 'Mathematics', 2, 'Formal sciences')
        ) t(subfield_id, subfield_name, field_id, field_name, domain_id, domain_name)
        ORDER BY subfield_id
    """, config.table_dir("dim_topics") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (5001, 'EPFL', 'CH', 'education', 'https://ror.org/02s376052'),
            (5002, 'CNRS', 'FR', 'government', 'https://ror.org/02feahw73'),
            (5003, 'Université de Neuchâtel', 'CH', 'education', NULL)
        ) t(institution_id, display_name, country_code, type, ror)
        ORDER BY institution_id
    """, config.table_dir("dim_institutions") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (1, 'Nature', '0028-0836', 'journal', 'GB', TRUE),
            (2, 'NeurIPS', NULL, 'conference', 'US', FALSE),
            (3, 'Nature Communications', NULL, 'journal', 'GB', TRUE),
            (4, 'Journal of Nature', NULL, 'journal', 'GB', TRUE)
        ) t(source_id, display_name, issn_l, type, country_code, is_core)
        ORDER BY source_id
    """, config.table_dir("dim_sources") / "part-0.parquet")
    _copy(con, """
        SELECT * FROM (VALUES
            (8001, 'Swiss National Science Foundation', 'CH')
        ) t(funder_id, display_name, country_code)
        ORDER BY funder_id
    """, config.table_dir("dim_funders") / "part-0.parquet")
