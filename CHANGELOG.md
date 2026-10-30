# Changelog

## 0.4.0

`oam.resolve_dois(dois)` returns a DataFrame of `doi` and `work_id`, one row per DOI in the order given and spelled as given, so a publication list joins back onto its scores. A DOI the snapshot does not have gets an empty `work_id`. A DOI OpenAlex gives to several works gets one row per work. It used to return the sorted ids, as `resolve(doi=...)` still does.

`aggregate(by="subfield")` adds `subfield_name` and puts the largest subfield first, within the other groups when there are any.

The metrics guide explains full, fractional and corresponding-author counting on a worked example, and shows how to check whether a few widely shared papers carry a set.

The data format stays 3.

## 0.3.1

`resolve(doi=...)` no longer leaves the query connection unusable on DuckDB 2.0. The next `aggregate`, `get_metrics` or `resolve` in the same session works.

`year=[2019, 2021]` is those two years. A tuple and a `"2014-2023"` string stay ranges.

The PyPI publish workflow runs the tests first and publishes only when they pass.

`dir(oam)` lists the public names. `oam.status()` on a download reports the data format and that this OAM can read it. Its section titles no longer say "build step".

The data format stays 3.

## 0.3.0

`get_metrics` calls the score-table flag `scored`. `in_scope` stays on `explain`, where it means the work passed the scope rules. `explain` and `get_metrics(explain=True)` return `is_xpack` and `failed_criteria`. A paper in scope is not scored when it is an xpack record, or when it has no publication year.

`resolve` rejects a country name, an unknown open-access status, an unknown work type, a year it cannot parse, and a range whose start is after its end. A DOI list prints how many DOIs the snapshot does not contain. `dimension` search ignores accents and returns an exact name before a prefix and a prefix before another substring.

`oam.download()` reads `bundle.json` from the end of the archive before transferring it. A format this version cannot read is refused first. Two archives with the same snapshot date and different checksums are not the same install. A missing Zenodo record is an error. The download line names the record, the snapshot and the size.

`oam.build()` on a downloaded archive stops and leaves the files in place. `oam.profile()` reads the citation count from the archive. `oam.status()` labels a download as a downloaded archive, and `oam.check()` names the checks a download skips.

The data format stays 3. This version reads the archive published with 0.2.

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
