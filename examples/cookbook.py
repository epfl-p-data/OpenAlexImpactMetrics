"""The three recipes in docs/cookbook.md.

Needs the data, and a network connection for the last recipe. Once is enough:

    import oam
    oam.download()

Then:

    python examples/cookbook.py
"""

import requests

import oam

print("EPFL and ETH Zurich, 2020-2024")
print(f"{'':<12}{'works':>10}{'P_full':>10}{'MNCS full':>12}{'P_frac':>10}{'MNCS frac':>12}")
for name, inst in {"EPFL": "I5124864", "ETH Zurich": "I35440088"}.items():
    papers = oam.resolve(institution=inst, year=(2020, 2024))
    full = oam.aggregate(papers, by=None).iloc[0]
    frac = oam.aggregate(papers, by=None, counting="frac").iloc[0]
    print(f"{name:<12}{len(papers):>10,}{int(full.P_full):>10,}"
          f"{full.MNCS_full:>12.2f}{frac.P_frac:>10,.0f}{frac.MNCS_frac:>12.2f}")

print("\nCo-publications, EPFL and ETH Zurich, 2020-2024")
both = set(oam.resolve(institution="I5124864", year=(2020, 2024))) & set(
    oam.resolve(institution="I35440088", year=(2020, 2024)))
print(f"  {len(both):,} works")
print(oam.aggregate(list(both), by=None).round(2).to_string(index=False))

print("\nEPFL 2022, three scored papers with the highest ncs")
top = (oam.get_metrics(oam.resolve(institution="I5124864", year=2022))
       .query("scored").nlargest(3, "ncs"))
wanted = "|".join(f"W{i}" for i in top.work_id)
titles = {
    int(work["id"].rsplit("W", 1)[-1]): work["title"]
    for work in requests.get(
        "https://api.openalex.org/works",
        params={"filter": f"openalex:{wanted}", "select": "id,title"},
        timeout=60,
    ).json()["results"]
}
for row in top.itertuples():
    print(f"  {row.work_id}  {int(row.citations)} citations  "
          f"ncs {row.ncs:.2f}  fwci {row.openalex_fwci:.2f}  {titles.get(row.work_id)}")
