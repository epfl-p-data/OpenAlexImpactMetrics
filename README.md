# OAM — citation impact from OpenAlex

OAM tells you whether a set of papers was cited more or less than comparable papers. You can score a university, a researcher, a country, a funder's portfolio or a list of DOIs. It runs on your own machine, and the data comes from [OpenAlex](https://openalex.org).

## Get started

Python 3.11 or newer.

```bash
pip install openalex-metrics
```

Download the data once. This fetches the latest release from [Zenodo](https://doi.org/10.5281/zenodo.23493154), about 12 GB, and checks every file:

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
 publication_year    P  P_weighted      TCS   MCS  MNCS  PP_top10  PP_top1
             2020 4433      4433.0 200062.0 45.13  1.95     21.52     3.41
             2021 4498      4498.0 150841.0 33.54  1.80     20.08     3.11
             2022 4349      4349.0 120403.0 27.69  1.89     20.74     2.90
             2023 4470      4470.0  85462.0 19.12  1.74     20.25     3.02
             2024 4457      4457.0  58542.0 13.13  1.83     21.11     3.43
```

### A researcher

Jennifer Doudna, from her [ORCID](https://orcid.org/0000-0001-9161-999X). Of 755 works, 391 are scored. `by=None` gives one row for the whole set.

```python
ids = oam.resolve(orcid="0000-0001-9161-999X")
oam.aggregate(ids, by=None).round(2)
```

```
  P  P_weighted     TCS   MCS  MNCS  PP_top10  PP_top1
391       391.0 91886.0 235.0  5.97     59.34    14.83
```

### A country

Switzerland, 2020 to 2024. Of 490,707 works, 265,954 are scored.

```python
ids = oam.resolve(country="CH", year=(2020, 2024))
oam.aggregate(ids).round(2)
```

```
 publication_year     P  P_weighted       TCS   MCS  MNCS  PP_top10  PP_top1
             2020 52142     52142.0 2108683.0 40.44  1.80     18.78     2.87
             2021 55231     55231.0 1744205.0 31.58  1.78     18.79     3.02
             2022 53311     53311.0 1206510.0 22.63  1.71     18.77     2.88
             2023 52502     52502.0  887357.0 16.90  1.71     19.19     3.08
             2024 52768     52768.0  605921.0 11.48  1.75     19.27     2.99
```

You can select papers the same way by author, funder, journal, field or DOI. [Querying](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/querying.md) lists every option.

## Reading the numbers

- **P** is the number of papers that received a score.
- **MNCS** compares those papers with similar papers worldwide. The world average is 1. A value of 2 means cited twice as much.
- **PP_top10** is the share of papers among the 10% most cited of their group. For the world, that share is about 10%. **PP_top1** is the same for the top 1%.

[How the scores work](https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md) explains the other columns, and full and fractional counting.

## How this differs from OpenAlex

OpenAlex already gives most works a citation score, the FWCI. It compares each work with everything OpenAlex indexes, and it counts citations from anything, including preprints, datasets and theses. OAM compares a paper only with peer-reviewed research papers like it, and counts only the citations that come from such papers.

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
