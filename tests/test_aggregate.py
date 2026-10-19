"""Set-level indicators, full and fractional, against the same hand values.

The reliable bin is the 2014 journal bin: citations 3, 2, 1, 0 and weights
1, 1/2, 1, 1/4. Only the paper with 3 citations is in the top 10% and the top 1%.
"""

import pytest

import oam

_IDS = [101, 102, 103, 104]
_WEIGHTS = {101: 1.0, 102: 0.5, 103: 1.0, 104: 0.25}
_NCS = {101: 3 / 1.5, 102: 2 / 1.5, 103: 1 / 1.5, 104: 0.0}


def _weighted(column):
    return sum(column[i] * _WEIGHTS[i] for i in _IDS) / sum(_WEIGHTS.values())


def test_full_counting_is_the_plain_average():
    row = oam.aggregate(_IDS, by=None).iloc[0]
    assert row.P == 4
    assert row.P_weighted == 4
    assert row.TCS == 6
    assert row.MCS == pytest.approx(1.5)
    assert row.MNCS == pytest.approx(1.0)
    assert row.PP_top10 == pytest.approx(25.0)
    assert row.PP_top1 == pytest.approx(25.0)


def test_fractional_counting_weights_by_institutions():
    row = oam.aggregate(_IDS, counting="frac", by=None).iloc[0]
    assert row.P == 4
    assert row.P_weighted == pytest.approx(sum(_WEIGHTS.values()))
    # citations 3, 2, 1, 0 with weights 1, 1/2, 1, 1/4
    assert row.TCS == pytest.approx(3 * 1 + 2 * 0.5 + 1 * 1 + 0)
    assert row.MCS == pytest.approx(_weighted({101: 3, 102: 2, 103: 1, 104: 0}))
    assert row.MNCS == pytest.approx(_weighted(_NCS))
    assert row.PP_top10 == pytest.approx(100 * _WEIGHTS[101] / sum(_WEIGHTS.values()))
    assert row.PP_top1 == pytest.approx(100 * _WEIGHTS[101] / sum(_WEIGHTS.values()))


def test_small_bin_is_left_out_unless_asked_for():
    ids = _IDS + [201, 202]
    kept = oam.aggregate(ids, by=None)
    assert kept.P.iloc[0] == 4
    both = oam.aggregate(ids, by=None, reliable_only=False)
    assert both.P.iloc[0] == 6


def test_grouping():
    by_year = oam.aggregate(_IDS)
    assert list(by_year.publication_year) == [2014]
    both = oam.aggregate(_IDS + [201, 202], by=["publication_year", "is_proceedings"],
                         reliable_only=False)
    assert len(both) == 2
    assert set(both.is_proceedings) == {False, True}


def test_unknown_grouping_is_rejected():
    with pytest.raises(ValueError, match="cannot group"):
        oam.aggregate(_IDS, by="doi")
