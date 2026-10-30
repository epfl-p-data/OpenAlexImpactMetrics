# How the scores work

OAM compares every paper with papers like it, then summarises those comparisons for a set of papers. This page starts with one paper, then moves to a set.

## Comparable papers: the cohort

A paper is compared with the papers that share three things with it:

- its **subfield**, as assigned by OpenAlex (for example Artificial Intelligence)
- its **publication year**
- its **venue type**: journal article or conference paper

That group is the paper's **cohort**. Citation habits differ a lot between fields and years, so comparing within the cohort is what makes a mathematics paper from 2014 comparable with a biology paper from 2022.

## One paper

Take `W2103094658`, a 2014 conference paper in Artificial Intelligence.

```python
oam.get_metrics(["W2103094658"])
```

| Column | Meaning |
|---|---|
| `citations` | In-scope citations, counted the way the data was built. See [Citation window](#citation-window) and [Self-citations](#self-citations) |
| `ncs` | `citations` divided by the cohort's average. 1 means exactly average. This is the normalised citation score the MNCS is built from |
| `citation_normalized_percentile` | Mid-rank in the cohort: the papers below this one, plus half of the papers tied with it, divided by the size of the cohort. Between 0 and 1. Tied papers share a value, and an uncited paper is not given 0 |
| `is_in_top_10_percent` | `citations` is at or above the cohort's 90th percentile, and at least 1. Papers tied on the threshold are all counted in |
| `is_in_top_1_percent` | The same at the 99th percentile, and at least 1 |
| `cohort_n_works` | Papers in the cohort |
| `cohort_is_reliable` | The cohort has at least 500 papers |

`get_metrics` also returns OpenAlex's own `fwci` and percentile, under the names `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent` and `openalex_is_in_top_1_percent`. They are copied unchanged. [OAM and OpenAlex](#oam-and-openalex) says how they differ from OAM's scores.

## A set of papers

For a set of papers — an institution, a lab, a funder's portfolio — OAM summarises the per-paper scores. The column names carry the counting method. With full counting they end in `_full`.

| Column | Meaning |
|---|---|
| `P_full` | Number of papers scored. Returned for both counting methods |
| `MNCS_full` | Average `ncs`. 1 is the world average |
| `PP_top10_full` | Percentage of papers with `is_in_top_10_percent`. A little above 10 for the world, because ties at the threshold are counted in |
| `PP_top1_full` | Percentage of papers with `is_in_top_1_percent`. A little above 1 for the world |
| `TCS_full` | Total `citations` |
| `MCS_full` | Average `citations`, not normalised |

EPFL in 2014:

```python
ids = oam.resolve(institution="I5124864", year=2014)
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full
             2014    4151  248778.0     59.93       1.94          22.04          3.11
```

`aggregate` returns one row per publication year by default. Pass `by=None` for a single row covering the whole set.

4,151 papers are scored. The MNCS is 1.94, so they are cited just under twice as often as comparable papers. 22.0% are in the top 10% of their cohort and 3.1% in the top 1%.

The percentile is a per-paper column. The summary does not average it; PP(top 10%) and PP(top 1%) are how the ranking shows up at the set level.

Over the 48,656,456 papers published at least four years before the snapshot and in a reliable cohort, the MNCS is 1.00, PP(top 10%) is 10.18% and PP(top 1%) is 1.02%. Among those years with at least 100,000 such papers, PP(top 10%) runs from 10.09 to 10.37 and PP(top 1%) from 1.01 to 1.04. Ties at the threshold are counted in, which is why both sit a little above the name.

## Full and fractional counting

By default every paper counts once, whatever the number of institutions on it. This is **full counting**, and it is what the EPFL numbers above use.

With **fractional counting**, a paper counts as `1 / institutions_distinct_count`. A paper written with nine other institutions counts as a tenth of a paper for EPFL. Use it when you compare institutions and do not want a shared paper to be counted in full by everyone who shares it. The columns then end in `_frac`, and `P_full` is still the number of papers. `P_frac` is the sum of the fractions.

```python
oam.aggregate(ids, counting="full")   # the default
oam.aggregate(ids, counting="frac")
```

EPFL in 2014, fractional counting:

```
 publication_year  P_full  P_frac  TCS_frac  MCS_frac  MNCS_frac  PP_top10_frac  PP_top1_frac
             2014    4151 2061.93 105536.17     51.18       1.73          19.63          2.73
```

The same 4,151 papers weigh 2,062 once each institution's share is taken. The MNCS is 1.73, and 19.6% are in the top 10%.

## Which papers are scored

EPFL's name is on 5,788 works from 2014, and 4,213 are in scope. Two things remove papers from the summary.

**Scope.** Only papers inside the scope get a score: by default, English-language articles in a core journal or a conference proceedings, with an institutional affiliation, a subfield and a reference list, and not retracted or front matter. 4,213 of the 5,788 pass. The rest are preprints, datasets, book chapters and similar. Scope also decides which citations count: a citation is only counted when the citing paper is itself in scope. See [Scope](scope.md) for the rules, and [Querying](querying.md#which-papers-are-scored) to see which rule removed which paper.

**Small cohorts.** A cohort with fewer than 500 papers has an unstable average. Its papers still get an `ncs`, with `cohort_is_reliable` set to false, but `aggregate` leaves them out. 62 of the 4,213 are in such cohorts. To keep them, pass `reliable_only=False`.

## Citation window

By default a citation counts whenever it was made, up to the snapshot. A 2014 paper can be cited by a paper from 2024.

The window is chosen when the metric layers are built, and the data keeps one count. A window of `n` counts the publication year and the `n - 1` years after it, so `4` is the publication year and the three following years. That is the window OpenAlex and Scopus use for the field-weighted citation impact. It is not the window of the Leiden Ranking, which counts citations until the end of the first year after a multi-year publication period, so the papers in one period have windows of different lengths.

```python
oam.build(steps="metrics", citation_window=4)
```

A paper from 2025 scored with a window of 4 has not had four years. It is still scored, on the citations that have arrived. The publication year is enough to see that; there is no separate flag.

`oam.status()` prints the window the data was built with. A published archive records it in `bundle.json`.

## Self-citations

A self-citation is a citation where the citing paper and the cited paper share an OpenAlex author. By default those citations are kept, which is what the OpenAlex and Scopus field-weighted citation impact do.

`exclude_self_citations=True` removes them, and the cohort averages are computed without them. The Leiden Ranking excludes author self-citations. The data still holds one set of scores, so this is a choice about how it is built, not an argument of `aggregate`.

```python
oam.build(steps="metrics", citation_window=4, exclude_self_citations=True)
```

Two caveats, when they are removed. OpenAlex decides which names are the same person, and that decision is wrong in both directions: some real self-citations stay in, and some citations by a different person are removed. A paper with thousands of authors can lose most of its citations, because a citing paper from the same collaboration almost always shares an author.

## OAM and OpenAlex

OpenAlex publishes an `fwci` and a `citation_normalized_percentile` on the work itself. OAM copies both onto the score table, under the `openalex_` names, and computes its own. They answer similar questions and they are not the same number.

The population differs. OAM scores English-language articles in a core journal or a conference proceedings, with an affiliation, a subfield and a reference list, and it counts a citation only when the citing paper is in that set too. OpenAlex computes the FWCI for articles, conference articles, reviews, books and book chapters, and it counts a citation from any work in OpenAlex.

The cohort differs. OAM groups by publication year, subfield and venue type, and the venue type is journal or conference. OpenAlex groups by publication year, work type and subfield, and it splits articles into journal articles and conference articles.

| OAM | OpenAlex | What differs |
|---|---|---|
| `citations` | the count behind `fwci` | The published data counts every in-scope citation up to the snapshot, self-citations included. OpenAlex uses the publication year and the three following years, keeps self-citations, and counts every citing work. A build can choose that window, and can drop self-citations |
| `ncs` | `fwci` | Both divide the count by the mean of the group. The groups and the counts differ, as above |
| `citation_normalized_percentile` | `citation_normalized_percentile.value` | OAM uses the mid-rank of `citations`. OpenAlex uses all-time citations and `(rank − 1) / (max rank + 1)`, with tied papers taking the lowest rank, so an uncited paper is 0 |
| `is_in_top_10_percent` | `citation_normalized_percentile.is_in_top_10_percent` | OAM flags every paper at or above the cohort's 90th percentile that has at least one citation. OpenAlex flags from its own percentile |
| `is_in_top_1_percent` | `citation_normalized_percentile.is_in_top_1_percent` | The same, at the 99th percentile |

OpenAlex marks the top 10% from a percentile of 0.90 and the top 1% from 0.99.

OpenAlex's description of these fields is in its help center, and the calculation is in its 2025 metric code.
