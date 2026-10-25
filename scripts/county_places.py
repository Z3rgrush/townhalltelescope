"""Work out which counties the map shows and which tracked cities fall in each.

Downloads the 2020 TIGER/Line county and place files. A county is on the map when its county
government is a source, or when it holds at least MIN_CITY of a tracked city's land. Writes:
  county_places.csv  county, city, share of the city's land in that county
  zip_counties.csv   zip, the county holding most of the zip's land
  counties.geojson   simplified outlines of the mapped counties

Usage: python scripts/county_places.py --region co
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import requests
import shapefile  # pyshp
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from collector.regions import REGIONS  # noqa: E402
from build_map import SETUP  # noqa: E402

MIN_CITY = 0.10

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="co", choices=REGIONS)
key = ap.parse_args().region
region, setup = REGIONS[key], SETUP[key]
DATA, fips = ROOT / region["data"], region["state_fips"]


def shapes(url, stem, keep):
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=600).content))
    r = shapefile.Reader(shp=io.BytesIO(z.read(f"{stem}.shp")), shx=io.BytesIO(z.read(f"{stem}.shx")),
                         dbf=io.BytesIO(z.read(f"{stem}.dbf")))
    out = []
    for i, rec in enumerate(r.iterRecords()):
        rec = rec.as_dict()
        if keep(rec):
            out.append((rec, shape(r.shape(i).__geo_interface__).buffer(0)))
    return out


counties = shapes("https://www2.census.gov/geo/tiger/TIGER2020/COUNTY/tl_2020_us_county.zip", "tl_2020_us_county",
                  lambda rec: rec["STATEFP"] == fips)
places = shapes(f"https://www2.census.gov/geo/tiger/TIGER2020/PLACE/tl_2020_{fips}_place.zip", f"tl_2020_{fips}_place",
                lambda rec: rec["FUNCSTAT"] == "A" and rec["NAME"] in setup["places"])

rows, mapped = [], set(setup["county_govs"])
for prec, pg in places:
    for crec, cg in counties:
        if pg.intersects(cg):
            share = pg.intersection(cg).area / pg.area
            if share >= 0.005:
                rows.append([crec["NAME"], prec["NAME"], round(share, 3)])
                if share >= MIN_CITY:
                    mapped.add(crec["NAME"])
rows = sorted(r for r in rows if r[0] in mapped)
with open(DATA / "county_places.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["county", "city", "share_of_city_area"])
    w.writerows(rows)

zips = json.loads((DATA / "pilot_zips.geojson").read_text())["features"]
with open(DATA / "zip_counties.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["zip", "county"])
    for z in zips:
        zg = shape(z["geometry"]).buffer(0)
        best = max(counties, key=lambda c: zg.intersection(c[1]).area if zg.intersects(c[1]) else 0)
        w.writerow([z["properties"]["ZCTA5CE10"], best[0]["NAME"]])

features = [{"type": "Feature", "properties": {"county": rec["NAME"]},
             "geometry": mapping(g.simplify(0.0015, preserve_topology=True))}
            for rec, g in counties if rec["NAME"] in mapped]
(DATA / "counties.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                                  separators=(",", ":")))
print(f"{len(features)} counties: {sorted(mapped)}")
