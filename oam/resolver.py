"""Turn a query into work ids, from the local warehouse.

`oam.resolve` comes from here. The answer is read from the resolve tables, which
arrive with oam.download() or from build step 3, and never touches the network.
It is the same snapshot the metrics come from, so a work cannot be resolved that
the metrics cannot account for, and rerunning the query next month gives the
same list.

The dimensions the warehouse holds are listed in DIMENSIONS. For a set of work
ids you already have, skip this module and pass them to oam.aggregate.
"""

import pandas as pd

from . import config, openalex, resolve_store
from .duck import read_connection as connection
from .duck import require_tables

# Work ids come back ascending. Nothing should read anything into the order.


def available():
    """Whether queries can be answered locally."""
    return resolve_store.is_built()


# ---------------------------------------------------------------------------
# What can be resolved
# ---------------------------------------------------------------------------
# The pair tables, keyed by the argument that searches them. Each is sorted by its id
# column, which is what makes the lookup cheap: the row groups that cannot hold the
# id are skipped without being read.
#
# `through` names a dimension table to translate a non-numeric argument into those
# ids first — a country into its institutions, an ORCID into its author — so every
# lookup still lands on the sorted column.

_PAIRS = {
    "institution": ("work_institutions", "institution_id", None),
    "country":     ("work_institutions", "institution_id",
                    ("dim_institutions", "institution_id", "country_code")),
    "author":      ("work_authors", "author_id", None),
    "orcid":       ("work_authors", "author_id",
                    ("author_orcid", "author_id", "orcid")),
    "funder":      ("work_funders", "funder_id", None),
}

# Columns of works_attrs, which every query with one of these filters has to touch.
# subfield carries the field and the domain too, via the 252-row dim_topics.
_ATTRS = {
    "source":    "source_id",
    "oa_status": "oa_status",
    "work_type": "work_type",
    "subfield":  "subfield",
}

# Everything resolve() accepts beyond year and doi.
DIMENSIONS = tuple(_PAIRS) + tuple(_ATTRS) + ("field", "domain", "proceedings")

# Stored as numbers, so any id shape the caller has is fine. The letter is what
# stops an institution argument accepting a work id that merely ends in digits.
_NUMERIC = {"institution", "author", "funder", "source",
            "subfield", "field", "domain"}
_LETTERS = {
    "institution": "I", "institutions": "I",
    "author": "A",
    "funder": "F", "funders": "F",
    "source": "S", "sources": "S",
}

# The arguments corresponding= narrows. It reads the flag off work_institutions or
# work_authors, so on its own there is nothing for it to apply to.
_CORRESPONDABLE = ("institution", "country", "author", "orcid")

_OA_STATUSES = ("closed", "green", "bronze", "hybrid", "gold", "diamond")
_YEAR_FORMS = '2020, [2019, 2021], (2014, 2023) or "2014-2023"'


# ---------------------------------------------------------------------------
# SQL fragments
# ---------------------------------------------------------------------------

def _literals(values):
    """A comma-separated SQL literal list."""
    rendered = []
    for value in values:
        if isinstance(value, bool):
            rendered.append("TRUE" if value else "FALSE")
        elif isinstance(value, int):
            rendered.append(str(value))
        else:
            rendered.append("'" + str(value).replace("'", "''") + "'")
    return ", ".join(rendered)


def _numbers(name, given):
    """Any id shape -> the numbers the warehouse stores.

    subfield, field and domain are plain numbers. The others carry a type letter,
    and a prefixed id has to carry the right one.
    """
    letter = _LETTERS.get(name)
    numbers = []
    for value in given:
        if letter is None:
            text = str(value).strip()
            number = int(text) if text.isdigit() else None
        else:
            number = openalex.numeric_id(value, letter=letter)
        if number is None:
            raise ValueError(f"{name}: not an OpenAlex id: {value!r}")
        numbers.append(number)
    return numbers


def _normalise(name, given):
    """The values for one argument, in the shape the warehouse stores them."""
    if name in _NUMERIC:
        return _numbers(name, given)
    if name == "orcid":
        return [openalex.clean_orcid(v) for v in given]
    if name == "country":
        return [_country(value) for value in given]
    if name == "oa_status":
        return [_one_of("oa_status", value, _OA_STATUSES) for value in given]
    if name == "work_type":
        return [_one_of("work_type", value, config.OPENALEX_TYPES) for value in given]
    return [str(value).strip().lower() for value in given]


def _country(value):
    code = str(value).strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    raise ValueError(
        f"country={value!r} must be a two-letter country code. "
        'Rows from oam.dimension("institutions", search=...) carry country_code.'
    )


def _one_of(name, value, allowed):
    text = str(value).strip().lower()
    if text in allowed:
        return text
    raise ValueError(f"{name}={value!r} must be one of {', '.join(allowed)}.")


def _in(column, values):
    return f"{column} = {_literals(values)}" if len(values) == 1 \
        else f"{column} IN ({_literals(values)})"


