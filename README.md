# OAM

OAM answers a single question about a set of papers: **were they cited more or less than comparable papers?**

Comparable means the same field, the same publication year, and the same kind of venue (a journal or a conference). The answer comes back as a few numbers. This is EPFL in 2014, counting a paper shared with other institutions as a fraction of a paper:

```
   P   P_weighted      TCS    MCS   MNCS   PP_top10  PP_top1
4439       2118.3   108728  51.33  1.738      19.15     2.72
```

- **P** — 4,439 papers were scored. EPFL's name is on 6,223 works that year; the rest are outside the scope below, or sit in a group too small to score stably.
- **MNCS** — 1.74 means cited 74% more than the world average for papers of the same field, year and venue type. The world average is 1.
- **PP(top 10%)** — 19% of these papers are in the top 10% of their group. Across the world that share is 10.3%, a little over 10 because papers with the same citation count are kept together.
- **PP(top 1%)** — 2.7% are in the top 1%. The world share is 1.0%.
- **MCS** — 51 citations per paper, on average. **TCS** is the total of those citations. **P_weighted** is P after shared papers have been split.

## The ideas behind the numbers

**A bin** is the group a paper is compared with: its OpenAlex subfield, its publication year, and whether it appeared in a journal or a conference. Dividing a paper's citations by its bin's average is what makes a 2014 mathematics paper comparable with a 2022 biology paper.

**Scope** is your decision about which papers belong in that comparison. It does two jobs at once. Only in-scope papers get a score, and a citation counts only when the citing paper is in scope too. The default keeps English-language articles in a core journal or a conference proceedings, with an affiliation, a subfield and a reference list, and drops retracted works and front matter. On the OpenAlex snapshot of 2026-09-23 that is **62.9 million works, 13.2%** of the corpus. The criteria, and how to change them, are in [docs/scope.md](docs/scope.md).

**Full counting** gives every paper weight 1. **Fractional counting** gives it `1 / n_institutions`, so a paper with ten institutions counts as a tenth. `counting="frac"` is the second one. `P` stays the raw paper count either way.

**A fixed window.** By default citations are everything accrued up to the snapshot. `window=3` uses only the citations that arrived within three years of publication, which is what you want when you compare recent years with older ones.

## Install

The package is `openalex-metrics`. The import is `oam`.

```bash
pip install openalex-metrics
```

From a clone of this repository, until the package is on PyPI:

```bash
pip install .
```

## Get the data, then score

Scoring reads a local copy of the metric tables. You do not need the OpenAlex snapshot unless you want to build the tables yourself.

| | What you can do | Size | Call |
|---|---|---|---|
| Lite | Score a list of OpenAlex ids, and see why a paper was left out | 2.2 GB | `oam.download()` |
| Full | Also pick papers by institution, author, ORCID, funder or DOI | about 12 GB | `oam.download("full")` |
| Your own | Rebuild with a different scope | about 23 GB stored, about 67 GB downloaded | `oam.build()` |

`oam.download()` fetches the release from Zenodo. The record id is `oam.config.ZENODO_RECORD`. A file you already have, or any https URL of one, works the same way:

```python
oam.download(source="oam-2026-09-23-lite.tar")
```

Then score. Any OpenAlex id shape is fine.

```python
import oam

ids = ["W2741809807", "https://openalex.org/W2108440715"]
oam.explain(ids)                          # in scope, or which rule kept it out
oam.aggregate(ids, counting="frac")       # one row: MNCS, PP(top 10%), PP(top 1%)
```

Picking the papers from the data, instead of bringing the ids yourself:

```python
ids = oam.resolve(institution="I5124864", year=2014)   # needs the full download
oam.aggregate(ids, by="publication_year", counting="frac")
```

`oam.build()` is the long way: it streams the public OpenAlex snapshot and derives the tables. That is how a changed scope gets published. It needs about 60 GB free and a couple of hours. See [Build the data](docs/build/README.md).

Where the files land: `~/oam-data` for a normal install, `./data` in a clone of this repository, or whatever `OAM_DATA_DIR` or `oam.set_data_dir(path)` points at.

## Data and licence

The metric tables are derived from [OpenAlex](https://openalex.org), which is CC0. The release this package scores against is the **2026-09-23** snapshot. Each Zenodo upload of `oam.export()` carries that date, the scope, and the OpenAlex licence text. Cite the Zenodo DOI of the release you used; the DOI is on the record.

OAM itself is MIT. See [LICENSE](LICENSE).

## More

- [Querying](docs/use/README.md) — filters, DOI lists, scope, and the aggregate columns.
- [Building and publishing](docs/build/README.md) — `oam.build()`, and how a new snapshot gets to Zenodo.
- [Scope](docs/scope.md) — the seven rules, and what each one removes.
