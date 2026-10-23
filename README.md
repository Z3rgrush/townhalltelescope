# Town Hall Telescope

An interactive map of the Kansas side of the Kansas City area. Click a zip code to see the specific issues most discussed in its city council, county commission and planning meetings over the last three months.

Pilot scope: 47 zip codes and 23 local governments in Johnson and Wyandotte counties.

- `data/sources.csv` lists where each government posts agendas and minutes.
- `data/zips.csv` maps each zip to the governments that cover it.
- `data/pilot_zips.geojson` holds the zip boundaries (2010 Census ZCTAs).

## How the map is built

1. `python -m collector.collect` (run weekly by GitHub Actions) downloads agendas and minutes into `data/text/`.
2. `python scripts/condense.py` boils each document down to its business lines in `data/condensed/`.
3. Issues are written to `data/issues/<government>.yaml`, each pointing at the meetings it came up in.
4. `python scripts/build_map.py` ranks issues per zip by how many distinct meetings discussed them (scaled by how much of the zip the government covers, with a boost when residents spoke) and writes `docs/data/`.
5. `docs/index.html` is the map, served by GitHub Pages from the `docs/` folder.