def _pair_select(name, values, corresponding):
    """Work ids from one pair table."""
    table, column, through = _PAIRS[name]
    if through is None:
        condition = _in(column, values)
    else:
        dim, dim_id, dim_column = through
        condition = (f"{column} IN (SELECT {dim_id} FROM "
                     f"read_parquet('{config.table_glob(dim)}') "
                     f"WHERE {_in(dim_column, values)})")
    conditions = [condition]
    # Only meaningful on the two tables that carry the flag.
    if corresponding and table in ("work_institutions", "work_authors"):
        conditions.append("is_corresponding")
    return (f"SELECT work_id FROM read_parquet('{config.table_glob(table)}') "
            f"WHERE {' AND '.join(conditions)}")


def _year_condition(year):
    """A list is those years. A tuple or "2014-2023" is every year between them."""
    if isinstance(year, list):
        years = []
        for value in year:
            try:
                years.append(int(value))
            except (TypeError, ValueError):
                raise ValueError(
                    f"year={year!r} is not a year. Use {_YEAR_FORMS}."
                ) from None
        return _in("publication_year", years)
    try:
        spec = openalex.year_filter(year).strip()
        if "-" in spec:
            low_s, high_s = spec.split("-", 1)
            low, high = int(low_s), int(high_s)
        else:
            low = high = int(spec)
    except (TypeError, ValueError):
        raise ValueError(f"year={year!r} is not a year. Use {_YEAR_FORMS}.") from None
    if low > high:
        raise ValueError(f"year={year!r} starts after it ends. Use {_YEAR_FORMS}.")
    if low == high and "-" not in spec:
        return f"publication_year = {low}"
    return f"publication_year BETWEEN {low} AND {high}"


def _topic_condition(level, values):
    """field= and domain= narrow the subfield through dim_topics."""
    return (f"subfield IN (SELECT subfield_id FROM "
            f"read_parquet('{config.table_glob('dim_topics')}') "
            f"WHERE {_in(f'{level}_id', values)})")


def local_sql(year=None, corresponding=False, proceedings=None, **dimensions):
    """The query behind a local resolve. Returned as text so it can be inspected."""
    pairs, attrs = [], []

    for name in _PAIRS:
        given = openalex.values(dimensions.get(name))
        if given:
            pairs.append(_pair_select(name, _normalise(name, given), corresponding))

    for name, column in _ATTRS.items():
        given = openalex.values(dimensions.get(name))
        if given:
            attrs.append(_in(column, _normalise(name, given)))
    for level in ("field", "domain"):
        given = openalex.values(dimensions.get(level))
        if given:
            attrs.append(_topic_condition(level, _normalise(level, given)))
    if year:
        attrs.append(_year_condition(year))
    if proceedings is not None:
        attrs.append("is_proceedings" if proceedings else "NOT is_proceedings")

    works_attrs = f"read_parquet('{config.table_glob('works_attrs')}')"
    if not pairs and not attrs:
        raise ValueError(
            f"give at least one of year, doi or {', '.join(DIMENSIONS)}")
    if not pairs:
        return f"SELECT work_id FROM {works_attrs} WHERE {' AND '.join(attrs)} ORDER BY 1"

    # INTERSECT because the arguments are ANDed: a work matching an institution and a
    # funder has to appear in both. It also removes the duplicates a multi-value
    # argument produces, which the DISTINCT below covers for the single-argument case.
    candidates = "\n    INTERSECT\n    ".join(pairs)
    if not attrs:
        return f"SELECT DISTINCT work_id FROM (\n    {candidates}\n) ORDER BY 1"
    return (f"WITH candidates AS (SELECT DISTINCT work_id FROM (\n    {candidates}\n))\n"
            f"SELECT a.work_id FROM {works_attrs} a\n"
            f"JOIN candidates c ON c.work_id = a.work_id\n"
            f"WHERE {' AND '.join(attrs)}\nORDER BY 1")


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------

