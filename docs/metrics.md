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

| Column | Value |
|---|---|
| `citations_4y` | 10 |
| `ncs_4y` | 2.81 |
| `citation_normalized_percentile_4y` | 0.930 |
| `is_in_top_10_percent_4y` | true |
| `is_in_top_1_percent_4y` | false |
| `window_is_complete` | true |
| `cited_by_count_in_scope` | 37 |
| `cited_by_count_outside_scope` | 15 |
| `cohort_n_works` | 14,698 |
| `openalex_fwci` | 4.15 |
| `openalex_citation_normalized_percentile` | 0.958 |

| Column | Meaning |
|---|---|
| `citations_4y` | Citations in the publication year and the three following years, excluding self-citations |
| `ncs_4y` | `citations_4y` divided by the cohort's average. 1 means exactly average. This is the normalised citation score the MNCS is built from |
| `citation_normalized_percentile_4y` | Mid-rank in the cohort: the papers below this one, plus half of the papers tied with it, divided by the size of the cohort. Between 0 and 1. Tied papers share a value, and an uncited paper is not given 0 |
| `is_in_top_10_percent_4y` | `citations_4y` is at or above the cohort's 90th percentile, and at least 1. Papers tied on the threshold are all counted in |
| `is_in_top_1_percent_4y` | The same at the 99th percentile, and at least 1 |
| `window_is_complete` | The four years have passed. See [Citation window](#citation-window) |
| `cited_by_count_in_scope` | All citations from in-scope papers, including self-citations. This is the in-scope part of OpenAlex's `cited_by_count` |
| `cited_by_count_outside_scope` | OpenAlex's `cited_by_count` minus the in-scope count, and never negative |
| `cohort_n_works` | Papers in the cohort |
| `cohort_is_reliable` | The cohort has at least 500 papers |

`get_metrics` also returns OpenAlex's own `fwci` and percentile, under the names `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent` and `openalex_is_in_top_1_percent`. They are copied unchanged. [OAM and OpenAlex](#oam-and-openalex) says how they differ from OAM's scores.

## A set of papers

For a set of papers — an institution, a lab, a funder's portfolio — OAM summarises the per-paper scores. The column names carry the counting method. With full counting they end in `_full`.

| Column | Meaning |
|---|---|
| `P_full` | Number of papers scored. Returned for both counting methods |
| `MNCS_full` | Average `ncs_4y`. 1 is the world average |
| `PP_top10_full` | Percentage of papers with `is_in_top_10_percent_4y`. A little above 10 for the world, because ties at the threshold are counted in. On this snapshot a complete year falls between 10.46 and 11.99 |
| `PP_top1_full` | Percentage of papers with `is_in_top_1_percent_4y`. A little above 1 for the world. On this snapshot a complete year falls between 1.02 and 1.13 |
| `TCS_full` | Total `citations_4y` |
| `MCS_full` | Average `citations_4y`, not normalised |
| `window_is_complete` | True when every paper in the row has had its four years |

EPFL in 2014:

```python
ids = oam.resolve(institution="I5124864", year=2014)
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full  window_is_complete
             2014    4151   65413.0     15.76       2.13          23.39          3.54                True
```

`aggregate` returns one row per publication year by default. Pass `by=None` for a single row covering the whole set.

4,151 papers are scored. The MNCS is 2.13, so they are cited a little more than twice as often as comparable papers. 23.4% are in the top 10% of their cohort and 3.5% in the top 1%. The window is complete.

The percentile is a per-paper column. The summary does not average it; PP(top 10%) and PP(top 1%) are how the ranking shows up at the set level.

Over the 48,656,456 papers with a complete window and a reliable cohort, the MNCS is 1.00, PP(top 10%) is 10.76% and PP(top 1%) is 1.04%. Among complete years with at least 100,000 such papers, PP(top 10%) runs from 10.46 to 11.99 and PP(top 1%) from 1.02 to 1.13. Earlier years are at the top of that range: many papers there share a small citation count, and every paper on the threshold is counted in.

## Full and fractional counting

By default every paper counts once, whatever the number of institutions on it. This is **full counting**, and it is what the EPFL numbers above use.

With **fractional counting**, a paper counts as `1 / institutions_distinct_count`. A paper written with nine other institutions counts as a tenth of a paper for EPFL. Use it when you compare institutions and do not want a shared paper to be counted in full by everyone who shares it. The columns then end in `_frac`, and `P_full` is still the number of papers. `P_frac` is the sum of the fractions.

```python
oam.aggregate(ids, counting="full")   # the default
oam.aggregate(ids, counting="frac")
```

EPFL in 2014, fractional counting:

```
 publication_year  P_full  P_frac  TCS_frac  MCS_frac  MNCS_frac  PP_top10_frac  PP_top1_frac  window_is_complete
             2014    4151 2061.93   27993.0     13.58       1.89          21.56          2.97                True
```

The same 4,151 papers weigh 2,062 once each institution's share is taken. The MNCS is 1.89, and 21.6% are in the top 10%.

## Which papers are scored

EPFL's name is on 5,788 works from 2014, and 4,213 are in scope. Two things remove papers from the summary.

**Scope.** Only papers inside the scope get a score: by default, English-language articles in a core journal or a conference proceedings, with an institutional affiliation, a subfield and a reference list, and not retracted or front matter. 4,213 of the 5,788 pass. The rest are preprints, datasets, book chapters and similar. Scope also decides which citations count: a citation is only counted when the citing paper is itself in scope. See [Scope](scope.md) for the rules, and [Querying](querying.md#which-papers-are-scored) to see which rule removed which paper.

**Small cohorts.** A cohort with fewer than 500 papers has an unstable average. Its papers still get an `ncs_4y`, with `cohort_is_reliable` set to false, but `aggregate` leaves them out. 62 of the 4,213 are in such cohorts. To keep them, pass `reliable_only=False`.

## Citation window

Every score uses one window: the publication year and the three following years. A 2014 paper is cited by papers from 2014, 2015, 2016 and 2017. This is the window OpenAlex and Scopus use for the field-weighted citation impact. It is not the window of the Leiden Ranking, which counts citations until the end of the year after a multi-year period.

`window_is_complete` is true when those four years are already over, which is when the publication year plus three is earlier than the snapshot's year. On the snapshot of 14 October 2026, that is every paper from 2022 and before. Papers from 2023, 2024 and 2025 are still scored, and `window_is_complete` is false: their `citations_4y` is whatever has arrived so far, not a four-year count. A row of `aggregate` has `window_is_complete` true only when every paper in it does.

Counted up to the snapshot, and with self-citations kept, the MNCS for EPFL in 2014 was 1.94. With the four-year window and self-citations removed, it is 2.13.

## Self-citations

A self-citation is a citation where the citing paper and the cited paper share an OpenAlex author. Those citations are removed from `citations_4y`, and so from every score. There is no option to keep them. `cited_by_count_in_scope` still includes them, because that column is the in-scope part of OpenAlex's own count rather than a score.

Inside a complete four-year window, 19.7% of the in-scope citations are self-citations. Those are the ones left out of `citations_4y`.

This is the same practice as the Leiden Ranking. The OpenAlex and Scopus field-weighted citation impact both keep self-citations.

Two caveats. OpenAlex decides which names are the same person, and that decision is wrong in both directions: some real self-citations stay in, and some citations by a different person are removed. A paper with thousands of authors can lose most of its citations, because a citing paper from the same collaboration almost always shares an author.

## OAM and OpenAlex

OpenAlex publishes an `fwci` and a `citation_normalized_percentile` on the work itself. OAM copies both onto the score table, under the `openalex_` names, and computes its own. They answer similar questions and they are not the same number.

The population differs. OAM scores English-language articles in a core journal or a conference proceedings, with an affiliation, a subfield and a reference list, and it counts a citation only when the citing paper is in that set too. OpenAlex computes the FWCI for articles, conference articles, reviews, books and book chapters, and it counts a citation from any work in OpenAlex.

The cohort differs. OAM groups by publication year, subfield and venue type, and the venue type is journal or conference. OpenAlex groups by publication year, work type and subfield, and it splits articles into journal articles and conference articles.

| OAM | OpenAlex | What differs |
|---|---|---|
| `citations_4y` | the count behind `fwci` | Both use the publication year and the three following years. OAM drops self-citations and counts only in-scope citing papers. OpenAlex keeps self-citations and counts every citing work |
| `ncs_4y` | `fwci` | Both divide the count by the mean of the group. The groups and the counts differ, as above |
| `citation_normalized_percentile_4y` | `citation_normalized_percentile.value` | OAM uses the mid-rank of `citations_4y`. OpenAlex uses all-time citations and `(rank − 1) / (max rank + 1)`, with tied papers taking the lowest rank, so an uncited paper is 0 |
| `is_in_top_10_percent_4y` | `citation_normalized_percentile.is_in_top_10_percent` | OAM flags every paper at or above the cohort's 90th percentile that has at least one citation. OpenAlex flags from its own percentile |
| `is_in_top_1_percent_4y` | `citation_normalized_percentile.is_in_top_1_percent` | The same, at the 99th percentile |
| `cited_by_count_in_scope` | part of `cited_by_count` | In-scope citing papers, all years, self-citations kept |
| `cited_by_count_outside_scope` | the rest of `cited_by_count` | OpenAlex's count minus the in-scope count. The difference is clamped at 0 |

Where both scores are present, on a complete window and a reliable cohort, `ncs_4y` and `openalex_fwci` have a correlation of 0.70. OpenAlex marks the top 10% from a percentile of 0.90 and the top 1% from 0.99. On this snapshot, 102 papers have more in-scope citations than OpenAlex's `cited_by_count`, and their `cited_by_count_outside_scope` is 0.

OpenAlex's description of these fields is in its help center, and the calculation is in its 2025 metric code.
