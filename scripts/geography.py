"""Build the statewide geography the map is drawn from: every zip, county and town in the state.

Downloads 2020 Census files: zip (ZCTA) and county outlines, incorporated place outlines, and
every census block with its population and land area. Each block is placed in a zip and a town
by its interior point (its county is part of its id), so the pieces below add up exactly.
Writes, under the region's data folder:
  zip_areas.csv     zip, county, place ("" = unincorporated), population, land_m2: one row per piece
  places.csv        place, geoid, kind (city, town, ...), population, county holding most residents
  zips.geojson      simplified outlines of every zip mostly in the state (property "zip")
  counties.geojson  simplified outlines of every county (property "county")

Usage: python scripts/geography.py [--region co]
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import requests
import shapefile  # pyshp
import shapely
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402

GENZ = "https://www2.census.gov/geo/tiger/GENZ2020/shp"
TIGER = "https://www2.census.gov/geo/tiger/TIGER2020"
UA = {"User-Agent": "townhalltelescope (https://github.com/Z3rgrush/townhalltelescope)"}

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="co", choices=REGIONS)
region = REGIONS[ap.parse_args().region]
DATA, fips = ROOT / region["data"], region["state_fips"]


def reader(url, stem):
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, headers=UA, timeout=900).content))
    parts = {ext: io.BytesIO(z.read(f"{stem}.{ext}")) for ext in ("shp", "shx", "dbf")}
    return shapefile.Reader(**parts)


def shapes(url, stem, keep):
    r = reader(url, stem)
    out = []
    for i, rec in enumerate(r.iterRecords()):
        rec, s = rec.as_dict(), r.shape(i)
        if keep(rec, s.bbox):
            out.append((rec, shape(s.__geo_interface__).buffer(0)))
    return out


counties = shapes(f"{GENZ}/cb_2020_us_county_500k.zip", "cb_2020_us_county_500k", lambda r, _: r["STATEFP"] == fips)
county_name = {r["COUNTYFP"]: r["NAME"] for r, _ in counties}
minx, miny, maxx, maxy = shapely.union_all([g for _, g in counties]).bounds
zctas = shapes(f"{GENZ}/cb_2020_us_zcta520_500k.zip", "cb_2020_us_zcta520_500k",
               lambda _, b: b[0] <= maxx and b[2] >= minx and b[1] <= maxy and b[3] >= miny)
places = shapes(f"{TIGER}/PLACE/tl_2020_{fips}_place.zip", f"tl_2020_{fips}_place",
                lambda r, _: r["FUNCSTAT"] == "A")  # incorporated places, not census-designated ones
print(f"{len(counties)} counties, {len(zctas)} nearby zips, {len(places)} places")

blocks = reader(f"{TIGER}/TABBLOCK20/tl_2020_{fips}_tabblock20.zip", f"tl_2020_{fips}_tabblock20")
recs = [r.as_dict() for r in blocks.iterRecords(fields=["GEOID20", "POP20", "ALAND20", "INTPTLAT20", "INTPTLON20"])]
points = shapely.points([float(r["INTPTLON20"]) for r in recs], [float(r["INTPTLAT20"]) for r in recs])
print(f"{len(recs)} blocks")


def locate(features):
    """Index of the feature holding each block's interior point, or -1."""
    tree = shapely.STRtree([g for _, g in features])
    hit = tree.query(points, predicate="within")
    out = [-1] * len(recs)
    for p, f in zip(*hit):
        out[p] = f
    return out


in_zip, in_place = locate(zctas), locate(places)
pieces = defaultdict(lambda: [0, 0])
for r, zi, pi in zip(recs, in_zip, in_place):
    if zi < 0:
        continue  # a few blocks on the state line sit just outside every zip outline
    key = (zctas[zi][0]["ZCTA5CE20"], county_name[r["GEOID20"][2:5]], places[pi][0]["NAME"] if pi >= 0 else "")
    pieces[key][0] += r["POP20"]
    pieces[key][1] += r["ALAND20"]

# A zip is the state's when most of its land is, so border zips go to the side they mostly serve.
land = defaultdict(int)
for (z, _, _), (_, a) in pieces.items():
    land[z] += a
zips = {r["ZCTA5CE20"]: g for r, g in zctas if r["ALAND20"] and land[r["ZCTA5CE20"]] >= 0.5 * r["ALAND20"]}
rows = sorted((k, v) for k, v in pieces.items() if k[0] in zips)
with open(DATA / "zip_areas.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["zip", "county", "place", "population", "land_m2"])
    w.writerows([*k, *v] for k, v in rows)

per_place = defaultdict(lambda: defaultdict(int))
for (_, c, p), (pop, _) in pieces.items():
    if p:
        per_place[p][c] += pop
kind = {r["NAME"]: r["NAMELSAD"][len(r["NAME"]):].strip() for r, _ in places}
with open(DATA / "places.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["place", "geoid", "kind", "population", "county"])
    for r, _ in sorted(places, key=lambda t: t[0]["NAME"]):
        by_county = per_place.get(r["NAME"]) or {}
        main = max(by_county, key=by_county.get) if by_county else ""
        w.writerow([r["NAME"], r["GEOID"], kind[r["NAME"]], sum(by_county.values()), main])


def write_geo(path, features, prop, tolerance):
    out = [{"type": "Feature", "properties": {prop: k},
            "geometry": mapping(shapely.set_precision(g.simplify(tolerance, preserve_topology=True), 1e-5))} for k, g in features]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": out}, separators=(",", ":")))


write_geo(DATA / "zips.geojson", sorted(zips.items()), "zip", 0.0008)
write_geo(DATA / "counties.geojson", sorted((r["NAME"], g) for r, g in counties), "county", 0.0015)
print(f"{len(zips)} zips, {len(rows)} zip pieces, {sum(p for (_, _, _), (p, _) in rows)} residents")
