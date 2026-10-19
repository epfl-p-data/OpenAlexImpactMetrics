"""The per-paper scores, against values worked out from the fixture.

Journal bin, 2014, citations 0, 1, 2, 3. Mean 1.5, so ncs is citations / 1.5.
CUME_DIST of those four values is 0.25, 0.50, 0.75, 1.
quantile_cont at 0.90 is 2.7 and at 0.99 is 2.97, so only the paper with 3
citations is in the top 10% and the top 1%.
"""

import pytest

import oam

# work_id -> citations, ncs, percentile, decile, top10, top1
_JOURNAL = {
    104: (0, 0.0, 0.25, 4, False, False),
    103: (1, 1 / 1.5, 0.50, 3, False, False),
    102: (2, 2 / 1.5, 0.75, 2, False, False),
    101: (3, 3 / 1.5, 1.00, 1, True, True),
}
# Conference bin, citations 0 and 4. Mean 2. p90 is 3.6, p99 is 3.96.
_CONFERENCE = {
    202: (0, 0.0, 0.5, 2, False, False),
    201: (4, 2.0, 1.0, 1, True, True),
}


def _row(work_id):
    frame = oam.get_metrics([work_id]).set_index("work_id")
    return frame.loc[work_id]


def test_journal_bin_scores():
    for work_id, (citations, ncs, percentile, decile, top10, top1) in _JOURNAL.items():
        row = _row(work_id)
        assert row.citations == citations
        assert row.ncs == pytest.approx(ncs)
        assert row.percentile == pytest.approx(percentile)
        assert row.decile == decile
        assert bool(row.is_top10) is top10
        assert bool(row.is_top1) is top1
        assert row.bin_n_works == 4
        assert bool(row.bin_is_reliable) is True


def test_conference_bin_scores():
    for work_id, (citations, ncs, percentile, decile, top10, top1) in _CONFERENCE.items():
        row = _row(work_id)
        assert row.citations == citations
        assert row.ncs == pytest.approx(ncs)
        assert row.percentile == pytest.approx(percentile)
        assert row.decile == decile
        assert bool(row.is_top10) is top10
        assert bool(row.is_top1) is top1
        assert row.bin_n_works == 2
        assert bool(row.bin_is_reliable) is False


def test_xpack_citation_does_not_count():
    # 901 cites 104, and 901 is xpack, so 104 stays uncited.
    assert _row(104).citations == 0
    assert oam.get_metrics([901]).in_scope.iloc[0] == False  # noqa: E712


def test_three_year_window_drops_the_late_citation():
    # 101 has citations in 2014, 2016 and 2020. Only the first two fall inside
    # publication_year + 2. The window mean of the bin (2, 2, 1, 0) is 1.25.
    row = oam.get_metrics([101], window=3).iloc[0]
    assert row.citations == 2
    assert row.ncs == pytest.approx(2 / 1.25)
    assert bool(row.is_top10) is True

    # 201 is cited in 2018, 2019, 2020 and 2025. 2025 is outside the window,
    # so 3 of its 4 citations remain. The bin mean of (3, 0) is 1.5.
    row = oam.get_metrics([201], window=3).iloc[0]
    assert row.citations == 3
    assert row.ncs == pytest.approx(3 / 1.5)


def test_unknown_window_is_rejected():
    with pytest.raises(ValueError, match="window"):
        oam.get_metrics([101], window=5)
