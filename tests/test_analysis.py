"""The citation window and the self-citation rule are choices of the build.

The default data counts every citation. A second build of the same store, with a
4-year window and self-citations removed, is the other end: 101's 2018 citation
and 201's 2025 citation fall outside the window, and 101's citation of 102 is a
self-citation.
"""

import json
import shutil
import tarfile

import pytest

import oam
from oam.metrics import recorded_analysis


def test_the_default_build_records_an_open_count(built):
    assert recorded_analysis() == {
        "citation_window": None,
        "exclude_self_citations": False,
    }
    assert oam.check(deep=True)


def test_a_window_and_self_citations_can_be_imposed(built, tmp_path):
    dest = tmp_path / "oam"
    shutil.copytree(built / "store", dest / "store")
    shutil.copytree(built / "resolve", dest / "resolve")
    oam.set_data_dir(dest)
    assert oam.build(steps="metrics", citation_window=4, exclude_self_citations=True)
    assert oam.check(deep=True)

    citations = oam.get_metrics([101, 102, 103, 104, 201]).set_index("work_id").citations
    # 4-year window, self-citations removed: 3, 2, 1, 0, and 3 for the 2018 paper.
    assert list(citations.loc[[101, 102, 103, 104, 201]]) == [3, 2, 1, 0, 3]
    assert oam.get_metrics([101]).ncs.iloc[0] == pytest.approx(3 / 1.5)

    assert recorded_analysis() == {"citation_window": 4, "exclude_self_citations": True}

    tar = oam.export(tmp_path / "dist")
    with tarfile.open(tar) as archive:
        meta = json.load(archive.extractfile("bundle.json"))
    assert meta["analysis"] == {"citation_window": 4, "exclude_self_citations": True}


def test_a_window_of_zero_is_rejected():
    with pytest.raises(ValueError, match="citation_window"):
        oam.build(citation_window=0)