def resolve(institution=None, author=None, doi=None, year=None,
            corresponding=False, proceedings=None, **dimensions):
    """Numeric work IDs for a query, from the warehouse.

    institution   institution id; matches the whole lineage, so a parent picks up
                  its labs and hospitals
    author        author id
    doi           one DOI or a list of them, on its own: it does not combine with
                  the other arguments
    year          2020, [2019, 2021], (2014, 2023) or "2014-2023". A list is those
                  years. A tuple or a "2014-2023" string is the range between them
    corresponding restrict institution=, country=, author= and orcid= to the works
                  they corresponded on
    proceedings   True for conference papers, False for journal articles
    dimensions    country, funder, source, orcid, oa_status, work_type, subfield,
                  field, domain — one value or a list of them, OR-ed together

    Arguments are ANDed, so resolve(institution=..., funder=..., year=...) is works
    from that institution, funded by that funder, in those years. Out-of-scope works
    are included; get_metrics reports scored per work rather than dropping them.

    >>> resolve(institution="I5124864", year=(2014, 2023))
    >>> resolve(country="CH", funder="F4320306076", year=2023)
    """
    unknown = set(dimensions) - set(DIMENSIONS)
    if unknown:
        raise TypeError(f"resolve() got unexpected arguments: {sorted(unknown)}")
    if corresponding and not any(
            dimensions.get(name) for name in _CORRESPONDABLE) \
            and not (institution or author):
        raise ValueError(f"corresponding= narrows "
                         f"{', '.join(n + '=' for n in _CORRESPONDABLE)}, "
                         f"so one of them is needed")
    require_tables(config.RESOLVE_TABLES, "resolve()")

    if doi is not None:
        if institution or author or year or corresponding or proceedings is not None \
                or any(dimensions.values()):
            raise ValueError("doi= does not combine with other arguments; filter "
                             "oam.get_metrics(ids) instead")
        found = resolve_dois(doi)
        missing = int(found.work_id.isna().sum())
        if missing:
            print(f"· {missing} of {found.doi.nunique()} DOIs are not in the snapshot. "
                  f"oam.resolve_dois() lists them")
        return sorted(set(found.work_id.dropna().astype("int64").tolist()))

    sql = local_sql(year=year, corresponding=corresponding, proceedings=proceedings,
                    institution=institution, author=author, **dimensions)
    return [row[0] for row in connection().execute(sql).fetchall()]


def resolve_dois(dois):
    """The work id behind each DOI, as a DataFrame of doi and work_id.

    One row per DOI, in the order given and spelled as given, so the result joins
    back onto a publication list. A DOI the snapshot does not have gets an empty
    work_id. A DOI OpenAlex gives to several works gets one row per work.
    """
    require_tables(("work_doi",), "resolve_dois()")
    given = pd.DataFrame({"doi": openalex.values(dois)})
    given["key"] = given.doi.map(openalex.clean_doi)
    given = given.dropna(subset=["key"]).drop_duplicates("key")
    if given.empty:
        return pd.DataFrame({"doi": [], "work_id": pd.array([], dtype="Int64")})
    con = connection()
    con.register("_dois", given[["key"]])
    found = con.execute(f"""
        SELECT w.doi AS key, w.work_id
        FROM read_parquet('{config.table_glob("work_doi")}') w
        JOIN _dois d ON d.key = w.doi
    """).fetchdf()
    table = given.merge(found, on="key", how="left")
    return table[["doi", "work_id"]].astype({"work_id": "Int64"}).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
# The fact tables store numbers. These are how a number becomes a name, and how a
# name becomes the number to resolve on.

_SEARCHABLE = {
    "topics":       ("dim_topics", "subfield_id",
                     ("subfield_name", "field_name", "domain_name")),
    "institutions": ("dim_institutions", "institution_id", ("display_name", "ror")),
    "sources":      ("dim_sources", "source_id", ("display_name", "issn_l")),
    "funders":      ("dim_funders", "funder_id", ("display_name",)),
}


def dimension(kind, ids=None, search=None, limit=None):
    """One dimension table as a DataFrame, both directions.

    ids     the names behind numbers you already have, e.g. from aggregate(by=...)
    search  the numbers behind a name, so you have something to resolve on

    >>> dimension("institutions", search="Lausanne")
    >>> dimension("institutions", ids="I5124864")
    >>> dimension("topics")                       # every subfield, field and domain
    """
    if kind not in _SEARCHABLE:
        raise ValueError(f"unknown dimension {kind!r}; "
                         f"choose from {sorted(_SEARCHABLE)}")
    table, id_column, name_columns = _SEARCHABLE[kind]
    require_tables((table,), f"dimension({kind!r})")
    conditions = []
    if ids is not None:
        conditions.append(_in(id_column, _numbers(kind, openalex.values(ids))))
    order = id_column
    if search:
        needle = str(search).replace("'", "''")
        folded = f"strip_accents(lower('{needle}'))"
        contains = f"strip_accents(lower('%{needle}%'))"
        prefix = f"strip_accents(lower('{needle}%'))"
        conditions.append("(" + " OR ".join(
            f"strip_accents(lower({c})) LIKE {contains}" for c in name_columns) + ")")
        exact = " OR ".join(f"strip_accents(lower({c})) = {folded}" for c in name_columns)
        starts = " OR ".join(
            f"strip_accents(lower({c})) LIKE {prefix}" for c in name_columns)
        order = f"CASE WHEN {exact} THEN 0 WHEN {starts} THEN 1 ELSE 2 END, {id_column}"
    return connection().execute(
        f"SELECT * FROM read_parquet('{config.table_glob(table)}') "
        f"{'WHERE ' + ' AND '.join(conditions) if conditions else ''} "
        f"ORDER BY {order} {f'LIMIT {int(limit)}' if limit else ''}"
    ).fetchdf()
