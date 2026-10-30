# Querying

How to choose papers, check which ones are scored, and get the scores per paper or per group. For what the scores mean, see [How the scores work](metrics.md).

## All functions

The query functions read the data from `oam.download()`. If it is not there, the function says so.

| Function | What it does | Where |
|---|---|---|
| `oam.resolve(...)` | Paper ids matching a filter | [Choose the papers](#choose-the-papers) |
| `oam.resolve_dois(dois)` | Paper ids for a DOI list | [A list of DOIs](#a-list-of-dois) |
| `oam.work_ids(ids)` | Integers from `W…` ids | [A list of OpenAlex work ids](#a-list-of-openalex-work-ids) |
| `oam.get_metrics(ids)` | One row of scores per paper | [Scores per paper](#scores-per-paper) |
| `oam.explain(ids)` | Whether each paper is in scope, and which rules it failed | [Which papers are scored](#which-papers-are-scored) |
| `oam.aggregate(ids)` | One row of indicators per group | [Indicators per group](#indicators-per-group) |
| `oam.dimension(kind)` | Names behind ids, and ids behind names | [Finding an id](#finding-an-id) |
| `oam.download()` | Install a published archive | [Building](building.md) |
| `oam.installed()` | The `bundle.json` of a download, or `None` for a local build | [Where the data lives](#where-the-data-lives) |
| `oam.status()` | What is installed, how big, which snapshot | [Where the data lives](#where-the-data-lives) |
| `oam.check()` | Consistency checks | [Building](building.md) |
| `oam.profile()` | Print what the data contains | [Building](building.md) |
| `oam.set_data_dir(path)` | Use another directory for the rest of the session | [Where the data lives](#where-the-data-lives) |
| `oam.export(path)` | Write the archive a download installs | [Building](building.md) |
| `oam.build()` | Build the store, the metric layers and the resolve warehouse | [Building](building.md) |

## Choose the papers

### A list of OpenAlex work ids

Any of these shapes works: `2741809807`, `"W2741809807"` or `"https://openalex.org/W2741809807"`. A DOI is not a work id, and passing one raises an error rather than scoring a different paper. Use the DOI list below.

```python
ids = ["W2103094658", "W2741809807", "https://openalex.org/W2108440715"]
oam.aggregate(ids)
```

`oam.work_ids(ids)` converts them to plain integers, if you need that.

### A list of DOIs

```python
ids = oam.resolve(doi=["10.1038/nature12373", "10.1126/science.1244693"])
```

Only DOIs present in the snapshot come back, so the list can be shorter than the input, and the call prints how many are missing. `doi=` takes one DOI or a list, with or without `https://doi.org/`, and does not combine with the other arguments.

### A filter

```python
oam.resolve(institution="I5124864", year=(2014, 2023))    # EPFL, 2014 to 2023
oam.resolve(country="CH", funder="F4320306076", year=2023)  # Swiss papers funded by the US NSF, 2023
oam.resolve(orcid="0000-0002-1825-0097")                  # one researcher
oam.resolve(field=17, year=2023, proceedings=True)        # computer science conference papers
```

Different arguments are combined with AND. A list given to one argument is combined with OR: `country=["CH", "FR"]` is Switzerland or France. `year=[2019, 2021]` is 2019 or 2021. `year=(2019, 2021)` and `year="2019-2021"` are every year from 2019 to 2021.

A bare number works wherever a prefixed id does: `5124864` and `"I5124864"` are the same institution, and the same is true of authors, funders, sources, subfields, fields and domains.

| Argument | Matches |
|---|---|
| `institution=` | An institution, including its labs and hospitals |
| `country=` | A two-letter country code, for example `"CH"`. A country name is an error |
| `author=` / `orcid=` | An OpenAlex author id, or an ORCID |
| `funder=` | A funder id |
| `source=` | A journal or conference id |
| `subfield=` / `field=` / `domain=` | The paper's main topic, at three levels of detail |
| `year=` | `2020`, `[2019, 2021]`, `(2014, 2023)` or `"2014-2023"`. A list is those years. A tuple or a dashed string is the range |
| `proceedings=` | `True` for conference papers only, `False` for journal articles only |
| `oa_status=` | `"closed"`, `"green"`, `"bronze"`, `"hybrid"`, `"gold"` or `"diamond"` |
| `work_type=` | The OpenAlex type, for example `"article"` |
| `corresponding=` | `True` keeps only papers where the institution, country or author is the corresponding author |

### Finding an id

`oam.dimension` has four kinds: `institutions`, `funders`, `sources` and `topics`. Search matches the stored OpenAlex display name, and the ROR or ISSN when the table has one. It ignores accents, so `"Neuchatel"` finds Neuchâtel, and an exact name comes before a name that only starts with the word, which comes before any other substring. `limit=` keeps the first rows of that order.

```python
oam.dimension("institutions", search="Lausanne")
oam.dimension("institutions", ids="I5124864")    # the name behind an id
oam.dimension("topics")                          # every subfield, field and domain
oam.dimension("sources", search="Nature", limit=10)
```

Look an id up before you score on it. A wrong id still returns the works of whatever it names.

| Kind | How to find it |
|---|---|
| Institution | English or local name. `"EPFL"` matches a continuing-education centre and a company, so search `"Lausanne"` or the full name and check the row |
| Source | English or local name, or an ISSN. `"NeurIPS"` matches nothing; `"Neural Information Processing Systems"` does. `"Nature"` with `limit=10` puts the journal Nature first |
| Funder | Often the name in the funder's own language, and sometimes one funder is several programmes. `"SNSF"` and `"Swiss National Science Foundation"` match nothing; the stored name is searched with `"Nationalfonds"`. `"European Research Council"` is three rows (ERC, H2020 ERC, FP7 Ideas ERC), and a portfolio of the council is all three |
| Author | No author table. Use an ORCID, or `https://api.openalex.org/authors?search=` for an `A…` id |

When a search here misses, search on [openalex.org](https://openalex.org) and pass the id it shows.

### A query OAM cannot express

OAM does not call the OpenAlex API. For a filter it lacks, or for papers newer than the snapshot, get the ids from the [OpenAlex API](https://docs.openalex.org) and pass them in:

The API returns the most-cited works first. The first page is the top of that list, so scoring it gives a number for the most-cited papers. Follow `cursor=*` until the pages run out. `meta.count` is how many works the filter matches.

```python
import requests

cursor, ids, total = "*", [], None
while cursor:
    page = requests.get("https://api.openalex.org/works", params={
        "filter": "authorships.institutions.lineage:I5124864,has_abstract:true,publication_year:2023",
        "select": "id",
        "per-page": 200,
        "cursor": cursor,
    }).json()
    total = page["meta"]["count"]
    ids.extend(w["id"] for w in page["results"])
    cursor = page["meta"]["next_cursor"]
print(len(ids), "of", total)
oam.aggregate(ids)
```

Papers the snapshot does not contain are reported as not found and are not scored.

## Which papers are scored

A filter usually returns more papers than get a score. `explain` tells you, paper by paper, whether it is in scope and which rules it failed. EPFL in 2023:

```python
ids = oam.resolve(institution="I5124864", year=2023)
why = oam.explain(ids)
why.in_scope.value_counts()
```

```
in_scope
True     4508
False    3255
```

Why the 3,255 others are out. A paper can fail several rules, so the counts overlap:

```python
why.loc[~why.in_scope, "failed_criteria"].explode().value_counts()
```

| Rule failed | Papers | Meaning |
|---|---|---|
| `is_core_or_proceedings` | 2,868 | Not in a core journal or a conference proceedings |
| `has_referenced_works` | 2,492 | No reference list |
| `is_english` | 215 | Not in English |
| `has_subfield` | 177 | No subfield |
| `is_not_paratext` | 24 | Editorial, erratum or similar |

`found` is false for an id the snapshot does not contain at all. The rules are described in [Scope](scope.md).

A paper can pass every rule and still not be scored. `is_xpack` is true for an OpenAlex xpack record, and those are left out of the comparison: 46,548 papers on the 2026-10-14 snapshot. [Scope](scope.md#what-is-not-configurable) explains why. A paper with no publication year is also left out: 2 papers on that snapshot. `explain` reports `is_xpack`, and a missing year is a null `publication_year`.

## Scores per paper

```python
works = oam.get_metrics(ids)
```

One row per id, including papers that are not scored; their score columns are empty. `scored` is true when the paper has a score. The other columns are `publication_year`, `subfield`, `is_proceedings`, `institutions_distinct_count`, `citations`, `ncs`, `citation_normalized_percentile`, `is_in_top_10_percent`, `is_in_top_1_percent`, `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent`, `openalex_is_in_top_1_percent`, `cohort_n_works` and `cohort_is_reliable`.

`citations` and the scores built from it follow the citation window and the self-citation rule the data was built with. The download uses every citation up to the snapshot and keeps self-citations. `oam.status()` prints the choices.

`oam.get_metrics(ids, explain=True)` adds `in_scope`, `is_xpack`, `failed_criteria` and the scope columns from `explain`. `in_scope` means the paper passed the rules. `scored` means it has a row in the score table. A paper in scope is not scored when it is an xpack record, or when it has no publication year.

### Joining to OpenAlex

Titles, DOIs and the other descriptive fields are not in the download. Join them from OpenAlex on the work id, with the `W` prefix put back on the integer:

```python
import requests

works = oam.get_metrics(ids)
wanted = "|".join(f"W{i}" for i in works.work_id.head(2))
r = requests.get(
    "https://api.openalex.org/works",
    params={"filter": f"openalex:{wanted}", "select": "id,doi,title"},
)
```

## Indicators per group

```python
oam.aggregate(ids)                                  # one row per publication year (the default)
oam.aggregate(ids, by=None)                         # one row for the whole set
oam.aggregate(ids, by="is_proceedings")             # journals and conferences apart
oam.aggregate(ids, by=["publication_year", "is_proceedings"])
```

EPFL 2014, `by="is_proceedings"`:

```
 is_proceedings  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full
          False    3402  239050.0     70.27       2.04          22.43          3.29
           True     749    9728.0     12.99       1.52          20.29          2.27
```

The 3,402 journal articles have an MNCS of 2.04. The 749 conference papers have an MNCS of 1.52.

What `resolve` can filter and what `aggregate` can group by are different lists.

| | `resolve(...)` filter | `aggregate(by=...)` |
|---|---|---|
| publication year | yes | yes |
| subfield | yes | yes, as an id |
| field, domain | yes | |
| journal or conference | `proceedings=` | `is_proceedings` |
| institution, country, author, ORCID, funder, source | yes | |
| open access status, work type | yes | |
| number of institutions, top 10% and top 1% flags, cohort reliability | | yes |

A grouping that `resolve` can filter, such as country or open access status, is one `resolve` per value:

```python
for status in ("closed", "green", "bronze", "hybrid", "gold", "diamond"):
    papers = oam.resolve(institution="I5124864", year=2023, oa_status=status)
    print(status, len(papers))
```

`subfield` is the OpenAlex id. The name is a merge with the topics table:

```python
by_subfield = oam.aggregate(ids, by="subfield").sort_values("P_full", ascending=False)
names = oam.dimension("topics", ids=by_subfield.subfield)
by_subfield.merge(names, left_on="subfield", right_on="subfield_id")
```

`aggregate` returns the rows in subfield-id order, so sort by `P_full` before reading them.

The scores follow the build. They are not arguments of `aggregate`. [Building](building.md#citation-window-and-self-citations) is where a window or the removal of self-citations is chosen.

The other options:

| Option | Default | Effect |
|---|---|---|
| `counting=` | `"full"` | `"frac"` weighs each paper by `1 / institutions_distinct_count`. See [counting](metrics.md#full-and-fractional-counting) |
| `reliable_only=` | `True` | `False` also keeps papers in cohorts of fewer than 500 papers |

## Where the data lives

```python
oam.status()                  # what is installed, how big, which snapshot
oam.installed()               # the bundle.json of a download, or None for a local build
oam.set_data_dir("/data/oam") # use another directory for the rest of the session
```

The default is `~/oam-data`, or `./data` when you work from a clone of this repository. The `OAM_DATA_DIR` environment variable overrides both.
