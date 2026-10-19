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

Only DOIs present in the snapshot come back, so the list can be shorter than the input.

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
oam.dimension("funders", search="Swiss National Science")
oam.dimension("topics")          # every subfield, field and domain
```

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
False    4824
True     4613
```

Why the 4,824 others are out. A paper can fail several rules, so the counts overlap:

```python
why.loc[~why.in_scope, "failed_criteria"].explode().value_counts()
```

| Rule failed | Papers | Meaning |
|---|---|---|
| `is_core_or_proceedings` | 4,423 | Not in a core journal or a conference proceedings |
| `has_referenced_works` | 3,554 | No reference list |
| `has_subfield` | 1,075 | No subfield |
| `is_english` | 210 | Not in English |
| `is_not_paratext` | 23 | Editorial, erratum or similar |
| `is_not_retracted` | 1 | Retracted |

`found` is false for an id the snapshot does not contain at all. The rules are described in [Scope](scope.md).

## Scores per paper

```python
works = oam.get_metrics(ids)
```

One row per id, including papers that are not scored; their score columns are empty. The columns are `in_scope`, `publication_year`, `subfield`, `is_proceedings`, `n_institutions`, `citations`, `ncs`, `percentile`, `decile`, `is_top10`, `is_top1`, `bin_n_works` and `bin_is_reliable`.

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
 is_proceedings     P  P_weighted     TCS    MCS   MNCS  PP_top10  PP_top1
          False  3675      3675.0  255413  69.50  2.063     22.34     3.46
           True   764       764.0    9358  12.25  1.479     19.37     1.83
```

`by` accepts `publication_year`, `subfield`, `is_proceedings`, `n_institutions`, `decile`, `is_top10`, `is_top1`, `is_cited` and `bin_is_reliable`. Name a subfield with `oam.dimension("topics", ids=...)`.

The other options:

| Option | Default | Effect |
|---|---|---|
| `counting=` | `"full"` | `"frac"` weighs each paper by `1 / number of institutions`. See [counting](metrics.md#full-and-fractional-counting) |
| `window=` | `None` | `3` counts only citations received within three years of publication |
| `reliable_only=` | `True` | `False` also keeps papers in bins of fewer than 500 papers |

## Where the data lives

```python
oam.status()                  # what is installed, how big, which snapshot
oam.set_data_dir("/data/oam") # use another directory for the rest of the session
```

The default is `~/oam-data`, or `./data` when you work from a clone of this repository. The `OAM_DATA_DIR` environment variable overrides both.
