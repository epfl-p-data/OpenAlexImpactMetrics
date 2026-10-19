# OAM — citation impact for OpenAlex papers

OAM is a Python library that tells you whether a set of papers was cited more or less than comparable papers worldwide. Comparable means the same field, the same publication year and the same kind of venue (journal or conference).

You give it papers: a list of OpenAlex ids or DOIs, or a filter such as an institution and a year. It returns the standard indicators of bibliometrics — MNCS, PP(top 10%) and PP(top 1%) — for each paper and for the whole set. The data comes from [OpenAlex](https://openalex.org) and everything runs on your machine.

## Install

Python 3.11 or newer.

```bash
pip install openalex-metrics
```

Or, from a clone of this repository:

```bash
pip install .
```



## Get the data

OAM reads a prebuilt copy of the metric tables. Download it once:

```python
import oam

oam.download()    # about 12 GB
```

The files go to `~/oam-data`. To store them somewhere else, call `oam.set_data_dir(path)` before downloading, or set the `OAM_DATA_DIR` environment variable.

The data is the OpenAlex snapshot of 2026-09-23, with the default [scope](docs/scope.md). To use another snapshot or another scope, you build the data yourself: see [Building the data](docs/building.md).

## Quick start

```python
import oam

ids = oam.resolve(institution="I5124864", year=2014)   # EPFL's 2014 papers
oam.aggregate(ids)
```

```
 publication_year     P  P_weighted     TCS    MCS   MNCS  PP_top10  PP_top1
             2014  4439      4439.0  264771  59.65  1.962     21.83     3.18
```

EPFL's 2014 papers were cited about twice as much as comparable papers: MNCS is 1.96, and the world average is 1. 21.8% of them are among the 10% most cited papers of their field and year, where the world share is about 10%.

You can also pass OpenAlex ids or DOIs directly. [Querying](docs/querying.md) covers the ways to choose papers.

## Documentation

- [How the scores work](docs/metrics.md) — what MNCS, PP(top 10%) and the other columns mean, full and fractional counting, and why some papers are not scored.
- [Querying](docs/querying.md) — choosing papers, checking which ones are scored, per-paper scores, grouping.
- [Building the data](docs/building.md) — rebuilding from the OpenAlex snapshot, changing the scope, publishing a release.
- [Scope](docs/scope.md) — the seven rules that decide which papers count.



## Data and licence

The data is derived from [OpenAlex](https://openalex.org), which is released under CC0. If you publish results, cite the Zenodo DOI of the data release you used.

OAM is released under the MIT licence. See [LICENSE](LICENSE).