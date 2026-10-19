"""Work ids are work ids. A DOI or an institution id must not be quietly accepted."""

import pytest

from oam.openalex import numeric_id
from oam.query import work_ids
from oam.resolver import _numbers


def test_accepts_work_id_shapes():
    assert work_ids([
        2741809807,
        "W1",
        "w2",
        "https://openalex.org/W3",
        4.0,
        None,
        "",
        "  ",
    ]) == [2741809807, 1, 2, 3, 4]


def test_doi_is_rejected_with_a_hint():
    with pytest.raises(ValueError, match="oam.resolve"):
        work_ids(["10.1126/science.1244693"])
    with pytest.raises(ValueError, match="oam.resolve"):
        work_ids(["https://doi.org/10.1038/nature12373"])


def test_institution_id_is_not_a_work_id():
    with pytest.raises(ValueError, match="I5124864"):
        work_ids(["I5124864"])


def test_fractional_float_is_rejected():
    with pytest.raises(ValueError, match="1.5"):
        work_ids([1.5])


def test_prefixed_id_must_carry_the_right_letter():
    assert numeric_id("https://openalex.org/I5124864", letter="I") == 5124864
    assert numeric_id(5124864, letter="I") == 5124864
    with pytest.raises(ValueError, match="W123"):
        _numbers("institution", ["W123"])


def test_subfield_is_a_bare_number():
    assert _numbers("subfield", [17, "26"]) == [17, 26]
    with pytest.raises(ValueError, match="W17"):
        _numbers("subfield", ["https://openalex.org/W17"])
