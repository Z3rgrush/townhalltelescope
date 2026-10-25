"""Work out which cities each pilot zip code falls in, from Census city boundaries.

Downloads the 2020 TIGER/Line place file for the region's state, overlays it on the region's
pilot_zips.geojson and writes zip_places.csv with the share of each zip's land inside each city.

Usage: python scripts/zip_places.py [--region co]
"""
import argparse
import csv
import io
import sys
import json
import zipfile
from pathlib import Path

import requests
import shapefile  # pyshp
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="co", choices=REGIONS)
region = REGIONS[ap.parse_args().region]
DATA = ROOT / region["data"]
stem = f"tl_2020_{region['state_fips']}_place"
PLACES = f"https://www2.census.gov/geo/tiger/TIGER2020/PLACE/{stem}.zip"

z = zipfile.ZipFile(io.BytesIO(requests.get(PLACES, timeout=120).content))
reader = shapefile.Reader(
    shp=io.BytesIO(z.read(f"{stem}.shp")), shx=io.BytesIO(z.read(f"{stem}.shx")), dbf=io.BytesIO(z.read(f"{stem}.dbf"))
)
places = []
for rec, shp in zip(reader.records(), reader.shapes()):
    if rec["FUNCSTAT"] == "A":  # places with an active government, not census-designated places
        places.append((rec["NAME"], shape(shp.__geo_interface__).buffer(0)))

zips = json.load(open(DATA / "pilot_zips.geojson"))["features"]
rows = []
for f in zips:
    zc = f["properties"]["ZCTA5CE10"]
    g = shape(f["geometry"]).buffer(0)
    covered = 0.0
    for name, pg in places:
        if not g.intersects(pg):
            continue
        share = g.intersection(pg).area / g.area
        if share >= 0.005:  # low enough to keep small mountain towns in their huge zips
            rows.append([zc, name, round(share, 3)])
            covered += share
    rows.append([zc, "(unincorporated)", round(max(0.0, 1 - covered), 3)])

rows.sort(key=lambda r: (r[0], -r[2]))
with open(DATA / "zip_places.csv", "w", newline="") as out:
    w = csv.writer(out)
    w.writerow(["zip", "city", "share_of_zip_area"])
    w.writerows(rows)
print(f"{len(rows)} rows")
