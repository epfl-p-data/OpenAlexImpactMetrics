# Changelog

## 0.2.0

One citation window, and scores that can be set next to OpenAlex's.

Citations are counted in the publication year and the three following years, which is the window OpenAlex and Scopus use for the field-weighted citation impact. The open window is gone, and so is the `window=` argument. A self-citation, a citing and cited paper that share an OpenAlex author, is removed from that count and from every score. `window_is_complete` says whether the four years have passed; recent years are still scored.

The percentile is the mid-rank, so tied papers share a value and an uncited paper is not given 0. A paper is in the top 10% or the top 1% only when it is at or above the cohort threshold and has at least one citation. The decile, `is_cited`, `norm_vs_median` and `zscore` are gone.

`cited_by_count_in_scope` and `cited_by_count_outside_scope` split OpenAlex's `cited_by_count`. The outside count is clamped at 0. OpenAlex's own `fwci` and percentile are copied onto the score table as `openalex_fwci`, `openalex_citation_normalized_percentile`, `openalex_is_in_top_10_percent` and `openalex_is_in_top_1_percent`.

The installed data is format 3. OAM 0.1.x cannot read it, and OAM 0.2 does not read a 0.1 directory: upgrade, or install OAM 0.1 and pin `record=23493155` for the 2026-10-14 snapshot built with 0.1.

`oam.export` also writes `oam-metrics-<date>.parquet`, the scores in one file sorted by work id.

### Names

| 0.1 | 0.2 |
|---|---|
| `citations`, `citations_3y` | `citations_4y` |
| `ncs`, `ncs_3y` | `ncs_4y` |
| `percentile` | `citation_normalized_percentile_4y` |
| `is_top10`, `is_top10_3y` | `is_in_top_10_percent_4y` |
| `is_top1`, `is_top1_3y` | `is_in_top_1_percent_4y` |
| `n_institutions` | `institutions_distinct_count` |
| `bin_n_works` | `cohort_n_works` |
| `bin_is_reliable` | `cohort_is_reliable` |
| `bin_stats` | `cohort_stats` |
| `P` | `P_full` |
| `P_weighted` | `P_frac`, and only with fractional counting |
| `TCS`, `MCS`, `MNCS`, `PP_top10`, `PP_top1` | the same names with `_full` or `_frac` |
