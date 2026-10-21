# OAM — citation impact from OpenAlex

[![PyPI](https://img.shields.io/pypi/v/openalex-metrics)](https://pypi.org/project/openalex-metrics/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23493154.svg)](https://doi.org/10.5281/zenodo.23493154)

OAM tells you whether a set of papers was cited more or less than comparable papers. You can score a university, a researcher, a country, a funder's portfolio or a list of DOIs. It runs on your own machine, and the data comes from [OpenAlex](https://openalex.org).

## Get started

Python 3.11 or newer.

```bash
pip install openalex-metrics
```

Download the data once. This fetches the latest release from [Zenodo](https://doi.org/10.5281/zenodo.23493154), 11.8 GiB, and checks every file:

```python
import oam

oam.download()
```

The files go to `~/oam-data`. To put them somewhere else, call `oam.set_data_dir(path)` before downloading, or set the `OAM_DATA_DIR` environment variable. You need about 25 GB free while the archive is unpacked.

Then score a set of papers:

```python
ids = oam.resolve(institution="I5124864", year=(2020, 2024))  # EPFL
oam.aggregate(ids).round(2)
```

`oam.resolve` finds the papers. `oam.aggregate` scores the ones that can be compared with similar papers. To find an institution's id, search by name:

```python
oam.dimension("institutions", search="Lausanne")
```

## Three examples

These numbers come from the OpenAlex snapshot of 14 October 2026.

### A university, over five years

EPFL, 2020 to 2024. Of the 36,475 works with EPFL's name on them, 22,207 are scored.

```python
ids = oam.resolve(institution="I5124864", year=(2020, 2024))
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full  window_is_complete
             2020    4433   89507.0     20.19       1.86          20.53          3.09                True
             2021    4498   81217.0     18.06       1.74          19.05          2.82                True
             2022    4349   81253.0     18.68       1.85          19.29          2.67                True
             2023    4470   68835.0     15.40       1.68          18.59          2.77               False
             2024    4457   47310.0     10.61       1.76          19.14          3.14               False
```

### A researcher

Jennifer Doudna, from her [ORCID](https://orcid.org/0000-0001-9161-999X). Of 755 works, 391 are scored. `by=None` gives one row for the whole set.

```python
ids = oam.resolve(orcid="0000-0001-9161-999X")
oam.aggregate(ids, by=None).round(2)
```

```
 P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full  window_is_complete
    391   27140.0     69.41       6.23          61.64         18.41               False
```

`window_is_complete` is false because the set includes papers whose four years have not elapsed.

### A country

Switzerland, 2020 to 2024. Of 490,707 works, 265,954 are scored.

```python
ids = oam.resolve(country="CH", year=(2020, 2024))
oam.aggregate(ids).round(2)
```

```
 publication_year  P_full  TCS_full  MCS_full  MNCS_full  PP_top10_full  PP_top1_full  window_is_complete
             2020   52142  966664.0     18.54       1.71          17.67          2.63                True
             2021   55231  944519.0     17.10       1.71          17.51          2.73                True
             2022   53311  801017.0     15.03       1.64          17.21          2.61                True
             2023   52502  722339.0     13.76       1.65          17.44          2.75               False
             2024   52768  494686.0      9.37       1.68          17.55          2.73               False
```

You can select papers the same way by author, funder, journal, field or DOI. [Querying](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/querying.md) lists every option.

## Reading the numbers

- **P_full** is the number of papers that received a score.
- **MNCS_full** compares those papers with similar papers worldwide, using citations from the publication year and the three following years, without self-citations. The world average is 1. A value of 2 means cited twice as much.
- **PP_top10_full** is the share of papers among the 10% most cited of their cohort. For the world it sits a little above 10%, because papers tied at the threshold are all counted in. **PP_top1_full** is the same for the top 1%.
- **window_is_complete** is true when every paper in that row has had its four years. Recent years are scored anyway, and the flag says the count is not a four-year count yet.

[How the scores work](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md) explains the other columns, and full and fractional counting.

## How this differs from OpenAlex

OpenAlex already gives most works a citation score, the FWCI. It compares each work with everything OpenAlex indexes, and it counts citations from anything, including preprints, datasets and theses. OAM compares a paper only with peer-reviewed research papers like it, and counts only the citations that come from such papers.

A paper is compared with its cohort: the same subfield, the same publication year, and journal or conference. OpenAlex groups by year, work type and subfield, and splits articles into journal and conference articles. OAM also drops self-citations, which the OpenAlex and Scopus FWCI keep. The two scores are stored side by side. [OAM and OpenAlex](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md#oam-and-openalex) lists each column and the difference.

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
oam.download(record=23493155)    # always the 2026-10-14 snapshot
```

If you publish results, cite the version you used. Each version has its own DOI, and `oam.status()` shows which snapshot is installed.

OAM is released under the MIT licence. See [LICENSE](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/LICENSE).
