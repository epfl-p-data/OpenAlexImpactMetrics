# OpenAlexMetrics (OAM) — Project Plan

*Documented 2026-09-10 from the design questionnaire (18 decisions) and recon of the archived ETH-Domain study.*

> ## Reading this today (2026-09-11)
>
> This is the design record: why the scope criteria, bins, counting rules and thresholds are what
> they are. It is kept for the reasoning, not as a guide to the code.
>
> The repository has since been reorganised into the `oam` library, so the paths below are historic.
> `1_data/transformed/1_oam_store/` is now `oam/store.py` writing to `data/store/`, and
> `1_data/transformed/2_oam_metrics/` is now `oam/metrics.py` writing to `data/metrics/`. The
> `0_plan / 1_data / 2_db / 3_analyses / 4_output` skeleton is gone. See
> [../README.md](../README.md) for the current layout, and note that the archived study's own files,
> referenced throughout the notes below, are no longer in the repository.

> ## Phasing note (2026-09-10)
>
> Work started with **data acquisition only**. Everything that benchmarks against the old DWH was
> deferred at the user's request until the validation inputs were supplied. Superseded by the
> 2026-09-11 note below: the metric layers are built and the EPFL benchmark is done. Still outstanding
> from this list are `2_db/`, the `oam` library, and the comparison report.
>
> **Snapshot baseline: free public quarterly release, manifest date 2026-06-26.** The premium
> `s3://openalex-snapshots` bucket was tested with the project API key and returned
> `403 Plan upgrade required` ("Snapshot access requires a Premium, Institutional, or Partner plan"),
> so monthly deltas are not available; the key does work for the regular API and remains useful for
> the ID resolver. Updates therefore follow the quarterly public release.
>
> ### Findings from the 2026 snapshot that change the plan below
>
> 1. **`type_crossref` no longer exists** in OpenAlex (neither snapshot nor API). The 2025 pipeline
>    defined conference papers as `type_crossref == 'proceedings-article'`. The crossref string now
>    lives in **`primary_location.raw_type`**, so that is what OAM reads. The modern `type` column
>    ('conference-paper') is a different population and is stored but does not define scope.
> 2. **`authorships` is never read.** `institutions_distinct_count` was verified equal to
>    `COUNT(DISTINCT authorships[].institutions[].id)` on every record of a test file, so it supplies
>    both `has_authorship` and the fractional-counting denominator.
> 3. **No local mirror is needed.** Struct-field projection is pushed down to HTTP range requests:
>    the OAM read touches ~2% of each part file, so the full scan transfers tens of GB rather than
>    725 GB.
> 4. **`is_xpac` matters for comparability.** The 2025-11-12 "walden" release folded xpack records
>    into `works`. They are ~33% of the 510M corpus and essentially never in scope (0.01% in a 6.1M
>    sample), and they did not exist in the snapshot the 2025 study used. OAM stores the flag so both
>    universes remain computable; it is *not* an eighth scope criterion.
> 5. **The scope logic is an exact match** with the 2025 implementation: a row-by-row comparison
>    against a direct port of `1_build_staging_area.py` found zero discrepancies across all seven
>    criteria, `in_scope`, `n_institutions`, and per-work edge counts.

