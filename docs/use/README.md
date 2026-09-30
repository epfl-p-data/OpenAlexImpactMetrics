# Querying

You have a set of publications — an institution’s output, a funder’s portfolio, or a list of OpenAlex ids. You want to know whether they were cited **more or less than comparable work** in the same field, year, and venue type (journal or conference). OAM returns **MNCS**, **PP(top 10%)**, and **PP(top 1%)** for that set.

**Which data you need.** `explain`, `get_metrics` and `aggregate` read the lite download (`oam.download()`). `resolve`, which is every filter and DOI list below, reads the full download (`oam.download("full")`). Passing ids you already have needs only the lite data.

## Scope

OpenAlex holds far more than peer-reviewed articles. **Scope** is your definition of the publication world used to build those metrics. It is chosen at [build time](../build/README.md) (see the [project README](../../README.md) and [docs/scope.md](../scope.md) for the default and how to change it).

Scope affects every number in two simple ways:

1. **Your papers** — only works that pass the scope rules get an MNCS. The rest appear in the list but are not scored.
2. **Citations** — a citation counts only if the *citing* work is in scope too.

So MNCS is always relative to **your** world. A stricter scope (for example English core journals only) lowers average citation counts and changes who is “above average”.

## Workflow

With the full download (or a build of your own):

```python
import oam

ids = oam.resolve(institution="I5124864", year=2023)              # papers that match the filter
why = oam.explain(ids)                                            # which ones are in scope
by_year = oam.aggregate(ids, by="publication_year", counting="frac")  # MNCS, PP(top 10%), …
```

`resolve` can return more ids than `aggregate` uses: preprints, datasets, and other out-of-scope works still show up in the affiliation query. `explain` shows why each id was kept or dropped. `aggregate` sums only in-scope papers in bins large enough to be stable (see below).

## Pick the works

Three ways to build the `ids` list. All use the same snapshot; no network calls.

### From a filter

Needs the full download. Arguments within one filter are OR-ed. Separate filters are AND-ed.

```python
import oam

ids = oam.resolve(institution="I5124864", year=(2014, 2023))   # EPFL
oam.resolve(country="CH", funder="F4320306076", year=2023)
oam.resolve(orcid="0000-0002-1825-0097")
oam.resolve(author="A5023888391", corresponding=True)      # corresponding author only
oam.resolve(field=17, year=2023, proceedings=True)         # conference papers
```

| Argument | Matches |
|---|---|
| `institution=` | the institution and its labs and hospitals |
| `country=` | any institution in that country (`CH`) |
| `author=` / `orcid=` | an OpenAlex author id, or an ORCID |
| `funder=` / `source=` | funder id, or journal or conference id |
| `subfield=` / `field=` / `domain=` | the work's primary topic |
| `year=` | `2020`, `(2014, 2023)` or `"2014-2023"` |
| `oa_status=` | `closed`, `green`, `bronze`, `hybrid`, `gold`, `diamond` |
| `work_type=` | the OpenAlex type, for example `article` |
| `proceedings=` | `True` for conference papers, `False` for journal articles |
| `corresponding=` | narrows `institution`, `country`, `author` and `orcid` to corresponding authorship |

`oam.dimension` turns a name into an id, or an id into a name:

```python
oam.dimension("institutions", search="Polytechnique")
oam.dimension("institutions", ids="I5124864")
```

### From a list of OpenAlex ids

If you already have work ids — a curated publication list, an export, another tool — skip `resolve` and pass them to `explain`, `get_metrics` or `aggregate`. Any OpenAlex shape is fine: `2741809807`, `"W2741809807"`, or the full URL.

```python
ids = ["W2741809807", "https://openalex.org/W2741809807", 2741809807]
oam.explain(ids)
oam.aggregate(ids, by="publication_year", counting="frac")
```

`oam.work_ids` is the same normalisation if you want the integers first. To keep only the ids that also match a warehouse filter:

