# Querying

How to choose papers, check which ones are scored, and get the scores per paper or per group. For what the scores mean, see [How the scores work](metrics.md).

## The functions

All of them read the data from `oam.download()`. If it is not there, the function says so.

| Function | Returns |
|---|---|
| `oam.aggregate(ids)` | One row of indicators per group |
| `oam.get_metrics(ids)` | One row of scores per paper |
| `oam.explain(ids)` | Whether each paper is in scope, and which rules it failed |
| `oam.dimension(kind)` | Names behind ids, and ids behind names |
| `oam.resolve(...)` | Paper ids matching a filter or a DOI list |

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

Only DOIs present in the snapshot come back, so the list can be shorter than the input. `doi=` takes one DOI or a list, with or without `https://doi.org/`, and does not combine with the other arguments.

### A filter

```python
oam.resolve(institution="I5124864", year=(2014, 2023))    # EPFL, 2014 to 2023
oam.resolve(country="CH", funder="F4320306076", year=2023)  # Swiss papers funded by the US NSF, 2023
oam.resolve(orcid="0000-0002-1825-0097")                  # one researcher
oam.resolve(field=17, year=2023, proceedings=True)        # computer science conference papers
```

Different arguments are combined with AND. A list given to one argument is combined with OR: `country=["CH", "FR"]` is Switzerland or France.

| Argument | Matches |
|---|---|
| `institution=` | An institution, including its labs and hospitals |
| `country=` | Any institution in that country, for example `"CH"` |
| `author=` / `orcid=` | An OpenAlex author id, or an ORCID |
| `funder=` | A funder id |
| `source=` | A journal or conference id |
| `subfield=` / `field=` / `domain=` | The paper's main topic, at three levels of detail |
| `year=` | `2020`, `(2014, 2023)` or `"2014-2023"` |
| `proceedings=` | `True` for conference papers only, `False` for journal articles only |
| `oa_status=` | `"closed"`, `"green"`, `"bronze"`, `"hybrid"`, `"gold"` or `"diamond"` |
| `work_type=` | The OpenAlex type, for example `"article"` |
| `corresponding=` | `True` keeps only papers where the institution, country or author is the corresponding author |

To find an id from a name, or a name from an id:

```python
oam.dimension("institutions", search="Lausanne")
oam.dimension("institutions", ids="I5124864")
oam.dimension("funders", search="Nationalfonds")   # the Swiss National Science Foundation
oam.dimension("topics")          # every subfield, field and domain
```

The search matches the names OpenAlex stores, which are sometimes in the local language: the Swiss National Science Foundation is `Schweizerischer Nationalfonds zur Förderung der Wissenschaftlichen Forschung`.

### A query OAM cannot express

OAM does not call the OpenAlex API. For a filter it lacks, or for papers newer than the snapshot, get the ids from the [OpenAlex API](https://docs.openalex.org) and pass them in:

```python
import requests

r = requests.get("https://api.openalex.org/works", params={
    "filter": "authorships.institutions.lineage:I5124864,has_abstract:true,publication_year:2023",
    "select": "id",
    "per-page": 200,
})
ids = [w["id"] for w in r.json()["results"]]   # follow meta.next_cursor for more pages
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

A paper can pass every rule and still not be scored when `is_xpac` is true. It is an OpenAlex xpack record, and those are left out of the comparison: 46,548 papers on the 2026-10-14 snapshot. [Scope](scope.md#what-is-not-configurable) explains why.

## Scores per paper

```python
works = oam.get_metrics(ids)
```

One row per id, including papers that are not scored; their score columns are empty. The columns are `in_scope`, `publication_year`, `subfield`, `is_proceedings`, `institutions_distinct_count`, `citations`, `ncs`, `citation_normalized_percentile`, `is_in_top_10_percent`, `is_in_top_1_percent`, `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent`, `openalex_is_in_top_1_percent`, `cohort_n_works` and `cohort_is_reliable`.

`citations` and the scores built from it follow the citation window and the self-citation rule the data was built with. The download uses every citation up to the snapshot and keeps self-citations. `oam.status()` prints the choices.

`oam.get_metrics(ids, explain=True)` adds the scope columns from `explain` to the same frame.

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

`by` accepts `publication_year`, `subfield`, `is_proceedings`, `institutions_distinct_count`, `is_in_top_10_percent`, `is_in_top_1_percent` and `cohort_is_reliable`. Name a subfield with `oam.dimension("topics", ids=...)`.

The scores follow the build. They are not arguments of `aggregate`. [Building](building.md#citation-window-and-self-citations) is where a window or the removal of self-citations is chosen.

The other options:

| Option | Default | Effect |
|---|---|---|
| `counting=` | `"full"` | `"frac"` weighs each paper by `1 / institutions_distinct_count`. See [counting](metrics.md#full-and-fractional-counting) |
| `reliable_only=` | `True` | `False` also keeps papers in cohorts of fewer than 500 papers |

## Where the data lives

```python
oam.status()                  # what is installed, how big, which snapshot
oam.set_data_dir("/data/oam") # use another directory for the rest of the session
```

The default is `~/oam-data`, or `./data` when you work from a clone of this repository. The `OAM_DATA_DIR` environment variable overrides both.
