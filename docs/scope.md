# Scope

Scope is the set of publications OAM treats as the world. It decides two things at once:

- **the population** — which works get a bin, a percentile and an MNCS
- **the citation universe** — a citation only counts if the *citing* work is also in scope

Those are deliberately the same set. A citation from a work OAM would not itself score
would inflate the numerator against a denominator built without it.

Everything below follows from one dict, `SCOPE` in [`../oam/config.py`](../oam/config.py).
A work is in scope when every criterion holds.

## The default criteria

Elimination counts are from the 2026-09-23 snapshot, 476,196,327 works. They overlap, so
they do not sum: most rejected works fail several criteria at once. 62,928,576 works
(13.21%) survive all seven.

### `is_core_or_proceedings` — removes 369,158,255 (77.5%)

```sql
COALESCE(primary_location.source.is_core, FALSE)
OR COALESCE(primary_location.raw_type = 'proceedings-article', FALSE)
```

Restricts the corpus to peer-reviewed venues: journals OpenAlex marks as core, plus
conference proceedings. This is by far the most consequential criterion, and it is what
excludes preprints, datasets, book chapters, dissertations and grey literature.

There is no `type_crossref` column in the Parquet snapshot; the Crossref type string
lives in `primary_location.raw_type`, which is where `'proceedings-article'` is read from.

### `has_referenced_works` — removes 351,985,465 (73.9%)

```sql
COALESCE(len(referenced_works), 0) > 0
```

A work with no outgoing references is almost never a research article with usable
metadata, and it cannot contribute to anyone else's citation count. Dropping these is
also what keeps `edges` at 2.2B rows rather than several times that.

### `has_authorship` — removes 299,435,006 (62.9%)

```sql
COALESCE(institutions_distinct_count, 0) > 0
```

At least one authorship must carry an institutional affiliation. `institutions_distinct_count`
equals `COUNT(DISTINCT authorships[].institutions[].id)`, so it doubles as the
fractional-counting denominator — which is why the build never has to read the large
`authorships` column at all.

### `is_english` — removes 149,099,672 (31.3%)

```sql
lower(COALESCE(language, '')) = 'en'
```

English-language works only. This is the criterion most often questioned, and the most
reasonable one to drop if your population is not anglophone. Note that OpenAlex infers
`language`, so it is not perfectly reliable either way.

### `has_subfield` — removes 98,022,660 (20.6%)

```sql
TRY_CAST(str_split(primary_topic.subfield.id, '/')[-1] AS INTEGER) IS NOT NULL
```

Subfield is one of the three bin dimensions, so a work without one cannot be normalised
against a peer group. This criterion must test the **parsed** subfield, not just
`primary_topic.subfield.id IS NOT NULL`: a work whose id failed to parse would otherwise
enter the population carrying a NULL bin dimension.

### `is_not_paratext` — removes 6,680,039 (1.4%)

```sql
NOT COALESCE(is_paratext, FALSE)
```

Editorials, errata, tables of contents and other front matter, as flagged by OpenAlex.

### `is_not_retracted` — removes 136,894 (0.0%)

```sql
NOT COALESCE(is_retracted, FALSE)
```

Retracted works, as flagged by OpenAlex. Almost free in volume terms, and kept for
defensibility rather than effect.

## What is not configurable

The three **bin dimensions** are fixed, because they are the metric definition rather
than a filter: subfield, publication year, and journal-versus-conference. They are derived
for every work whether or not you keep the criterion built on the same field, so removing
`has_subfield` from the scope does not remove `subfield` from the data.

**xpack records** are excluded from the population by a separate switch, not by a
criterion. The 2025-11-12 "walden" release folded them into `works`. On the 2026-09-23
snapshot they are 148,440,389 works, 31.2% of the corpus. `oam.build(include_xpack=True)`
puts them back.

## Changing the scope

1. Edit `SCOPE` in [`../oam/config.py`](../oam/config.py). Each entry is a criterion name,
   the SQL that decides it, and the label the profile funnel prints for the works it
   rejects. Add, remove or rewrite freely.
2. Rebuild: `oam.build(reset=True)` (about 1.5h).

Nothing else needs touching. The build SQL, the `works_status` columns, the profile funnel
and `oam.explain()` are all generated from the dict, and readers take the criteria from
what the store recorded rather than from live config, so a half-finished edit cannot
produce a confusing crash.

Scope is a **build-time** decision, and the rebuild is not optional. `works_scope` and
`edges` are both written already filtered, so a store cannot be re-scoped afterwards:
loosening the scope needs rows that were never stored, and tightening it would need the
citation universe recomputed to match. The build refuses to resume across a changed scope
for exactly this reason, and tells you which criteria moved:

```
✗ Scope changed since the store was built:
    - is_english  removed
  Every table was filtered by the old definition, so topping up would blend the two.
  Rebuild from scratch:  oam.build(reset=True)    (~1.5h)
```

### Writing a criterion

The SQL is evaluated against one row of the OpenAlex works Parquet, so any column in
[`openalex-works-schema.txt`](openalex-works-schema.txt) is available, nested paths
included. Nothing has to declare which columns are read — DuckDB works that out from the
SQL and fetches only those byte ranges.

Two things worth knowing:

- **Keep it narrow.** The build reads about 22% of each source file, and 14.9 of those
  points are the single `authorships` column the resolve warehouse needs — DuckDB
  cannot prune leaves inside a `LIST(STRUCT)`, so one field of it costs all of it.
  `authorships` is therefore already paid for and free to test against, but a criterion
  touching `abstract_inverted_index` (37.8% of the snapshot) would roughly triple the
  build again.
- **Handle NULLs.** Most snapshot columns are nullable and SQL `NULL` is not `FALSE`, so
  wrap tests in `COALESCE` the way the defaults do. A criterion that returns NULL makes
  `in_scope` NULL, and the work silently leaves the population.

After a rebuild, `oam.profile()` shows the new funnel and `oam.check()` confirms the layers
are still self-consistent. Compare the in-scope total against 62,928,576 to see what your
change actually did.
