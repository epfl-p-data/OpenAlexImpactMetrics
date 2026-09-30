# Scope

OpenAlex contains much more than research articles: preprints, datasets, book chapters, theses, editorials. The **scope** is the set of papers OAM keeps. It decides two things:

- **which papers get a score.** A paper outside the scope is reported, but not scored.
- **which citations count.** A citation counts only when the citing paper is in scope too.

Both use the same set on purpose. Counting citations from papers that are themselves excluded would inflate every score against averages computed without them.

A paper is in scope when it passes all seven rules below. `oam.explain(ids)` tells you, paper by paper, which rules it failed; see [Querying](querying.md#which-papers-are-scored).

The scope is fixed when the data is built. The published download uses the rules below. To change them, you rebuild the data: see [Change the scope](#change-the-scope).

## The seven rules

On the OpenAlex snapshot of 2026-09-23, 62,928,576 of 476,196,327 works pass all seven rules: 13.2%. The counts below are the works each rule removes. Most removed works fail several rules, so the counts do not add up.

| Rule | Keeps | Removes |
|---|---|---|
| `is_core_or_proceedings` | Papers in a core journal or a conference proceedings | 369,158,255 (77.5%) |
| `has_referenced_works` | Papers with a reference list | 351,985,465 (73.9%) |
| `has_authorship` | Papers with at least one institutional affiliation | 299,435,006 (62.9%) |
| `is_english` | Papers in English | 149,099,672 (31.3%) |
| `has_subfield` | Papers with an OpenAlex subfield | 98,022,660 (20.6%) |
| `is_not_paratext` | Papers that are not editorials, errata or tables of contents | 6,680,039 (1.4%) |
| `is_not_retracted` | Papers that are not retracted | 136,894 (0.0%) |

### `is_core_or_proceedings`

```sql
COALESCE(primary_location.source.is_core, FALSE)
OR COALESCE(primary_location.raw_type = 'proceedings-article', FALSE)
```

Keeps peer-reviewed venues: the journals OpenAlex marks as core, plus conference proceedings. This rule removes the most works. It is what excludes preprints, datasets, book chapters, theses and grey literature.

The conference test reads the Crossref type from `primary_location.raw_type`; the snapshot has no `type_crossref` column.

### `has_referenced_works`

```sql
COALESCE(len(referenced_works), 0) > 0
```

A work with no reference list is almost never a research article with usable metadata, and it cannot cite anyone. Removing these also keeps the citation table at 2.2 billion rows instead of several times that.

### `has_authorship`

```sql
COALESCE(institutions_distinct_count, 0) > 0
```

At least one author must have an institutional affiliation. The same number of institutions is what fractional counting divides by.

### `is_english`

```sql
lower(COALESCE(language, '')) = 'en'
```

English-language papers only. This is the rule most worth reconsidering if the papers you study are often not in English. OpenAlex infers the language, so it is not perfectly reliable either way.

### `has_subfield`

```sql
TRY_CAST(str_split(primary_topic.subfield.id, '/')[-1] AS INTEGER) IS NOT NULL
```

The subfield defines the bin a paper is compared with, so a paper without one cannot be scored. The rule tests the parsed number rather than only the presence of an id, so a malformed id cannot enter with an empty bin.

### `is_not_paratext`

```sql
NOT COALESCE(is_paratext, FALSE)
```

Removes editorials, errata, tables of contents and other front matter, as flagged by OpenAlex.

### `is_not_retracted`

```sql
NOT COALESCE(is_retracted, FALSE)
```

Removes retracted papers, as flagged by OpenAlex. It removes very few works; it is there so no score rests on a retracted paper.

## What is not configurable

**The bin.** A paper is always compared by subfield, publication year and venue type (journal or conference). These three are the definition of the scores, not scope rules. Removing `has_subfield` from the scope would not remove the subfield from the comparison.

**xpack records.** Since the 2025-11-12 release, OpenAlex includes "xpack" records: 148,440,389 works on the 2026-09-23 snapshot, 31.2% of the corpus, and almost none of them pass the rules. They are left out of the comparison by a separate switch, not by a rule. `oam.build(include_xpack=True)` keeps them.

## Change the scope

1. Edit `SCOPE` in [`oam/config.py`](../oam/config.py). Each entry is a rule name, the SQL that decides it, and a short label used in reports. Add, remove or rewrite entries.
2. Rebuild the data from scratch with `oam.build(reset=True)`. It takes a few hours; see [Building the data](building.md).

Nothing else needs editing: the build, `oam.explain()` and `oam.profile()` all read the rules from that dictionary.

The rebuild is required. The data stores only the papers and citations the old scope kept, so it cannot be widened or narrowed afterwards. If you edit the scope and call `oam.build()` without `reset=True`, it stops and lists what changed:

```
✗ Scope changed since the store was built:
    - is_english  removed
  Every table was filtered by the old definition, so topping up would blend the two.
  Rebuild from scratch:  oam.build(reset=True)    (re-reads 67 GiB)
```

After the rebuild, `oam.profile()` shows how many papers each rule removes, and `oam.check()` confirms the data is consistent. Compare the in-scope total with 62,928,576 to see what your change did.

### Writing a rule

A rule is SQL evaluated on one row of the OpenAlex works data. Any column listed in [`openalex-works-schema.txt`](openalex-works-schema.txt) can be used, including nested fields.

- **Prefer small columns.** The build downloads only the columns the rules and tables use. It already reads `authorships`, so a rule on authorship data is free. A rule on a large column such as `abstract_inverted_index`, 37.8% of the snapshot, would roughly triple the download.
- **Handle missing values.** Most columns can be empty, and in SQL an empty value is neither true nor false. Wrap tests in `COALESCE(..., FALSE)` as the default rules do. A rule that returns an empty value drops the paper without saying so.
