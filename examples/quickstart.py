"""End-to-end example: resolve an institution's works, then score them.

Needs the data. Once is enough:

    import oam
    oam.download()                # or oam.build(), for a scope of your own

Then:

    python examples/quickstart.py
"""

import oam

INSTITUTION = "I5124864"  # EPFL
YEARS = (2014, 2023)

print(f"Resolving works for {INSTITUTION}, {YEARS[0]}-{YEARS[1]} ...")
ids = oam.resolve(institution=INSTITUTION, year=YEARS)
name = oam.dimension("institutions", ids=INSTITUTION).display_name.iloc[0]
print(f"  {len(ids):,} work ids for {name}")

# Fractional counting: a paper shared with nine other institutions counts as a tenth.
by_year = oam.aggregate(ids, by="publication_year", counting="frac")
print("\nFractional indicators by year")
print(by_year.round(3).to_string(index=False))

overall = oam.aggregate(ids, by=None, counting="frac")
print("\nWhole period")
print(overall.round(3).to_string(index=False))
print(f"\n  {int(overall.P.iloc[0]):,} of {len(ids):,} ids scored — the rest are out of "
      f"scope, or sit in a bin too small to be reliable.")

# Why did the rest not make it? Each scope criterion, per work.
missing = oam.get_metrics(ids)
missing = missing.loc[~missing.in_scope, "work_id"]
if len(missing):
    reasons = oam.explain(missing.head(2000))
    print("\nMost common reasons for falling out of scope (sample of 2,000)")
    print(reasons.failed_criteria.explode().value_counts().head().to_string())
