"""The three examples from the README.

Needs the data. Once is enough:

    import oam
    oam.download()

Then:

    python examples/quickstart.py
"""

import oam

print("EPFL, 2020-2024")
epfl = oam.resolve(institution="I5124864", year=(2020, 2024))
print(f"  {len(epfl):,} works")
print(oam.aggregate(epfl).round(2).to_string(index=False))

print("\nJennifer Doudna")
doudna = oam.resolve(orcid="0000-0001-9161-999X")
print(f"  {len(doudna):,} works")
print(oam.aggregate(doudna, by=None).round(2).to_string(index=False))

print("\nSwitzerland, 2020-2024")
swiss = oam.resolve(country="CH", year=(2020, 2024))
print(f"  {len(swiss):,} works")
print(oam.aggregate(swiss).round(2).to_string(index=False))
