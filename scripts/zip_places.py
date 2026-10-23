"""Work out which cities each pilot zip code falls in, from Census city boundaries.

Downloads the 2020 TIGER/Line place file for Kansas, overlays it on data/pilot_zips.geojson
and writes data/zip_places.csv with the share of each zip's land inside each city.
"""
import csv
import io
import json
import zipfile
from pathlib import Path

import requests
import shapefile  # pyshp
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
PLACES = "https://www2.census.gov/geo/tiger/TIGER2020/PLACE/tl_2020_20_place.zip"

z = zipfile.ZipFile(io.BytesIO(requests.get(PLACES, timeout=120).content))
stem = "tl_2020_20_place"
reader = shapefile.Reader(
    shp=io.BytesIO(z.read(f"{stem}.shp")), shx=io.BytesIO(z.read(f"{stem}.shx")), dbf=io.BytesIO(z.read(f"{stem}.dbf"))
)
places = []
for rec, shp in zip(reader.records(), reader.shapes()):
    if rec["LSAD"] == "25":  # incorporated cities only, not census-designated places
        places.append((rec["NAME"], shape(shp.__geo_interface__).buffer(0)))

zips = json.load(open(ROOT / "data" / "pilot_zips.geojson"))["features"]
rows = []
for f in zips:
    zc = f["properties"]["ZCTA5CE10"]
    g = shape(f["geometry"]).buffer(0)
    covered = 0.0
    for name, pg in places:
        if not g.intersects(pg):
            continue
        share = g.intersection(pg).area / g.area
        if share >= 0.02:
            rows.append([zc, name, round(share, 3)])
            covered += share
    rows.append([zc, "(unincorporated)", round(max(0.0, 1 - covered), 3)])

rows.sort(key=lambda r: (r[0], -r[2]))
with open(ROOT / "data" / "zip_places.csv", "w", newline="") as out:
    w = csv.writer(out)
    w.writerow(["zip", "city", "share_of_zip_area"])
    w.writerows(rows)
print(f"{len(rows)} rows")
