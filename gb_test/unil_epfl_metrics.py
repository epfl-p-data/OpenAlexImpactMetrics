"""Extract citation metrics for UNIL and EPFL.

Requires the full data: oam.download("full") or oam.build(). Run with:

    python gb_test/unil_epfl_metrics.py

Writes one CSV per institution (per-year metrics, fractional counting)
next to this script.
"""

from pathlib import Path

import oam

INSTITUTIONS = {
    "EPFL": "I5124864",
    "UNIL": "I97565354",
}
YEARS = (2014, 2023)
OUT_DIR = Path(__file__).parent

for name, inst_id in INSTITUTIONS.items():
    print(f"\n=== {name} ({inst_id}), {YEARS[0]}-{YEARS[1]} ===")
    ids = oam.resolve(institution=inst_id, year=YEARS)
    print(f"{len(ids):,} works resolved from the warehouse")

    by_year = oam.aggregate(ids, by="publication_year", counting="frac")
    print(by_year.round(3).to_string(index=False))

    overall = oam.aggregate(ids, by=None, counting="frac")
    print("\nWhole period")
    print(overall.round(3).to_string(index=False))

    out = OUT_DIR / f"{name.lower()}_metrics.csv"
    by_year.to_csv(out, index=False)
    print(f"Saved {out}")
