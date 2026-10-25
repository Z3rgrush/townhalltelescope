# Town Hall Telescope

An interactive map of what local governments are discussing. It covers Colorado, starting with the Denver and Boulder area and growing toward the whole state. The map shows zip codes by default, and a Zip codes / Counties switch shows whole counties instead. Click an area, or search by zip code or county name, to see the specific issues most discussed by its city councils and county commission over the last three months.

In the county view, each county shows its county government plus every tracked city with at least 10% of its land in the county. `scripts/county_places.py` (the county-places workflow) picks the mapped counties and writes `county_places.csv`, `zip_counties.csv` and `counties.geojson`. `build_map.py` writes both the per-zip files (`zips.json`, top 8 issues) and the county files (`counties.json`, top 12).

Current scope: 52 zip codes in Denver, Boulder and Broomfield counties and their neighbors.

- `data/sources.csv` lists where each government posts agendas and minutes; `collector/sources.yaml` configures the collector.
- `data/zips.csv` maps each zip to the governments that cover it.
- `data/pilot_zips.geojson` holds the zip boundaries (2010 Census ZCTAs).

Scripts take `--region co` (the default and only region); paths are in `collector/regions.py`. The project covered parts of Kansas until October 2026; that work is in git history. The old `/co/` address redirects to the main page.

## How the map is built

1. `python -m collector.collect` (run weekly by GitHub Actions) downloads agendas and minutes into `data/text/`.
2. `python scripts/condense.py` boils each document down to its business lines in `data/condensed/`.
3. Issues are written to `data/issues/<government>.yaml`, each pointing at the meetings it came up in.
4. `python scripts/build_map.py` ranks issues per zip by how many distinct meetings discussed them (scaled by how much of the zip the government covers, with a boost when residents spoke) and writes `docs/data/`.
5. `docs/index.html` is the map, served by GitHub Pages from the `docs/` folder.