```python
wanted = set(oam.work_ids(ids))
kept = [i for i in oam.resolve(institution="I5124864", year=(2014, 2023)) if i in wanted]
```

### From a list of DOIs

Needs the full download. `doi=` is the one `resolve` argument that is itself a publication list. Only DOIs the snapshot knows come back.

```python
ids = oam.resolve(doi=["10.1038/nature12373", "10.1126/science.1244693"])
```

## Which works are scored

After `resolve`, check scope before trusting the aggregates. EPFL in 2023 is 9,437 works; about half are in scope:

```python
ids = oam.resolve(institution="I5124864", year=2023)
why = oam.explain(ids)
why.in_scope.value_counts()
```

```
in_scope
False    4824
True     4613
```

Each row is one of the ids you passed. `found` is whether the snapshot has the work at all. `failed_criteria` lists the scope rules that did not hold.

```python
why.loc[~why.in_scope, ["work_id", "publication_year", "failed_criteria"]].head()
```

```
   work_id  publication_year                                failed_criteria
6939633710              2023 [has_referenced_works, is_core_or_proceedings]
4386568935              2023                       [is_core_or_proceedings]
6892488337              2023 [has_referenced_works, is_core_or_proceedings]
```

Most of the drop is works that are not a core journal or conference paper, or that have no outgoing references. A work can fail several rules at once:

```python
why.loc[~why.in_scope, "failed_criteria"].explode().value_counts()
```

```
is_core_or_proceedings    4423
has_referenced_works      3554
has_subfield              1075
is_english                 210
is_not_paratext             23
is_not_retracted             1
```

`get_metrics` is one row per id, with `in_scope` and the scores. Out-of-scope ids stay in the frame with null metrics, so the rows line up with the input.

```python
works = oam.get_metrics(ids)
works.loc[works.in_scope, ["work_id", "ncs", "is_top10", "bin_is_reliable"]].head()
```

`bin_is_reliable` is true when the work's bin — subfield × year × journal or conference — has at least 500 works. Smaller bins still get an MNCS; the mean is just unstable, so `aggregate` drops them by default. Of the 4,613 in-scope works, 4,565 sit in a reliable bin. That is the `P` `aggregate` reports. Pass `reliable_only=False` to keep the rest.

## Aggregates

`counting="full"` gives every work weight 1. `counting="frac"` gives it `1/n_institutions`, so a paper with ten institutions counts as a tenth. `P` is the raw paper count either way. `P_weighted` is the sum of the weights. `window=3` uses a fixed three-year citation window instead of all citations to date.

```python
oam.aggregate(ids, by="publication_year", counting="frac")
oam.aggregate(ids, by="publication_year", counting="full")
oam.aggregate(ids, by=None, counting="frac")          # one row for the whole set
```

```
 publication_year    P  P_weighted     TCS    MCS  MNCS  PP_top10  PP_top1
             2014 4439    2118.344 108728  51.33 1.738     19.15     2.72
             2015 4499    2145.505  97565  45.47 1.653     19.15     2.70
```

Compare `P` with `len(ids)` to see how many were scored.

## From the OpenAlex API

OAM does not call the OpenAlex API. If you need a filter the warehouse does not have — `has_abstract:true`, a concept, a live list newer than the snapshot — retrieve the work ids yourself, then score them here.

```python
import requests
import oam

r = requests.get(
    "https://api.openalex.org/works",
    params={
        "filter": "authorships.institutions.lineage:I5124864,has_abstract:true,publication_year:2023",
        "select": "id",
        "per-page": 200,
    },
)
ids = [w["id"] for w in r.json()["results"]]   # walk meta.next_cursor for more pages
oam.explain(ids)
oam.aggregate(ids, by="publication_year", counting="frac")
```

Works the snapshot does not know come back from `explain` with `found=False` and from `get_metrics` with `in_scope=False` and no score.
