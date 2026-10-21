"""The per-paper scores, against values worked out from the fixture.

Journal cohort, 2014, 4-year citations 0, 1, 2, 3. Mean 1.5, so ncs is
citations / 1.5. The mid-rank percentile of those four values is 0.125, 0.375,
0.625, 0.875. quantile_cont at 0.90 is 2.7 and at 0.99 is 2.97, so only the
paper with 3 citations is in the top 10% and the top 1%.
"""

import math

import pytest

import oam

# work_id -> citations_4y, ncs, percentile, top10, top1, in_scope count, outside
_JOURNAL = {
    104: (0, 0.0, 0.125, False, False, 0, 0),
    103: (1, 1 / 1.5, 0.375, False, False, 1, 4),
    102: (2, 2 / 1.5, 0.625, False, False, 3, 7),
    101: (3, 3 / 1.5, 0.875, True, True, 4, 0),
}
# Conference cohort, citations 0 and 3. Mean 1.5. p90 is 2.7, p99 is 2.97.
_CONFERENCE = {
    202: (0, 0.0, 0.25, False, False),
    201: (3, 2.0, 0.75, True, True),
}


def _row(work_id):
    frame = oam.get_metrics([work_id]).set_index("work_id")
    return frame.loc[work_id]


def test_journal_cohort_scores():
    for work_id, (citations, ncs, percentile, top10, top1, inside, outside) in _JOURNAL.items():
        row = _row(work_id)
        assert row.citations_4y == citations
        assert row.ncs_4y == pytest.approx(ncs)
        assert row.citation_normalized_percentile_4y == pytest.approx(percentile)
        assert bool(row.is_in_top_10_percent_4y) is top10
        assert bool(row.is_in_top_1_percent_4y) is top1
        assert row.cited_by_count_in_scope == inside
        assert row.cited_by_count_outside_scope == outside
        assert bool(row.window_is_complete) is True
        assert row.cohort_n_works == 4
        assert bool(row.cohort_is_reliable) is True


def test_openalex_scores_pass_through():
    row = _row(101)
    assert row.openalex_fwci == pytest.approx(1.5)
    assert row.openalex_citation_normalized_percentile == pytest.approx(0.8)
    assert bool(row.openalex_is_in_top_10_percent) is True
    assert bool(row.openalex_is_in_top_1_percent) is False


def test_conference_cohort_scores():
    for work_id, (citations, ncs, percentile, top10, top1) in _CONFERENCE.items():
        row = _row(work_id)
        assert row.citations_4y == citations
        assert row.ncs_4y == pytest.approx(ncs)
        assert row.citation_normalized_percentile_4y == pytest.approx(percentile)
        assert bool(row.is_in_top_10_percent_4y) is top10
        assert bool(row.is_in_top_1_percent_4y) is top1
        assert row.cohort_n_works == 2
        assert bool(row.cohort_is_reliable) is False


def test_xpack_citation_does_not_count():
    # 901 cites 104, and 901 is xpack, so 104 stays uncited.
    assert _row(104).citations_4y == 0
    assert oam.get_metrics([901]).in_scope.iloc[0] == False  # noqa: E712


def test_self_citation_counts_in_scope_but_not_in_the_window():
    # 101 and 102 share an author, and 101 cites 102 in 2015.
    row = _row(102)
    assert row.cited_by_count_in_scope == 3
    assert row.citations_4y == 2


def test_window_keeps_year_plus_three_and_drops_the_year_after():
    # 101 is cited in 2014, 2016, 2017 and 2018. 2017 is publication year + 3.
    row = _row(101)
    assert row.citations_4y == 3
    assert row.cited_by_count_in_scope == 4

    # 201 is cited in 2018, 2019, 2020 and 2025. 2025 is outside. Mean of (3, 0) is 1.5.
    row = _row(201)
    assert row.citations_4y == 3
    assert row.ncs_4y == pytest.approx(3 / 1.5)
    assert row.cited_by_count_in_scope == 4


def test_an_uncited_cohort_flags_nobody():
    # Both papers have no citations, so the top-10% threshold is 0. Neither is flagged.
    for work_id in (301, 302):
        row = _row(work_id)
        assert row.citations_4y == 0
        assert math.isnan(row.ncs_4y)
        assert bool(row.is_in_top_10_percent_4y) is False
        assert bool(row.is_in_top_1_percent_4y) is False
        assert row.citation_normalized_percentile_4y == pytest.approx(0.5)
        assert bool(row.window_is_complete) is False
