"""Fill in share_in_<county> columns of a region's zips.csv: the share of each zip's land in that county.

Uses the 2020 Census county boundaries and the zip shapes already in pilot_zips.geojson. Adds a
column for any county not yet in the file and overwrites the named counties' columns.

Usage: python scripts/county_shares.py --region ks Sedgwick Shawnee Douglas
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
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="ks", choices=REGIONS)
ap.add_argument("counties", nargs="+")
args = ap.parse_args()
region = REGIONS[args.region]
DATA = ROOT / region["data"]

z = zipfile.ZipFile(io.BytesIO(requests.get(
    "https://www2.census.gov/geo/tiger/TIGER2020/COUNTY/tl_2020_us_county.zip", timeout=600).content))
stem = "tl_2020_us_county"
r = shapefile.Reader(shp=io.BytesIO(z.read(f"{stem}.shp")), shx=io.BytesIO(z.read(f"{stem}.shx")),
                     dbf=io.BytesIO(z.read(f"{stem}.dbf")))
counties = {}
for i, rec in enumerate(r.iterRecords(fields=["STATEFP", "NAME"])):
    if rec["STATEFP"] == region["state_fips"] and rec["NAME"] in args.counties:
        counties[rec["NAME"]] = shape(r.shape(i).__geo_interface__).buffer(0)
missing = set(args.counties) - set(counties)
if missing:
    sys.exit(f"no such counties: {sorted(missing)}")

zips = {f["properties"]["ZCTA5CE10"]: shape(f["geometry"]).buffer(0)
        for f in json.loads((DATA / "pilot_zips.geojson").read_text())["features"]}
with open(DATA / "zips.csv") as f:
    reader = csv.DictReader(f)
    fields, rows = list(reader.fieldnames), list(reader)
for name in args.counties:
    col = f"share_in_{name.lower().replace(' ', '_')}"
    if col not in fields:
        # Keep the share columns together, before governments_to_track.
        last = max(i for i, k in enumerate(fields) if k.startswith("share_in_"))
        fields.insert(last + 1, col)
    g = counties[name]
    n = 0
    for row in rows:
        zg = zips.get(row["zip"])
        share = zg.intersection(g).area / zg.area if zg is not None and zg.intersects(g) else 0
        row[col] = f"{share:.2f}".rstrip("0").rstrip(".") if share >= 0.005 else "0"
        n += share >= 0.005
    print(f"{name}: {n} zips")
with open(DATA / "zips.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
