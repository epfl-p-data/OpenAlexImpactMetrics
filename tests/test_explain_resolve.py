"""Scope explanations, and resolving a filter down to work ids."""

import pytest

import oam


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
    assert bool(row.in_scope) is False
    assert row.ncs != row.ncs  # NaN


def test_resolve_institution_country_and_year():
    assert oam.resolve(institution="I5001") == [101, 102, 201]
    assert oam.resolve(institution="I5001", corresponding=True) == [101, 201]
    assert oam.resolve(country="ch", year=2014) == [101, 102]
    assert oam.resolve(country="CH", funder="F8001", year=2014) == [101]


def test_resolve_doi_orcid_and_field():
    assert oam.resolve(doi=["https://doi.org/10.1000/oam-101", "10.1000/missing"]) == [101]
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


def test_dimension_looks_up_both_ways():
    name = oam.dimension("institutions", ids="I5001")
    assert name.display_name.iloc[0] == "EPFL"
    found = oam.dimension("institutions", search="lausanne")
    assert found.empty
    found = oam.dimension("funders", search="Swiss National")
    assert list(found.funder_id) == [8001]
    topics = oam.dimension("topics")
    assert set(topics.subfield_id) == {11, 22}
