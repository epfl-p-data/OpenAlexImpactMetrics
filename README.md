# OAM — citation impact from OpenAlex

[![PyPI](https://img.shields.io/pypi/v/openalex-metrics)](https://pypi.org/project/openalex-metrics/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23493154.svg)](https://doi.org/10.5281/zenodo.23493154)

OAM computes citation impact metrics for any set of papers: a university, a researcher, a country, a funder's portfolio or a list of DOIs. It tells you whether those papers were cited more or less than comparable papers worldwide. It runs on your own machine, and the data comes from [OpenAlex](https://openalex.org).

OAM returns those metrics keyed by OpenAlex work id. Titles, authors, venues and any other descriptive field come from OpenAlex itself, joined on `work_id`.

Comparing papers is only fair within a well-defined scope. OpenAlex indexes almost everything, not only research papers but also preprints, datasets, theses and editorials, and these are cited very differently. OAM keeps only peer-reviewed research papers, about 13% of OpenAlex. Only these papers are scored, and only the citations they make are counted. [Which papers are scored](#which-papers-are-scored) lists the rules.

## How this differs from OpenAlex

OpenAlex already gives most works a citation score, the FWCI. It compares each work with everything OpenAlex indexes, and it counts citations from anything, including preprints, datasets and theses. OAM compares a paper only with papers in its scope, and counts only citations from that scope.

A paper is compared with its cohort: the same subfield, the same publication year, and journal or conference. OpenAlex groups by year, work type and subfield, and splits articles into journal and conference articles. The download counts every citation up to the snapshot, self-citations included. The FWCI also keeps self-citations, but counts only the publication year and the three following years. The two scores are stored side by side. [OAM and OpenAlex](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md#oam-and-openalex) lists each column and the difference.

## Get started

Python 3.11 or newer.

```bash
pip install openalex-metrics
```

Download the data once. This fetches the latest release from [Zenodo](https://doi.org/10.5281/zenodo.23493154), 11.9 GiB, and checks every file:

```python
import oam

oam.download()
```

The files go to `~/oam-data`, or to `./data` when you work from a clone of this repository. To put them somewhere else, call `oam.set_data_dir(path)` before downloading, or set the `OAM_DATA_DIR` environment variable. You need about 25 GB free while the archive is unpacked, and 12 GB once it is installed.

The citation links are a separate file on the same record, 9.2 GiB. `oam.download(edges=True)` installs them. Each link is one paper citing another, with the citing paper's publication year. That is enough to see who cites a paper, count citations by citing year, flag a self-citation, and list the references a paper makes. The scores are the same either way. You need about 20 GB free while the links are unpacked, and 9.2 GiB once they are installed.

Then score a set of papers:

```python
ids = oam.resolve(institution="I5124864", year=(2020, 2024))  # EPFL
oam.aggregate(ids).round(2)
```

`oam.resolve` finds the papers. `oam.aggregate` scores the ones that can be compared with similar papers. To find an institution's id, search by name:

```python
oam.dimension("institutions", search="Lausanne")
```

## How the pieces fit

```text
 a question            oam.dimension(...)      names → ids          (institution, funder, source, topic)
       │
       ▼
 the works             oam.resolve(...)        every work matching, scored or not  → list of integer work ids
       │                                       (or your own ids or DOIs)
       ├──────────────▶ oam.explain(ids)       per work: found, in scope, which rule failed
       ├──────────────▶ oam.get_metrics(ids)   per work: citations, ncs, percentile, top flags, OpenAlex FWCI
       ▼
 the indicators        oam.aggregate(ids)      per group: P, TCS, MCS, MNCS, PP(top 10%), PP(top 1%)
```

`resolve` keeps every matching work. `explain` and `get_metrics` report on each one. `aggregate` counts a work when it is scored and its cohort has at least 500 papers. EPFL in 2023 shows each drop:

| Step | Works | Call |
|---|---|---|
| resolved | 7,763 | `len(oam.resolve(institution="I5124864", year=2023))` |
| in scope (passes the seven rules) | 4,508 | `oam.explain(ids).in_scope.sum()` |
| scored | 4,508 | `oam.get_metrics(ids).scored.sum()` |
| counted by `aggregate` | 4,470 | `oam.aggregate(ids).P_full` |

The last 38 papers sit in cohorts smaller than 500. `aggregate` leaves them out while `reliable_only=True`, which is the default.

`resolve` returns plain integers, such as `[771562940, 1530463068]`. Every function also accepts `W…` ids. `aggregate` accepts a list, a set, a pandas Series or a NumPy array. From the frame `get_metrics` returns, pass `df.work_id`. Nothing calls the network after the download. Titles, DOIs and authors are not in the data. Resolving and scoring EPFL over five years, or a country over a year, takes under a second.

## Three examples

These numbers come from the OpenAlex snapshot of 14 October 2026.

### A university, over five years

EPFL, 2020 to 2024. Of the 36,475 works with EPFL's name on them, 22,207 are scored.

```python
ids = oam.resolve(institution="I5124864", year=(2020, 2024))
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full
             2020    4433  200062.0     45.13       1.95          21.52          3.41
             2021    4498  150841.0     33.54       1.80          20.08          3.11
             2022    4349  120403.0     27.69       1.89          20.74          2.90
             2023    4470   85462.0     19.12       1.74          20.25          3.02
             2024    4457   58542.0     13.13       1.83          21.11          3.43
```

### A researcher

Jennifer Doudna, from her [ORCID](https://orcid.org/0000-0001-9161-999X). A researcher is selected by ORCID or by OpenAlex author id. Of 755 works, 391 are scored. `by=None` gives one row for the whole set.

```python
ids = oam.resolve(orcid="0000-0001-9161-999X")
oam.aggregate(ids, by=None).round(2)
```

```
 P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full
    391   91886.0     235.0       5.97          59.34         14.83
```

### A country

Switzerland, 2020 to 2024. Of 490,707 works, 265,954 are scored.

```python
ids = oam.resolve(country="CH", year=(2020, 2024))
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full
             2020   52142 2108683.0     40.44       1.80          18.78          2.87
             2021   55231 1744205.0     31.58       1.78          18.79          3.02
             2022   53311 1206510.0     22.63       1.71          18.77          2.88
             2023   52502  887357.0     16.90       1.71          19.19          3.08
             2024   52768  605921.0     11.48       1.75          19.27          2.99
```

You can select papers the same way by author, funder, journal, field or DOI. [Querying](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/querying.md) lists every option. [Cookbook](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/cookbook.md) puts a question next to the call that answers it: two institutions, a funder, a journal, co-publications.

## Reading the numbers

- **P_full** is the number of papers that received a score.
- **MNCS_full** compares those papers with similar papers worldwide. The download counts every citation up to the snapshot, self-citations included. The world average is 1. A value of 2 means cited twice as much.
- **PP_top10_full** is the share of papers among the 10% most cited of their cohort. For the world it sits a little above 10%, because papers tied at the threshold are all counted in. **PP_top1_full** is the same for the top 1%.

A recent paper has had less time to be cited. It is compared only with papers from its own year, so MNCS and the top shares can be read across years, while TCS and MCS fall for recent years. For a small institution, compare full and fractional counting before quoting an MNCS. A set of a few dozen papers gives numbers with a wide margin. [When the two disagree](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md#when-full-and-fractional-counting-disagree) shows a university whose shared papers carry most of the citations. To count a fixed number of years, or to drop self-citations, the data is rebuilt with that choice. [Building](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/building.md#citation-window-and-self-citations) shows the arguments.

[How the scores work](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md) explains the other columns, and full and fractional counting.

## Which papers are scored

A paper is scored when it passes all seven rules. On the snapshot of 14 October 2026, 62,493,369 of 464,765,485 works pass, which is 13.45%.

1. It is in a core journal or a conference proceedings.
2. It has a reference list.
3. It has at least one institutional affiliation.
4. It is in English.
5. It has an OpenAlex subfield.
6. It is not paratext, such as an editorial, erratum or table of contents.
7. It is not retracted.

Each paper is then compared with papers of the same subfield, publication year and venue type (journal or conference).

These rules are fixed for the data you download. To score a different set of papers, for example to keep papers in every language, edit the rules and rebuild the data. [Scope](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/scope.md) explains each rule, and [Building the data](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/building.md) explains the rebuild.

## Data and licence

The data is built from the public OpenAlex snapshot and released under CC0. `oam.download()` installs the latest version. To keep one snapshot, pass its Zenodo record id, listed under Versions on the [Zenodo page](https://doi.org/10.5281/zenodo.23493154):

```python
oam.download(record=23770181)    # the 2026-10-14 snapshot, built with OAM 0.2
```

Record 23493155 is the same snapshot date built with OAM 0.1. OAM 0.2 and later do not read it.

If you publish results, cite the version you used. Each version has its own DOI, and `oam.status()` shows which snapshot is installed.

OAM is released under the MIT licence. See [LICENSE](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/LICENSE).
