# Town Hall Telescope

An interactive map of what local governments are discussing, by zip code. There are two regions: the Kansas side of the Kansas City area (`docs/`) and the Boulder / Denver area (`docs/co/`). Click a zip code to see the specific issues most discussed in its city council, county commission and planning meetings over the last three months.

Kansas scope: 96 zip codes in Johnson and Wyandotte counties plus the state's other largest cities (Wichita, Topeka, Lawrence, Manhattan, Salina). Wichita blocks automated requests, so its zips show no records yet, and the county governments outside Johnson and Wyandotte are not collected.

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
