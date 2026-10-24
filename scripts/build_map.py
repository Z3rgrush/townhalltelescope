"""Rank each zip's issues from <data>/issues/*.yaml and write the map's data files into <docs>/data/.

An issue's weight is the number of distinct meeting dates where it came up. A zip gets issues
from every government that covers part of it, scaled down when that government covers only a
small share of the zip. Writes docs/data/issues.json (every issue, by id), docs/data/zips.json
(each zip's governments and ranked issue ids) and docs/data/zips.geojson.
Run with --region ks (default) or --region co; paths come from collector/regions.py.
"""
import argparse
import csv
import sys
import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
from regions import REGIONS  # noqa: E402

TOP = 8

# Census place name -> source id in the region's sources file
KS_PLACES = {
    "Kansas City": "unified-government",
    "Bonner Springs": "bonner-springs",
    "Edwardsville": "edwardsville",
    "De Soto": "de-soto",
    "Edgerton": "edgerton",
    "Fairway": "fairway",
    "Gardner": "gardner",
    "Lake Quivira": "lake-quivira",
    "Leawood": "leawood",
    "Lenexa": "lenexa",
    "Merriam": "merriam",
    "Mission": "mission",
    "Mission Hills": "mission-hills",
    "Mission Woods": "mission-woods",
    "Olathe": "olathe",
    "Overland Park": "overland-park",
    "Prairie Village": "prairie-village",
    "Roeland Park": "roeland-park",
    "Shawnee": "shawnee",
    "Spring Hill": "spring-hill",
    "Westwood": "westwood",
    "Westwood Hills": "westwood-hills",
    "Wichita": "wichita",
    "Topeka": "topeka",
    "Lawrence": "lawrence",
    "Manhattan": "manhattan",
    "Salina": "salina",
}
CO_PLACES = {
    "Denver": "denver",
    "Boulder": "boulder",
    "Longmont": "longmont",
    "Broomfield": "broomfield",
    "Erie": "erie",
    "Lafayette": "lafayette",
    "Louisville": "louisville",
    "Superior": "superior",
    "Glendale": "glendale",
    "Aurora": "aurora",
    "Thornton": "thornton",
    "Frederick": "frederick",
    "Firestone": "firestone",
    "Mead": "mead",
    "Commerce City": "commerce-city",
    "Lyons": "lyons",
    "Nederland": "nederland",
}
# Per region: place map, the county columns in zips.csv, and which source stands for each
# county's government. A county source of None means the county is also a city (Denver,
# Broomfield), so its city share already covers it.
SETUP = {
    "ks": {"places": KS_PLACES,
           "counties": {"share_in_johnson": "johnson-county", "share_in_wyandotte": "unified-government"}},
    "co": {"places": CO_PLACES,
           "counties": {"share_in_denver": None, "share_in_boulder": "boulder-county", "share_in_broomfield": None}},
}
NAMES = {
    "unified-government": "Unified Government (KCK / Wyandotte County)",
    "johnson-county": "Johnson County",
    "mission-woods": "Mission Woods",
    "westwood": "Westwood",
    "wichita": "Wichita",
}


def load_names(region):
    for s in yaml.safe_load((ROOT / region["sources"]).open()):
        NAMES.setdefault(s["id"], s["name"])


def load_issues(data, index):
    issues = defaultdict(list)
    for f in sorted((data / "issues").glob("*.yaml")):
        source = f.stem
        for it in yaml.safe_load(f.open()):
            docs = [index[f"{source}#{r}"] for r in it["refs"]]
            seen, sources = set(), []
            for d in sorted(docs, key=lambda d: d["date"], reverse=True):
                key = (d["date"], d["body"])
                if key in seen:
                    continue
                seen.add(key)
                sources.append({"date": d["date"], "body": d["body"], "kind": d["kind"],
                                "url": d.get("public_url") or d["url"]})
            issues[source].append({
                "id": f"{source}/{len(issues[source])}",
                "title": it["title"],
                "summary": it["summary"],
                "where": it.get("where"),
                "public_comment": bool(it.get("public_comment")),
                "tags": it.get("tags", []),
                "site_zip": it.get("site_zip"),
                "meetings": len({d["date"] for d in docs}),
                "sources": sources,
            })
    return issues


def zip_governments(data, setup):
    """zip -> list of (source id, share of zip area), largest first."""
    counties = setup["counties"]
    county = {}
    for r in csv.DictReader((data / "zips.csv").open()):
        county[r["zip"]] = {col: float(r[col]) for col in counties}
    govs = defaultdict(dict)
    for r in csv.DictReader((data / "zip_places.csv").open()):
        share = float(r["share_of_zip_area"])
        if r["city"] == "(unincorporated)":
            shares = county.get(r["zip"], {})
            col = max(shares, key=shares.get) if shares else None
            source = counties[col] if col and shares[col] > 0 else None
        else:
            source = setup["places"].get(r["city"])
        if source and share >= 0.005:
            govs[r["zip"]][source] = govs[r["zip"]].get(source, 0) + share
    # The Unified Government is also the county government for all of Wyandotte. Other
    # county governments are listed with their county share when no unincorporated land
    # already put them there.
    for z, shares in county.items():
        for col, source in counties.items():
            if not source or shares[col] <= 0:
                continue
            if source == "unified-government":
                govs[z][source] = max(govs[z].get(source, 0), shares[col])
            elif source not in govs[z]:
                govs[z][source] = shares[col]
    return {z: sorted(g.items(), key=lambda kv: -kv[1]) for z, g in govs.items()}


def tagged(z, ranked):
    """tag -> every issue with that tag in the zip (not just the top ones), and whether the
    zip holds the site itself or only shares a government with it."""
    out = {}
    for _, it in ranked:
        for t in it["tags"]:
            entry = out.setdefault(t, {"ids": [], "site": False})
            entry["ids"].append(it["id"])
            entry["site"] |= it["site_zip"] == z
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ks", choices=sorted(REGIONS))
    args = ap.parse_args()
    region = REGIONS[args.region]
    data, out_dir = ROOT / region["data"], ROOT / region["docs"] / "data"
    load_names(region)
    index = json.loads((data / "condensed" / "index.json").read_text())
    issues = load_issues(data, index)
    out = {}
    for z, govs in sorted(zip_governments(data, SETUP[args.region]).items()):
        ranked = []
        for source, share in govs:
            weight = min(1.0, 0.4 + share * 1.5)
            for it in issues.get(source, []):
                score = it["meetings"] * weight * (1.25 if it["public_comment"] else 1)
                ranked.append((score, it))
        ranked.sort(key=lambda t: -t[0])
        out[z] = {
            "governments": [{"name": NAMES.get(s, s), "share": round(sh, 3),
                             "has_records": s in issues} for s, sh in govs],
            "issues": [it["id"] for _, it in ranked[:TOP]],
            "tagged": tagged(z, ranked),
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "zips.json").write_text(json.dumps(out, indent=1))
    table = {it["id"]: {**it, "government": NAMES.get(s, s)} for s, its in issues.items() for it in its}
    (out_dir / "issues.json").write_text(json.dumps(table, indent=1))
    geo = json.loads((data / "pilot_zips.geojson").read_text())
    for f in geo["features"]:
        f["properties"] = {"zip": f["properties"]["ZCTA5CE10"]}
    (out_dir / "zips.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    total = sum(len(v) for v in issues.values())
    print(f"{len(out)} zips, {total} issues from {len(issues)} governments")


if __name__ == "__main__":
    main()
