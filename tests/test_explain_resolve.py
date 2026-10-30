"""Scope explanations, and resolving a filter down to work ids."""

import pytest

import oam


def test_an_edited_scope_is_what_the_store_builds():
    from oam.store import selects
    assert "is_english" in selects()["works_status"]
    removed = oam.config.SCOPE.pop("is_english")
    try:
        assert "is_english" not in selects()["works_status"]
    finally:
        oam.config.SCOPE["is_english"] = removed


def test_explain_names_the_failed_rule():
    why = oam.explain([101, 401, 999]).set_index("work_id")
    assert bool(why.loc[101, "found"]) is True
    assert bool(why.loc[101, "in_scope"]) is True
    assert list(why.loc[101, "failed_criteria"]) == []

    assert bool(why.loc[401, "in_scope"]) is False
    assert list(why.loc[401, "failed_criteria"]) == ["is_english"]

    assert bool(why.loc[999, "found"]) is False
    assert why.loc[999, "failed_criteria"] is None or (
        hasattr(why.loc[999, "failed_criteria"], "__len__") is False
    )


def test_out_of_scope_work_is_reported_but_not_scored():
    row = oam.get_metrics([401]).iloc[0]
    assert bool(row.scored) is False
    assert row.ncs != row.ncs  # NaN


def test_explain_on_get_metrics_shows_why_a_score_is_missing():
    rows = oam.get_metrics([101, 401, 901], explain=True).set_index("work_id")
    assert bool(rows.loc[101, "scored"]) is True
    assert bool(rows.loc[101, "in_scope"]) is True
    assert bool(rows.loc[101, "is_xpack"]) is False
    assert list(rows.loc[101, "failed_criteria"]) == []

    assert bool(rows.loc[401, "scored"]) is False
    assert bool(rows.loc[401, "in_scope"]) is False
    assert list(rows.loc[401, "failed_criteria"]) == ["is_english"]

    assert bool(rows.loc[901, "scored"]) is False
    assert bool(rows.loc[901, "in_scope"]) is True
    assert bool(rows.loc[901, "is_xpack"]) is True
    assert "is_xpack" in oam.explain([901]).columns
    assert "is_xpac" not in oam.explain([901]).columns


def test_resolve_institution_country_and_year():
    assert oam.resolve(institution="I5001") == [101, 102, 201]
    assert oam.resolve(institution="I5001", corresponding=True) == [101, 201]
    assert oam.resolve(country="ch", year=2014) == [101, 102]
    assert oam.resolve(country="CH", funder="F8001", year=2014) == [101]


def test_resolve_doi_orcid_and_field():
    assert oam.resolve(doi=["https://doi.org/10.1000/oam-101", "10.1000/missing"]) == [101]
    assert oam.resolve(doi="https://dx.doi.org/10.1000/OAM-101") == [101]
    with pytest.raises(ValueError, match="doi="):
        oam.resolve(doi=["10.1000/oam-101"], year=2014)
    assert oam.resolve(orcid="https://orcid.org/0000-0001-0000-0001") == [101]
    # Field 17 is Computer science, which contains subfield 11. 401 is out of
    # scope and 901 is xpack; resolve returns both, scoring does not.
    assert oam.resolve(field=17, year=2014) == [101, 102, 103, 104, 401, 901]
    assert oam.resolve(proceedings=True, year=2018) == [201, 202]


def test_wrong_letter_and_unknown_argument():
    with pytest.raises(ValueError, match="W101"):
        oam.resolve(institution="W101")
    with pytest.raises(TypeError, match="topic"):
        oam.resolve(topic="algebra")


def test_a_bad_filter_is_rejected():
    with pytest.raises(ValueError, match="country_code"):
        oam.resolve(country="Switzerland")
    with pytest.raises(ValueError, match="oa_status"):
        oam.resolve(oa_status="open")
    with pytest.raises(ValueError, match="work_type"):
        oam.resolve(work_type="paper")
    with pytest.raises(ValueError, match="2014-2023"):
        oam.resolve(year="recent")
    with pytest.raises(ValueError, match="starts after"):
        oam.resolve(year=(2024, 2020))
    assert oam.resolve(country="ch", year=2014) == [101, 102]
    assert oam.resolve(oa_status="Gold") == [101]
    assert oam.resolve(work_type="article", institution="I5001") == [101, 102, 201]


def test_a_missing_doi_is_reported(capsys):
    assert oam.resolve(doi=["10.1000/oam-101", "10.1000/missing", "10.1000/also"]) == [101]
    assert "· 2 of 3 DOIs are not in the snapshot" in capsys.readouterr().out


def test_resolve_dois_lines_up_with_the_input():
    table = oam.resolve_dois(["10.1000/missing", "https://doi.org/10.1000/OAM-101",
                              "10.1000/oam-101", ""])
    assert list(table.doi) == ["10.1000/missing", "https://doi.org/10.1000/OAM-101"]
    assert table.work_id.isna().tolist() == [True, False]
    assert table.work_id.iloc[1] == 101
    assert oam.resolve_dois([]).empty


def test_a_doi_lookup_leaves_the_connection_usable():
    assert oam.resolve(doi="10.1000/oam-101") == [101]
    table = oam.aggregate([101], by=None)
    assert int(table.P_full.iloc[0]) == 1


def test_a_year_list_is_those_years_not_a_range():
    # 201 sits in 2018, between 2014 and 2024, so a range includes it and a list does not.
    assert oam.resolve(institution="I5001", year=[2014, 2024]) == [101, 102]
    assert oam.resolve(institution="I5001", year=(2014, 2024)) == [101, 102, 201]
    assert oam.resolve(institution="I5001", year=[2014, 2018, 2024]) == [101, 102, 201]


def test_dimension_search_folds_accents_and_ranks_exact_names():
    found = oam.dimension("institutions", search="Neuchatel")
    assert list(found.institution_id) == [5003]
    sources = oam.dimension("sources", search="Nature")
    assert list(sources.source_id) == [1, 3, 4]


def test_dimension_looks_up_both_ways():
    name = oam.dimension("institutions", ids="I5001")
    assert name.display_name.iloc[0] == "EPFL"
    found = oam.dimension("institutions", search="lausanne")
    assert found.empty
    found = oam.dimension("funders", search="Swiss National")
    assert list(found.funder_id) == [8001]
    topics = oam.dimension("topics")
    assert set(topics.subfield_id) == {11, 22}
