# Town Hall Telescope

An interactive map of what local governments are discussing. It covers Colorado. The map shows zip codes by default, and a Zip codes / Counties switch shows whole counties instead. Click an area, or search by zip code or county name, to see the specific issues most discussed by its city councils and county commission over the last three months.

The map covers every zip code and county in Colorado; areas without collected records stay grey. `scripts/geography.py` (the geography workflow) builds the statewide geography from 2020 Census files and writes `data/zip_areas.csv` (every zip split by county and town, with residents and land area, from census blocks), `data/places.csv` (all 272 incorporated towns and cities), `data/zips.geojson` and `data/counties.geojson`.

A zip lists every government serving its residents: each town at the share of the zip's residents living in it, and each county government at its unincorporated share (zips nobody lives in use land area instead). In the county view, each county shows its county government plus every town with at least 10% of its residents in the county. A government's source id is its name as a slug (`commerce-city`, `boulder-county`); Denver and Broomfield are both city and county. `build_map.py` writes `zips.json` (top 8 issues) and `counties.json` (top 12).

Records so far cover 18 governments in the Denver and Boulder area. `data/sources.csv` lists where each posts agendas and minutes; `collector/sources.yaml` configures the collector.

Scripts take `--region co` (the default and only region); paths are in `collector/regions.py`. The project covered parts of Kansas until October 2026; that work is in git history. The old `/co/` address redirects to the main page.

## How the map is built

1. `python -m collector.collect` (run weekly by GitHub Actions) downloads agendas and minutes into `data/text/`.
2. `python scripts/condense.py` boils each document down to its business lines in `data/condensed/`.
3. Issues are written to `data/issues/<government>.yaml`, each pointing at the meetings it came up in.
4. `python scripts/build_map.py` ranks issues per zip and county by how many distinct meetings discussed them (scaled by the share of residents the government serves, with a boost when residents spoke) and writes `docs/data/`.
5. `docs/index.html` is the map, served by GitHub Pages from the `docs/` folder.
