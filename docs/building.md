# Building the data

Most users download the data with `oam.download()` and never build it. Build it yourself when you want:

- a different scope, for example keeping non-English papers
- a newer OpenAlex snapshot than the published release
- to publish a release for others to download

## What you need

- `pip install "openalex-metrics[build]"`. The `build` extra installs PyArrow, which step 1 uses to read the snapshot. Downloading the data does not need it.
- About 60 GB of free disk space during the build, and about 23 GB once it is finished
- A few hours. The first step downloads about 67 GB from the public OpenAlex snapshot, so its duration is set by your bandwidth: about 1 hour 40 minutes at 100 Mbit/s
- No account or API key. The snapshot is public

## Build

```python
import oam

oam.set_data_dir("/Volumes/data/oam")   # optional: somewhere with enough space
oam.build()
oam.status()
```

`oam.build()` can be interrupted and called again; it continues where it stopped. Everything it prints is also saved under `logs/` in the data directory.

It runs three steps in order:

| Step | What it does | Time |
|---|---|---|
| `"store"` | Reads the OpenAlex snapshot, applies the scope, keeps the citations between in-scope papers | Your bandwidth |
| `"metrics"` | Computes citation counts, bin averages and every paper's scores | About 2 minutes |
| `"resolve"` | Builds the lookup tables `oam.resolve` uses | About 25 minutes |

To run one step on its own, pass `steps="metrics"`, or a list such as `steps=["metrics", "resolve"]`.

The download uses 8 parallel connections. If your connection is fast, raise it until the speed in the log stops improving; each connection uses about 1 GB of memory:

```python
oam.build(workers=16)
```

## Check the result

```python
oam.check()      # consistency checks, a few seconds
oam.profile()    # what the data contains: scope funnel, citations, world averages
```

`oam.profile()` must show a world MNCS of **1.0000**. That confirms each paper was divided by its own bin's average.

## Change the scope

1. Edit `SCOPE` in [`oam/config.py`](../oam/config.py). Each rule is a line of SQL. [Scope](scope.md) documents the defaults and how to write a rule.
2. Rebuild from scratch:

   ```python
   oam.build(reset=True)
   ```

A scope cannot be changed on existing data: the build stored only the papers and citations the old scope kept. `oam.build()` detects an edited scope and stops with a message rather than mixing the two.

## Move to a new snapshot

OpenAlex publishes a new snapshot about once a quarter. To use it:

```python
oam.build(reset=True, refresh_manifest=True)
```

`refresh_manifest=True` fetches the list of files of the current snapshot. `reset=True` deletes the data built from the previous one. Data from two snapshots cannot be merged.

## Publish a release

From a finished build, write the archive `oam.download()` installs:

```python
oam.export("dist")    # dist/oam-<snapshot>.tar, 12.1 GiB for the 2026-09-23 snapshot
```

Then:

1. Upload the file to [Zenodo](https://zenodo.org) under the CC0 licence. For a new snapshot, add a new version to the existing record. Keep the file name `oam.export` wrote: `oam.download()` finds the file whose name starts with `oam-` and ends with `.tar`.
2. Set `ZENODO_RECORD` in [`oam/config.py`](../oam/config.py) to the id of the record. `oam.download()` fetches from that record.
3. Publish the new version of the package.

The archive records its snapshot date, its scope and a checksum of every file. `oam.download()` checks all three. It refuses to mix two snapshots in one data directory unless called with `replace=True`.

To share a build without Zenodo, send the archive file itself. The recipient installs it with:

```python
oam.download(source="oam-2026-09-23.tar")
```

## Other settings

| Environment variable | Default | Effect |
|---|---|---|
| `OAM_DATA_DIR` | `~/oam-data`, or `./data` in a clone | Where the data is stored |
| `OAM_MEMORY_LIMIT` | `20GB` | Memory DuckDB may use in the metrics and resolve steps |
| `OAM_THREADS` | `8` | Threads DuckDB may use |

Set them in the environment before starting Python.

To try the whole build on a few files first. The result is not usable for scoring:

```python
oam.set_data_dir("/tmp/oam-trial")
oam.build(steps="store", limit=3, smallest=True)
```

To keep the xpack records, a third of OpenAlex that the default build leaves out of the comparison, use `oam.build(include_xpack=True)`. See [Scope](scope.md#what-is-not-configurable).

## Release checklist

The uploads themselves are done by hand.

1. `oam.export("dist")` from a finished build, then install that archive into an empty directory and re-run the examples in the README.
2. Upload `dist/oam-<snapshot>.tar` to [Zenodo](https://zenodo.org) under the CC0 licence. For a new snapshot, add a new version of the existing record. Keep the file name `oam.export` wrote.
3. Set `ZENODO_RECORD` in [`oam/config.py`](../oam/config.py) to the record id.
4. Put the Zenodo DOI in [`CITATION.cff`](../CITATION.cff) and in the README, under Data and licence.
5. Check the package, tag the version, and upload it:

   ```bash
   python -m build
   twine check dist/*
   twine upload dist/*
   ```
