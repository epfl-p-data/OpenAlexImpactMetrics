# How the scores work

OAM compares every paper with papers like it, then summarises those comparisons for a set of papers. This page starts with one paper, then moves to a set.

## Comparable papers: the bin

A paper is compared with the papers that share three things with it:

- its **subfield**, as assigned by OpenAlex (for example Artificial Intelligence)
- its **publication year**
- its **venue type**: journal article or conference paper

That group is the paper's **bin**. Citation habits differ a lot between fields and years, so comparing within the bin is what makes a mathematics paper from 2014 comparable with a biology paper from 2022.

## One paper

Take `W2103094658`, a 2014 conference paper in Artificial Intelligence. It has 37 citations. Its bin holds 14,122 papers, which have about 18 citations on average.

```python
oam.get_metrics(["W2103094658"])
```

```
 citations   ncs   percentile   decile   is_top10   is_top1
        37  2.05         0.94        1       True     False
```

| Column | Meaning | This paper |
|---|---|---|
| `citations` | Citations received so far | 37 |
| `ncs` | Citations divided by the bin's average. 1 means exactly average | 2.05: twice the average |
| `percentile` | Share of the bin with as many citations or fewer, from 0 to 1 | 0.94: ahead of about 94% of its bin |
| `decile` | Rank in tenths of the bin: 1 is the top tenth, 10 the bottom | 1: top tenth |
| `is_top10` | Among the 10% most cited papers of the bin | yes |
| `is_top1` | Among the 1% most cited papers of the bin | no |

`ncs` stands for *normalised citation score*. It is the number the set-level MNCS is built from.

## A set of papers

For a set of papers — an institution, a lab, a funder's portfolio — OAM summarises the per-paper scores:

| Column | Meaning |
|---|---|
| `P` | Number of papers scored |
| `MNCS` | Average `ncs`. 1 is the world average |
| `PP_top10` | Percentage of papers with `is_top10`. About 10 for the world |
| `PP_top1` | Percentage of papers with `is_top1`. About 1 for the world |
| `TCS` | Total citations |
| `MCS` | Average citations per paper, not normalised |
| `P_weighted` | Sum of the paper weights. Equals `P` unless you use fractional counting (below) |

EPFL in 2014:

```python
ids = oam.resolve(institution="I5124864", year=2014)
oam.aggregate(ids)
```

```
 publication_year     P  P_weighted     TCS    MCS   MNCS  PP_top10  PP_top1
             2014  4439      4439.0  264771  59.65  1.962     21.83     3.18
```

`aggregate` returns one row per publication year by default. Pass `by=None` for a single row covering the whole set.

- MNCS 1.96: on average, an EPFL paper from 2014 was cited 1.96 times as much as a typical paper of its bin.
- PP(top 10%) 21.8%: more than twice the world share.
- PP(top 1%) 3.2%: about three times the world share.

The percentile and the decile are per-paper columns. The summary does not average them; PP(top 10%) and PP(top 1%) are how the ranking shows up at the set level.

The world values, measured on the 2014–2023 papers: MNCS 1.00, PP(top 10%) 10.27%, PP(top 1%) 1.01%. The top-10% share is slightly above 10 because papers tied at the threshold are all counted in.

## Full and fractional counting

By default every paper counts once, whatever the number of institutions on it. This is **full counting**, and it is what the EPFL numbers above use.

With **fractional counting**, a paper counts as `1 / number of institutions`. A paper written with nine other institutions counts as a tenth of a paper for EPFL. Use it when you compare institutions and do not want a shared paper to be counted in full by everyone who shares it.

```python
oam.aggregate(ids, counting="full")   # the default
oam.aggregate(ids, counting="frac")
```

EPFL in 2014, fractional counting:

```
 publication_year     P  P_weighted     TCS    MCS   MNCS  PP_top10  PP_top1
             2014  4439      2118.3  108728  51.33  1.738     19.15     2.72
```

`P` is still 4,439 papers. `P_weighted` is the sum of the fractions, 2,118. Every other column is a weighted average, so papers shared with many institutions weigh less. Here MNCS falls from 1.96 to 1.74, because EPFL's papers with five or more institutions are also its most cited: their average `ncs` is 2.67, against 1.75 for the others.

## Which papers are scored

EPFL's name is on 6,223 works from 2014, and 4,439 are scored. Two things remove the others.

**Scope.** Only papers inside the scope get a score: by default, English-language articles in a core journal or a conference proceedings, with an institutional affiliation, a subfield and a reference list, and not retracted or front matter. 4,511 of the 6,223 pass. The rest are preprints, datasets, book chapters and similar. Scope also decides which citations count: a citation is only counted when the citing paper is itself in scope. See [Scope](scope.md) for the rules, and [Querying](querying.md#which-papers-are-scored) to see which rule removed which paper.

**Small bins.** A bin with fewer than 500 papers has an unstable average. Its papers still get an `ncs`, with `bin_is_reliable` set to false, but `aggregate` leaves them out. 72 of the 4,511 are in such bins. To keep them, pass `reliable_only=False`.

## Citation window

By default a paper's citations are everything it has received up to the snapshot. A paper from 2014 has had twelve years to collect citations and a paper from 2023 only three, although both are compared within their own year, so the scores stay comparable.

To count only the citations received within three years of publication, pass `window=3`. This is the usual choice when you compare recent years with older ones.

```python
oam.aggregate(ids, window=3)
```