> ## Progress note (2026-09-11) — metric layers built and validated
>
> The store is complete (2,446/2,446 files, 510,372,821 works, 12 GB) and the derived layers are now
> materialized by `1_data/transformed/2_oam_metrics/run.py`: `citation_counts` (53,405,579 rows),
> `bin_stats` (39,295 bins, 10,922 reliable), `work_metrics` (62,429,590 works). 785 MB, 81 s to
> rebuild, stage-level resumable. Metrics for an arbitrary 65,774-ID set now return in ~0.2 s.
>
> **Decisions taken while building:**
> - **xpack works are excluded from the metric population** (`--include-xpack` reverses it), so bin
>   denominators stay comparable with the pre-walden snapshot the 2025 study used.
> - **A publication year is required**, since it is a bin dimension. Exactly one in-scope work
>   (W2269335524) is dropped, leaving a population of 62,429,590.
> - Metrics are **always computed** and `bin_is_reliable` is exposed, rather than NULLed out below the
>   500-work threshold as the 2025 pipeline did. Filter on the flag to reproduce the study.
> - A **3-year fixed window** (`citations_3y`, `ncs_3y`, `is_top10_3y`) is computed in the same edge
>   pass, so windowed variants never require rescanning the 2.16B-edge list.
>
> **Validation against the 2025 report — reproduced.** The curated publication list turned out to be
> archived at `2_matches/epfl/input/qpub.parquet`, and its matching algorithm is not needed: 55,915 of
> its 59,617 rows (93.8%) already carry an `openalex_id`. Of those, 54,048 still resolve in the
> 2026-06-26 snapshot and 47,254 are in scope.
>
> EPFL 2014–2023, fractional counting, reliable bins:
>
> | Grouping | MNCS mean Δ | ppTop10 mean Δ | r (MNCS / ppTop10) |
> |---|---|---|---|
> | Per year | −0.9% | −1.2% | 0.938 / 0.878 |
> | 4-year rolling windows (as the report headlines) | **+0.07%** | **−0.49%** | 0.980 / 0.980 |
>
> The earlier −5.1% / −6.1% gap came entirely from the publication set: a raw OpenAlex affiliation
> query (ROR 02s376052) instead of the curated list. The engine was never the difference. Fractional
> publication counts also track (frac/full ratio 0.493→0.426 vs the report's 0.504→0.439), which
> validates the `1/n_institutions` denominator.
>
> Residuals worth noting: publication counts run ~9.1% below the report (the 6.2% of rows whose ID the
> study's matcher supplied, plus the 3.3% of IDs that no longer resolve — the merged/deleted-works
> question); and two single-year outliers, 2022 MNCS (−9.5%) and 2023 ppTop10 (−8.2%), in the years a
> nine-months-newer snapshot reshapes the reference population most. Both vanish under 4-year smoothing.
>
> Self-checks on the world population pass: MNCS = 1.0000 exactly, ppTop10 = 10.27%, ppTop1 = 1.02%
> (the excess over 10%/1% is the inherited `>= p90` ties rule).
>
> Still open: the `oam` library, `2_db/`, the comparison report, and the other five institutions
> (their `qpub.parquet` files are archived alongside EPFL's).

## Objective

Build **OAM**: a lightweight engine that, for any set of OpenAlex work IDs, returns citation-impact
metrics computed against a custom **in-scope** publication universe — without rebuilding a heavy data
warehouse. Metrics: **NCS/MNCS**, **within-bin percentile**, **ppTop1 / ppTop10**, each in **full and
fractional counting**. A work's bin = publication year × subfield × publication type (journal vs
conference). Out-of-scope or unknown IDs are reported as such, with the failed criteria.

Validation goal: reproduce the EPFL charts of the 2025 ETH-Domain bibliometric study with the new
engine and compare. Exact equality is impossible (the data moved since the 2025 snapshot); we expect
the same order of magnitude and similar trends.

## Audience

- Primary: Gaël / EPFL bibliometrics team — internal tool for future studies.
- Secondary: eventual **open-source publication** (tool + validation report), so code and methodology
  must be clean and documented.

## Input Data

1. **OpenAlex works — Parquet snapshot.** Verified live on the free public bucket
   `s3://openalex/data/parquet/works/`: **725 GB, 510,372,821 works**, 2,446 part files across 482
   `updated_date` partitions (2016-06-24 → 2026-06-26; June 2026 quarterly release). Records match the
   API work object (nested columns). The premium daily bucket is **not accessible** with the project
   key (see phasing note), so updates follow the quarterly release.
   Eleven leaf columns are read: `id`, `publication_year`, `language`, `type`, `is_retracted`,
   `is_paratext`, `is_xpac`, `primary_location.source.is_core`, `primary_location.raw_type`,
   `primary_topic.subfield.id`, `institutions_distinct_count`, `referenced_works`.
2. **Validation references** (in `0_plan/`):
   - `ARCHIVE-2025-07-10-ethBiblio-main/` — the old pipeline code whose scope and metric definitions
     OAM replicates (`1_build_staging_area.py`, marts scripts).
   - `ARCHIVE-assets/` — archived indicator values. Key targets:
     `1_2_mncs/epfl/data.csv` (MNCS_frac 1.53–1.91) and `1_2_ppTop10/epfl/data.csv`
     (ppTop10_frac 17.3%–21.1%), per year 2014–2023.
   - `3_Bibliometric-Study-EPFL.pdf` — the published report; its methodology page confirms the
     conventions below (fractional counting at institution level, self-citations included, subfield
     normalisation, citations up to snapshot date).
3. **EPFL test ID sets** (both, per questionnaire):
   - (a) the old study's matched EPFL work-ID list, if recoverable (cleanest engine-to-engine test) —
     to be located / provided;
   - (b) a fresh OpenAlex API affiliation query (EPFL, 2014–2023) via the ID-resolver helper.

## Scientific Framework

Conventions replicated exactly from the old study:

- **Scope — all 7 criteria required** (each also stored as a flag for out-of-scope reporting):
  1. language == English
  2. ≥ 1 author–institution pair
  3. has subfield (`primary_topic.subfield`)
  4. ≥ 1 outgoing reference
  5. core journal (`primary_location.source.is_core`) **or** conference paper
     (`type_crossref == 'proceedings-article'`)
  6. not retracted
  7. not paratext
  Expected: ~25% of all works in scope (~128M).
- **Bin**: `publication_year × subfield × type_is_proceedings`. Any document type in a core journal
  qualifies for the journal branch (identical to old code).
- **Citation universe**: citations **from in-scope citing works only**; **self-citations included**;
  default **open window** (all citations to snapshot date). **Fixed windows** (publication year + k)
  are additionally supported — the citing year is stored with each citation edge; bin statistics are
  recomputed for the chosen window.
- **Per-work metrics (core set only)**:
  - `ncs` = citations / bin mean citations (aggregates to MNCS)
  - `percentile` = CUME_DIST (ascending) within bin
  - `is_top10` = citations ≥ bin p90 (QUANTILE_CONT), `is_top1` = citations ≥ bin p99
    — the old **threshold rule**, kept for comparability (ties can push the world share slightly
    above 10%; documented behavior).
- **Reliability**: always compute; expose bin `n_works` and a `reliable = n_works ≥ 500` flag;
  filtering happens at query time (old pipeline NULLed metrics instead).
- **Counting**: full → weight 1 per work; fractional → weight **1 / n_distinct_institutions_on_work**
  (independent of the institution being evaluated); works with 0 institutions are excluded from
  fractional aggregates. MNCS_frac = Σ(ncs·w)/Σ(w); ppTopX_frac = Σ(topX·w)/Σ(w).
- **IDs**: stored numerically (drop the `W` prefix → int64).

## Transformations

The heavy build is the Transform stage: **stream from S3 with column projection**, partition by
partition, resumable — no full raw mirror on local disk (Mac: 32 GB RAM, ~876 GB free).

- `1_data/original/` — small files only: snapshot manifests, EPFL ID lists, copies of reference
  `data.csv` files.
- `1_data/transformed/1_oam_store/` — partitioned Parquet (zstd), the OAM source of truth:
  1. `works_status` — **all** works: work_id, publication_year, 7 criterion flags, in_scope
     (~5–10 GB; powers "why is this ID out of scope").
  2. `works_scope` — in-scope works: work_id, year, subfield, is_proceedings, n_institutions
     (~1–2 GB).
  3. `edges` — in-scope citing → cited: cited_id, citing_id, citing_year (denormalized)
     (~3–4B rows, ~30–60 GB; enables exact incremental updates, fixed windows, future
     self-citation variants).
- `1_data/transformed/2_oam_metrics/` — derived layers built from the store: `citation_counts`,
  `bin_stats` (n_works, mean, p90, p99 per bin), `work_metrics` (ncs, percentile, top flags).
- `2_db/project.duckdb` — thin: views over the Parquet store plus small materialized aggregates for
  the analyses.
- **Incremental update (monthly, premium bucket)**: download only `updated_date > last_sync`
  partitions → upsert `works_status` / `works_scope`; delete + reinsert `edges` of updated citing
  works; recompute derived layers (cheap, minutes in DuckDB). Open question to investigate: how
  merged/deleted works surface in the new snapshot layout (legacy `merged_ids` moved to
  `legacy-data/`); fallback = quarterly full rebuild.
- **Python library `oam`** (in-repo package; factored out for publication once stable):
  - `oam.update()` — initial build / incremental sync
  - `oam.resolve(...)` — OpenAlex API query → work IDs (API key, cursor paging)
  - `oam.get_metrics(ids, window=None)` — per-work DataFrame: in_scope (+ failed criteria) /
    not_found, citations, ncs, percentile, is_top1, is_top10, n_institutions, bin info
  - `oam.aggregate(ids, by="year", counting="full"|"frac", window=None)` — MNCS, ppTop1, ppTop10
    (MCS/TCS come free from stored counts)

## Analyses

1. `scope_share` — sanity check: share of in-scope works (~25% expected), by year.
2. `validation_epfl` — MNCS_frac and ppTop10_frac per year for both EPFL ID sets, overlaid on the
   archived study values; full vs fractional comparison.
3. `bin_diagnostics` — bin sizes, share of works in unreliable bins, effect of the ties rule on the
   world ppTop10 baseline.

## Output Format

HTML comparison report (Quarto) in `4_output/`: methodology summary, old-vs-new EPFL charts,
divergence discussion, and an OAM usage guide — written with open-source publication in mind.

## Known Risks & Limitations

- **No exact reproduction**: citations grew and OpenAlex reassigned subfields / core flags /
  affiliations since the 2025 snapshot → compare magnitudes and trends only.
- **Initial build cost**: even column-pruned, the S3 scan transfers a large share of 725 GB
  (`authorships`, `referenced_works` are the fat columns) — hours; the build must be
  partition-resumable. 32 GB RAM → DuckDB spill-to-disk configured.
- **Merged/deleted works** in the new snapshot format need investigation; stale-row risk in
  incremental mode.
- **Premium bucket authentication** (`credential_process` flow) to set up and test.
- **Inherited methodological caveats** (by design, for comparability): ≥p90 ties rule, English-only,
  has-references requirement, primary topic only, institution-count fractionalisation (not
  author-share), zero-institution works excluded from fractional indicators.
- **Schema assumption**: works Parquet assumed to match the API object; verify nested paths against a
  real part file as the first implementation step.
