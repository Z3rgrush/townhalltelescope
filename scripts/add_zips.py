"""Add the zip codes that make up given cities to a region's map.

Downloads the 2010 Census ZCTA file and 2020 place boundaries for the region's state, picks every
zip where one of the cities covers at least MIN_SHARE of the zip's land (or the zip holds at
least MIN_CITY of the city), and appends those zips to pilot_zips.geojson and zips.csv. Run
zip_places.py afterwards to refresh the zip-to-city overlay.

Usage: python scripts/add_zips.py --region co Golden Arvada "Wheat Ridge"
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
from collector.regions import REGIONS  # noqa: E402

MIN_SHARE, MIN_CITY = 0.10, 0.02

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="co", choices=REGIONS)
ap.add_argument("cities", nargs="+")
args = ap.parse_args()
region = REGIONS[args.region]
DATA = ROOT / region["data"]
fips = region["state_fips"]


def shapes(url, stem):
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=300).content))
    r = shapefile.Reader(shp=io.BytesIO(z.read(f"{stem}.shp")), shx=io.BytesIO(z.read(f"{stem}.shx")),
                         dbf=io.BytesIO(z.read(f"{stem}.dbf")))
    return [(rec.as_dict(), shape(s.__geo_interface__).buffer(0)) for rec, s in zip(r.records(), r.shapes())]


places = shapes(f"https://www2.census.gov/geo/tiger/TIGER2020/PLACE/tl_2020_{fips}_place.zip", f"tl_2020_{fips}_place")
cities = {rec["NAME"]: g for rec, g in places if rec["NAME"] in args.cities and rec["FUNCSTAT"] == "A"}
missing = set(args.cities) - set(cities)
if missing:
    sys.exit(f"no such places: {sorted(missing)}")
zctas = shapes(f"https://www2.census.gov/geo/tiger/TIGER2010/ZCTA5/2010/tl_2010_{fips}_zcta510.zip",
               f"tl_2010_{fips}_zcta510")

geo_path = DATA / "pilot_zips.geojson"
geo = json.loads(geo_path.read_text())
have = {f["properties"]["ZCTA5CE10"] for f in geo["features"]}
with open(DATA / "zips.csv") as f:
    reader = csv.DictReader(f)
    fields, rows = reader.fieldnames, list(reader)

added = []
for rec, g in zctas:
    zc = rec["ZCTA5CE10"]
    if zc in have:
        continue
    names = [n for n, cg in cities.items() if g.intersects(cg) and
             (g.intersection(cg).area / g.area >= MIN_SHARE or g.intersection(cg).area / cg.area >= MIN_CITY)]
    if not names:
        continue
    geo["features"].append({"type": "Feature", "properties": rec, "geometry": mapping(g)})
    row = {k: "0" for k in fields}
    row["zip"] = zc
    for k in fields:
        if not k.startswith("share_in_") and k != "zip":
            row[k] = "; ".join(names)
    rows.append(row)
    added.append((zc, names))

geo["features"].sort(key=lambda f: f["properties"]["ZCTA5CE10"])
geo_path.write_text(json.dumps(geo, separators=(",", ":")))
rows.sort(key=lambda r: r["zip"])
with open(DATA / "zips.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
for zc, names in added:
    print(zc, ", ".join(names))
print(f"added {len(added)} zips")
