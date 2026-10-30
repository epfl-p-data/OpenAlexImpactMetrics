# Changelog

## 0.2.0

The citation window and self-citations are choices of the build, and each dataset holds one set of scores.

By default every citation up to the snapshot is counted, self-citations included. `oam.build(citation_window=4, exclude_self_citations=True)` counts the publication year and the three following years and drops a citation whose papers share an OpenAlex author. The choice is recorded in the data: `oam.status()` prints it, and an exported archive carries it in `bundle.json`. A query does not take either argument.

The percentile is the mid-rank, so tied papers share a value and an uncited paper is not given 0. A paper is in the top 10% or the top 1% only when it is at or above the cohort threshold and has at least one citation. The decile, `is_cited`, `norm_vs_median` and `zscore` are gone.

OpenAlex's own `fwci` and percentile are copied onto the score table as `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent` and `openalex_is_in_top_1_percent`.

`oam.resolve(doi=...)` takes one DOI as well as a list, with or without `https://doi.org/` or `https://dx.doi.org/`. Combined with another argument it raises, rather than ignoring that argument.

`oam.download()` returns the installed snapshot date, and `oam.status()` only prints. OAM needs DuckDB 1.4.4 or newer and runs on DuckDB 2.0.

The installed data is format 3. OAM 0.1.x cannot read it, and OAM 0.2 does not read a 0.1 directory: upgrade, or install OAM 0.1 and pin `record=23493155` for the 2026-10-14 snapshot built with 0.1.

### Names

| 0.1 | 0.2 |
|---|---|
| `citations`, `citations_3y` | `citations` |
| `ncs`, `ncs_3y` | `ncs` |
| `percentile` | `citation_normalized_percentile` |
| `is_top10`, `is_top10_3y` | `is_in_top_10_percent` |
| `is_top1`, `is_top1_3y` | `is_in_top_1_percent` |
| `n_institutions` | `institutions_distinct_count` |
| `bin_n_works` | `cohort_n_works` |
| `bin_is_reliable` | `cohort_is_reliable` |
| `bin_stats` | `cohort_stats` |
| `P` | `P_full` |
| `P_weighted` | `P_frac`, and only with fractional counting |
| `TCS`, `MCS`, `MNCS`, `PP_top10`, `PP_top1` | the same names with `_full` or `_frac` |
