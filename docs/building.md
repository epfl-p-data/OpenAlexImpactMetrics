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
| `"metrics"` | Computes citation counts, cohort averages and every paper's scores | About 2 minutes |
| `"resolve"` | Builds the lookup tables `oam.resolve` uses | About 6 minutes |

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

`oam.profile()` must show a world MNCS of **1.0000**. That confirms each paper was divided by its own cohort's average.

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
oam.export("dist")
# dist/oam-<snapshot>.tar    the tables oam.download() installs
```

Then upload that archive to [Zenodo](https://zenodo.org) under the CC0 licence, as a new version of the [existing record](https://doi.org/10.5281/zenodo.23493154). Set the version to the snapshot date. Keep the name `oam.export` wrote: `oam.download()` finds the one file whose name starts with `oam-` and ends with `.tar`, and reads the snapshot date from it.

The installed data has a format number, 3 in OAM 0.2. A directory written by an older OAM is refused: a query stops, and `oam.download()` fetches the current data even when the snapshot date matches. To keep using the 0.1 data, install OAM 0.1 and pin `record=23493155`.

Nothing in OAM needs to change. `ZENODO_RECORD` in [`oam/config.py`](../oam/config.py) holds the record's all-versions id, `23493154`, which Zenodo resolves to the newest version, so every install of OAM downloads the new snapshot from then on.

The archive records its snapshot date, its scope and a checksum of every file. `oam.download()` checks all three. It refuses to mix two snapshots in one data directory unless called with `replace=True`.

To share a build without Zenodo, send the archive file itself. The recipient installs it with:

```python
oam.download(source="oam-2026-10-14.tar")
```

To publish under a record of your own, set `OAM_ZENODO_RECORD` to its all-versions id, or pass `record=` to `oam.download()`.

## Other settings

| Environment variable | Default | Effect |
|---|---|---|
| `OAM_DATA_DIR` | `~/oam-data`, or `./data` in a clone | Where the data is stored |
| `OAM_ZENODO_RECORD` | `23493154`, the latest version | The Zenodo record `oam.download()` fetches. A version's own id pins that snapshot |
| `OAM_MEMORY_LIMIT` | `20GB` | Memory DuckDB may use in the metrics and resolve steps |
| `OAM_THREADS` | `8` | Threads DuckDB may use |

Set them in the environment before starting Python.

To try the whole build on a few files first. The result is not usable for scoring:

```python
oam.set_data_dir("/tmp/oam-trial")
oam.build(steps="store", limit=3, smallest=True)
```

To keep the xpack records, a third of OpenAlex that the default build leaves out of the comparison, use `oam.build(include_xpack=True)`. See [Scope](scope.md#what-is-not-configurable).

## Citation window and self-citations

The default count includes every citation up to the snapshot, self-citations included. Both are choices of the metric layers, so the data holds one set of scores and a query has nothing to switch.

A downloaded archive holds that one count. It has no store, so it cannot be rebuilt in place. Changing the window, or dropping self-citations, is a full build from the OpenAlex snapshot into another directory, with the disk and the time listed above:

```python
oam.set_data_dir("/Volumes/data/oam")
oam.build(citation_window=4, exclude_self_citations=True)
```

On a store you already built, the same change reruns step 2, about 2 minutes, and leaves the store in place:

```python
oam.build(steps="metrics", citation_window=4, exclude_self_citations=True)
```

`citation_window=4` counts the publication year and the three following years. Any integer of at least 1 is a window of that many years, starting with the publication year. `exclude_self_citations=True` drops a citation whose papers share an OpenAlex author.

The choice is part of what the data is. `oam.status()` prints it, `oam.profile()` repeats it, and `oam.export` writes it into `bundle.json`. [How the scores work](metrics.md#citation-window) says what the window and a self-citation are.

## Release checklist

The Zenodo release runs from a terminal in the repository, with `curl` and `jq`. It reads a Zenodo token from `.env`, which git ignores, as a line `ZENODO_TOKEN=...`. Create the token under Account, Applications on Zenodo, with the `deposit:write` and `deposit:actions` scopes.

1. `oam.export("dist")` from a finished build, then install that archive into an empty directory and re-run the examples in the README.
2. Create the new version. Zenodo copies the metadata of the latest version, sets the publication date to today, and starts with no file:

   ```bash
   set -a; source .env; set +a
   SNAPSHOT=2027-01-15
   VERSION="$SNAPSHOT (OAM 0.2)"
   NAME=oam-$SNAPSHOT.tar
   H="Authorization: Bearer $ZENODO_TOKEN"
   LATEST=$(curl -sfL https://zenodo.org/api/records/23493154 | jq -r .id)
   DRAFT=$(curl -sf -X POST -H "$H" https://zenodo.org/api/records/$LATEST/versions | jq -r .id)
   API=https://zenodo.org/api/records/$DRAFT/draft
   ```

3. Set the version and the collected date, and write the draft to `dist/draft.json`:

   ```bash
   curl -sf -H "$H" -H "Accept: application/vnd.inveniordm.v1+json" "$API" \
     | jq --arg v "$VERSION" --arg s $SNAPSHOT '.metadata.version = $v | .metadata.dates = [{date: $s, type: {id: "collected"}}]' \
     > dist/draft.json
   ```

   Update the snapshot figures in `metadata.description` of that file, then send it back:

   ```bash
   curl -sf -X PUT -H "$H" -H "Content-Type: application/json" -d @dist/draft.json "$API" -o /dev/null
   ```

4. Upload the archive, keeping the name `oam.export` wrote. 12 GiB takes about half an hour:

   ```bash
   curl -f -X POST -H "$H" -H "Content-Type: application/json" -d "[{\"key\":\"$NAME\"}]" "$API/files"
   curl -f -X PUT -H "$H" -H "Content-Type: application/octet-stream" --upload-file "dist/$NAME" "$API/files/$NAME/content" -o /dev/null
   curl -f -X POST -H "$H" "$API/files/$NAME/commit"
   ```

5. Check that Zenodo's checksum equals the local one, look over the draft at `https://zenodo.org/uploads/$DRAFT`, and publish. Publishing cannot be undone, and gives the version the DOI `10.5281/zenodo.$DRAFT`:

   ```bash
   curl -sf -H "$H" "$API/files" | jq -r '.entries[].checksum'
   md5 dist/$NAME
   curl -sf -X POST -H "$H" "$API/actions/publish" -o /dev/null
   ```

   To abandon a draft instead, `curl -f -X DELETE -H "$H" "$API"`.
6. Add the snapshot to the README figures. `ZENODO_RECORD` does not change: it already points at the newest version.

A new version of the package is only needed when the code changes. A new snapshot does not need one: `oam.download()` always fetches the latest version on Zenodo.

## Release the package

Publishing happens from GitHub, with no token stored in the repository. [PyPI](https://pypi.org/project/openalex-metrics/) trusts the workflow `publish.yml` of `epfl-p-data/OpenAlexImpactMetrics`, running in the environment `pypi`. Renaming the workflow or the repository breaks that link until it is updated on PyPI.

For each release:

1. Set `version` in [`pyproject.toml`](../pyproject.toml) and in [`CITATION.cff`](../CITATION.cff), and push.
2. On GitHub, create a release named `v` followed by that version, for example `v0.2.0`.

The [publish workflow](../.github/workflows/publish.yml) builds the package and uploads it to PyPI.
