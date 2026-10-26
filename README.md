# Town Hall Telescope

An interactive map of what local governments are discussing. It covers Colorado. The map shows zip codes by default, and a Zip codes / Counties switch shows whole counties instead. Click an area, or search by zip code or county name, to see the specific issues most discussed by its city councils and county commission over the last three months.

The map covers every zip code and county in Colorado; areas without collected records stay grey. `scripts/geography.py` (the geography workflow) builds the statewide geography from 2020 Census files and writes `data/zip_areas.csv` (every zip split by county and town, with residents and land area, from census blocks), `data/places.csv` (all 272 incorporated towns and cities), `data/zips.geojson` and `data/counties.geojson`.

A zip lists every government serving its residents: each town at the share of the zip's residents living in it, and each county government at its unincorporated share (zips nobody lives in use land area instead). In the county view, each county shows its county government plus every town with at least 10% of its residents in the county. A government's source id is its name as a slug (`commerce-city`, `boulder-county`); Denver and Broomfield are both city and county. `build_map.py` writes `zips.json` (top 8 issues) and `counties.json` (top 12).

Records so far cover 18 governments in the Denver and Boulder area. `collector/sources.yaml` configures the collector. `data/governments.csv` (from `scripts/master_list.py`, the master-list workflow) lists all 334 Colorado counties, cities and towns with each one's website, where it posts meeting records, the platform, and whether the collector already reads that platform; `data/governments.md` sums it up.

Scripts take `--region co` (the default and only region); paths are in `collector/regions.py`. The project covered parts of Kansas until October 2026; that work is in git history. The old `/co/` address redirects to the main page.

## How the map is built

1. `python -m collector.collect` (run weekly by GitHub Actions) downloads agendas and minutes into `data/text/`.
2. `python scripts/condense.py` boils each document down to its business lines in `data/condensed/`.
3. `python scripts/extract_issues.py` sends each government whose condensed meetings changed to Claude (Batch API) and writes `data/issues/<government>.yaml`, each issue pointing at the meetings it came up in. Each issue is filed under one to three topics from a fixed list (they feed the map's topic clouds) and flagged when it is about data centers or guns (the map's highlight toggles). `python scripts/tag_issues.py` fills in topics and the guns flag for issues extracted before those existed. `python scripts/gun_scan.py` then reads the meetings of governments that mention firearms and adds any gun item the extraction left out. It needs an Anthropic API key in the `TOWNHALL_SECRET` repository secret; the model defaults to `claude-sonnet-5-5` and can be changed with the `EXTRACT_MODEL` repository variable. The weekly collect run does steps 2 to 4 when the secret is set, and the extract workflow runs them on demand.
4. `python scripts/build_map.py` ranks issues per zip and county by how many distinct meetings discussed them (scaled by the share of residents the government serves, with a boost when residents spoke) and writes `docs/data/`.
5. `docs/index.html` is the map, served by GitHub Pages from the `docs/` folder.
