# Town Hall Telescope

An interactive map of what local governments are discussing. There are two pages: Kansas (`docs/`) and Colorado (`docs/co/`). The map shows zip codes by default, and a Zip codes / Counties switch shows whole counties instead. Click an area, or search by zip code or county name, to see the specific issues most discussed by its city councils and county commission over the last three months.

In the county view, each county shows its county government plus every tracked city with at least 10% of its land in the county. `scripts/county_places.py` (the county-places workflow) picks the mapped counties and writes `county_places.csv`, `zip_counties.csv` and `counties.geojson`. `build_map.py` writes both the per-zip files (`zips.json`, top 8 issues) and the county files (`counties.json`, top 12).

Kansas scope: 96 zip codes in Johnson and Wyandotte counties plus the state's other largest cities (Wichita, Topeka, Lawrence, Manhattan, Salina). Each city's county commission is collected too (Sedgwick, Shawnee, Douglas, Riley, Saline). Wichita's own site blocks automated requests, so its records come from its public Granicus archive instead; `scripts/county_shares.py` (the county-shares workflow) fills in how much of each zip lies in each county.

- `data/sources.csv` lists where each government posts agendas and minutes.
- `data/zips.csv` maps each zip to the governments that cover it.
- `data/pilot_zips.geojson` holds the zip boundaries (2010 Census ZCTAs).

Boulder / Denver scope: 52 zip codes in Denver, Boulder and Broomfield counties. Its files live under `data/co/`, its sources in `collector/sources_co.yaml`. Every script takes `--region ks` (default) or `--region co`; paths are in `collector/regions.py`. `docs/co/index.html` is a copy of `docs/index.html` that differs only in its text and the `PAGE` settings, so change both together.

## How the map is built

1. `python -m collector.collect` (run weekly by GitHub Actions) downloads agendas and minutes into `data/text/`.
2. `python scripts/condense.py` boils each document down to its business lines in `data/condensed/`.
3. Issues are written to `data/issues/<government>.yaml`, each pointing at the meetings it came up in.
4. `python scripts/build_map.py` ranks issues per zip by how many distinct meetings discussed them (scaled by how much of the zip the government covers, with a boost when residents spoke) and writes `docs/data/` (or `docs/co/data/` with `--region co`).
5. `docs/index.html` is the map, served by GitHub Pages from the `docs/` folder.
