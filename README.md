# OAM — scoped citation impact from OpenAlex

OAM stands for **OpenAlex Metrics**. It tells you whether a set of papers was cited more or less than comparable papers worldwide. It computes the standard indicators of bibliometrics, MNCS, PP(top 10%) and PP(top 1%), for any set of papers: a university, a lab, a funder's portfolio or a list of DOIs. Everything comes from [OpenAlex](https://openalex.org) and runs on your own machine.

## Why OAM

OpenAlex already gives most papers a field-normalised score, [`fwci`](https://help.openalex.org/data/works/citations/) and `citation_normalized_percentile`. Those scores compare a paper with every OpenAlex work of the same type, year and subfield. That pool is wide: an "article" can be in a journal outside the core list, have no reference list or no affiliation, or be in any language. The citations counted come from everything OpenAlex indexes, including preprints, datasets and theses. And the population is fixed by OpenAlex: you cannot change it.

OAM recomputes the same kind of scores against a set of papers you define, the **scope**. By default, that is peer-reviewed research papers in English. A paper is compared only with papers in the scope, and a citation counts only when the citing paper is in the scope as well.

The aim is a reference that any institution can use and check:

- **Open data.** Built from the public OpenAlex snapshot. No licence, account or API key.
- **Transparent rules.** The scope is seven lines of SQL that you can read, and change.
- **Reproducible.** Every result comes from one dated snapshot, so the same query gives the same answer next month.
- **Ready to use.** One download, and a university's indicators take a few lines of Python.

## The default scope

A paper is scored when it passes all seven rules. On the OpenAlex snapshot of 2026-09-23, 62,928,576 of 476,196,327 works pass: 13.2%.

| Rule | Keeps |
|---|---|
| `is_core_or_proceedings` | Papers in a core journal or a conference proceedings |
| `has_referenced_works` | Papers with a reference list |
| `has_authorship` | Papers with at least one institutional affiliation |
| `is_english` | Papers in English |
| `has_subfield` | Papers with an OpenAlex subfield |
| `is_not_paratext` | Papers that are not editorials, errata or tables of contents |
| `is_not_retracted` | Papers that are not retracted |

Every paper is then compared with the papers that share its **subfield**, **publication year** and **venue type** (journal or conference). [Scope](docs/scope.md) gives the SQL of each rule and how many works each one removes.

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

The data is prebuilt with the default scope. Download it once:

```python
import oam

oam.download()    # 12.1 GiB
```

The files go to `~/oam-data`. To store them somewhere else, call `oam.set_data_dir(path)` before downloading, or set the `OAM_DATA_DIR` environment variable. The install needs about 25 GiB free while the archive is unpacked, and keeps about 12 GiB.

## Score your institution

Find your institution's OpenAlex id:

```python
oam.dimension("institutions", search="Lausanne")
```

```
 institution_id                              display_name country_code
        5124864  École Polytechnique Fédérale de Lausanne           CH
       97565354                     University of Lausanne           CH
            ...
```

Then select its papers and score them:

```python
ids = oam.resolve(institution="I5124864", year=2014)   # EPFL's 2014 papers
oam.aggregate(ids)
```

```
 publication_year     P  P_weighted     TCS    MCS   MNCS  PP_top10  PP_top1
             2014  4439      4439.0  264771  59.65  1.962     21.83     3.18
```

EPFL's name is on 6,223 works from 2014. 4,439 of them are in scope and get a score (`P`). On average they were cited 1.96 times as much as comparable papers (`MNCS`; the world average is 1). 21.8% of them are among the 10% most cited papers of their bin (`PP_top10`; the world share is about 10%).

You can also score a list of OpenAlex work ids, or select papers by country, author, funder, journal, field or DOI. See [Querying](docs/querying.md).

## Change the scope

The default rules are a choice, not a law. If your institution publishes much of its work in French or German, for example, you may want to keep non-English papers.

A rule is one entry of `SCOPE` in [`oam/config.py`](oam/config.py). To keep every language, delete this entry from a clone of the repository:

```python
"is_english": (
    "lower(COALESCE(language, '')) = 'en'",
    "not English"),
```

Then rebuild the data from the OpenAlex snapshot:

```python
import oam

oam.build(reset=True)
```

The build downloads about 67 GB from the public snapshot and takes a few hours, set mostly by your bandwidth. Every score is then recomputed against the new scope: both the papers compared and the citations counted. You can also add a rule, or rewrite one; each rule is a line of SQL over the OpenAlex works data.

[Building the data](docs/building.md) covers requirements, checks, moving to a newer snapshot, and publishing your own data release so others can download it.

## Documentation

- [How the scores work](docs/metrics.md): what MNCS, PP(top 10%) and the other columns mean, full and fractional counting, and why some papers are not scored.
- [Querying](docs/querying.md): choosing papers, checking which ones are scored, per-paper scores, grouping.
- [Scope](docs/scope.md): the seven rules, how many works each removes, and how to write your own.
- [Building the data](docs/building.md): rebuilding from the OpenAlex snapshot, changing the scope, publishing a release.

## Data and licence

The data is derived from [OpenAlex](https://openalex.org), which is released under CC0. If you publish results, cite the data release you used.

OAM is released under the MIT licence. See [LICENSE](LICENSE).
