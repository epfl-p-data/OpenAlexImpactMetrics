"""OpenAlex identifier shapes: numbers, URLs, DOIs, ORCIDs, year ranges.

Nothing here touches the network. The warehouse stores ids as numbers and DOIs
and ORCIDs bare and lowercased; these helpers turn whatever the caller has into
that form.
"""

import re

_DIGITS = re.compile(r"(\d+)\s*$")


def numeric_id(openalex_id):
    """https://openalex.org/W2741809807 -> 2741809807"""
    match = _DIGITS.search(str(openalex_id))
    return int(match.group(1)) if match else None


def year_filter(year):
    """2020, (2014, 2023) or '2014-2023' -> a 'YYYY' or 'YYYY-YYYY' spec."""
    if isinstance(year, str):
        return year
    if isinstance(year, (tuple, list)):
        return f"{year[0]}-{year[1]}"
    return str(year)


def values(value):
    """One value or many -> a list. Strings are single values, not iterables."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        return [value]
    return list(value)


def clean_orcid(orcid):
    """Accept an ORCID with or without its URL prefix; return the bare form."""
    bare = re.sub(r"^https?://orcid\.org/", "", str(orcid).strip().lower())
    return bare or None


def clean_doi(doi):
    doi = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def unique_dois(dois):
    """The cleaned DOIs of an iterable, de-duplicated, order kept."""
    wanted, seen = [], set()
    for raw in dois:
        doi = clean_doi(raw)
        if doi and doi not in seen:
            seen.add(doi)
            wanted.append(doi)
    return wanted
