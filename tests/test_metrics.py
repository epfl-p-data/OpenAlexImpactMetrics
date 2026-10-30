"""The per-paper scores, against values worked out from the fixture.

The default build counts every citation, self-citations included. Journal
cohort, 2014, citations 0, 1, 3, 4. Mean 2, so ncs is citations / 2. The
mid-rank percentile of those four values is 0.125, 0.375, 0.625, 0.875.
quantile_cont at 0.90 is 3.7 and at 0.99 is 3.97, so only the paper with 4
citations is in the top 10% and the top 1%.
"""

import math

import pytest

import oam

# work_id -> citations, ncs, percentile, top10, top1
_JOURNAL = {
    104: (0, 0.0, 0.125, False, False),
    103: (1, 0.5, 0.375, False, False),
    102: (3, 1.5, 0.625, False, False),
    101: (4, 2.0, 0.875, True, True),
}
# Conference cohort, citations 0 and 4. Mean 2. p90 is 3.6, p99 is 3.96.
_CONFERENCE = {
    202: (0, 0.0, 0.25, False, False),
    201: (4, 2.0, 0.75, True, True),
}


def _row(work_id):
    frame = oam.get_metrics([work_id]).set_index("work_id")
    return frame.loc[work_id]


def test_journal_cohort_scores():
    for work_id, (citations, ncs, percentile, top10, top1) in _JOURNAL.items():
        row = _row(work_id)
        assert row.citations == citations
        assert row.ncs == pytest.approx(ncs)
        assert row.citation_normalized_percentile == pytest.approx(percentile)
        assert bool(row.is_in_top_10_percent) is top10
        assert bool(row.is_in_top_1_percent) is top1
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
        assert row.citations == citations
        assert row.ncs == pytest.approx(ncs)
        assert row.citation_normalized_percentile == pytest.approx(percentile)
        assert bool(row.is_in_top_10_percent) is top10
        assert bool(row.is_in_top_1_percent) is top1
        assert row.cohort_n_works == 2
        assert bool(row.cohort_is_reliable) is False


def test_xpack_citation_does_not_count():
    # 901 cites 104, and 901 is xpack, so 104 stays uncited.
    assert _row(104).citations == 0
    assert oam.get_metrics([901]).in_scope.iloc[0] == False  # noqa: E712


def test_a_self_citation_counts_by_default():
    # 101 and 102 share an author, and 101 cites 102.
    assert _row(102).citations == 3


def test_a_later_citation_counts_by_default():
    # 101 is cited in 2018, which is publication year + 4.
    assert _row(101).citations == 4
    # 201 is cited in 2025. Mean of (4, 0) is 2.
    row = _row(201)
    assert row.citations == 4
    assert row.ncs == pytest.approx(2.0)


def test_an_uncited_cohort_flags_nobody():
    # Both papers have no citations, so the top-10% threshold is 0. Neither is flagged.
    for work_id in (301, 302):
        row = _row(work_id)
        assert row.citations == 0
        assert math.isnan(row.ncs)
        assert bool(row.is_in_top_10_percent) is False
        assert bool(row.is_in_top_1_percent) is False
        assert row.citation_normalized_percentile == pytest.approx(0.5)
