# Cookbook

A question, and the call that answers it. The numbers are from the OpenAlex snapshot of 14 October 2026. [Querying](querying.md) is the reference for each argument. `examples/cookbook.py` runs the three recipes at the end.

| I want… | Call | Result |
|---|---|---|
| a university over years | `aggregate(resolve(institution="I5124864", year=(2020, 2024)))` | the table in the [README](../README.md#a-university-over-five-years) |
| one number for a whole set | `aggregate(ids, by=None)` | one row |
| a researcher | `resolve(orcid="0000-0001-9161-999X")`, or `author="A5067184382"` | 755 works either way |
| a country | `resolve(country="CH", year=2023)` | 52,502 scored |
| several countries together | `resolve(country=["CH", "AT"], year=2023)` | 77,110 scored. A co-publication counts once, so this is below 52,502 + 27,197 |
| a funder's portfolio | `resolve(funder=4320320924, year=(2020, 2024))` | SNSF: 82,007 works, 52,461 scored, MNCS 1.93 |
| a journal | `resolve(source=137773608, year=2020)` | Nature: 1,371 scored, MNCS 9.72 |
| a topic in a country | `resolve(country="CH", subfield=1702, year=(2020, 2024))` | Swiss AI: 11,132 works |
| conference vs journal | `aggregate(ids, by=["publication_year", "is_proceedings"])` | Swiss AI 2024: journal MNCS 1.63, conference 2.12 |
| a whole field, as a world check | `aggregate(resolve(field=17, year=2020), by=None)` | MNCS 1.00, PP(top 10%) 10.12 |
| only corresponding-author papers | `resolve(institution=..., corresponding=True)` | EPFL 2020–2024: 8,699 scored, MNCS 1.81 |
| a fair comparison of institutions | `aggregate(ids, counting="frac")` | [below](#compare-institutions) |
| per subfield, with names | `aggregate(ids, by="subfield")` merged with `dimension("topics")` | sort by `P_full`; see [Querying](querying.md#indicators-per-group) |
| one paper or a paper list | `get_metrics(["W2103094658"])` | one row per id, including papers that are not scored |
| why a paper is not scored | `explain(ids)`, then `failed_criteria` | [Which papers are scored](querying.md#which-papers-are-scored) |
| open access split | one `resolve(..., oa_status=s)` per status | EPFL 2023: closed MNCS 0.85, green 2.09, diamond 1.02 |
| a DOI list | `resolve(doi=["10.1038/nature12373", "10.1126/science.1244693"])` | `[1981213426, 2159974629]` |
| works newer than the snapshot, or a filter OAM lacks | ids from the OpenAlex API, then `aggregate` | [paginate](querying.md#a-query-oam-cannot-express) |

Three requests need more than one call.

## Compare institutions

A list in `institution=` is OR. To compare two institutions, resolve each one, and show full and fractional counting side by side. The gap between them changes the ranking.

```python
for name, inst in {"EPFL": "I5124864", "ETH Zurich": "I35440088"}.items():
    papers = oam.resolve(institution=inst, year=(2020, 2024))
    print(name, len(papers),
          oam.aggregate(papers, by=None).MNCS_full.iloc[0],
          oam.aggregate(papers, by=None, counting="frac").MNCS_frac.iloc[0])
```

| 2020–2024 | Works | P_full | MNCS full | P_frac | MNCS frac |
|---|---|---|---|---|---|
| EPFL | 36,475 | 22,207 | 1.84 | 9,376 | 1.51 |
| ETH Zurich | 71,019 | 42,049 | 2.07 | 14,384 | 1.80 |

## Co-publications of two institutions

AND is the intersection of the two lists.

```python
both = set(oam.resolve(institution="I5124864", year=(2020, 2024))) & set(
    oam.resolve(institution="I35440088", year=(2020, 2024)))
oam.aggregate(list(both), by=None)   # 2,160 works, 1,298 scored, MNCS 2.55
```

## Top papers with titles

`get_metrics` has the scores. The title is one call to the OpenAlex API. EPFL, 2022, the three scored papers with the highest MNCS:

```python
import requests

top = (oam.get_metrics(oam.resolve(institution="I5124864", year=2022))
       .query("scored").nlargest(3, "ncs"))
wanted = "|".join(f"W{i}" for i in top.work_id)
r = requests.get(
    "https://api.openalex.org/works",
    params={"filter": f"openalex:{wanted}", "select": "id,title"},
)
```

The first of the three is work `4206662285`: 7,959 citations, an ncs of 425.84, an OpenAlex FWCI of 704.66, titled "Hallmarks of Cancer: New Dimensions".
