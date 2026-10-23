"""Rank each zip's issues from data/issues/*.yaml and write the map's data files into docs/.

An issue's weight is the number of distinct meeting dates where it came up. A zip gets issues
from every government that covers part of it, scaled down when that government covers only a
small share of the zip. Writes docs/data/issues.json (every issue, by id), docs/data/zips.json
(each zip's governments and ranked issue ids) and docs/data/zips.geojson.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
TOP = 8

# Census place name -> source id in collector/sources.yaml
PLACE_SOURCE = {
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
}
NAMES = {
    "unified-government": "Unified Government (KCK / Wyandotte County)",
    "johnson-county": "Johnson County",
    "mission-woods": "Mission Woods",
    "westwood": "Westwood",
}


def load_names():
    for s in yaml.safe_load((ROOT / "collector" / "sources.yaml").open()):
        NAMES.setdefault(s["id"], s["name"])


def load_issues(index):
    issues = defaultdict(list)
    for f in sorted((ROOT / "data" / "issues").glob("*.yaml")):
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


def zip_governments():
    """zip -> list of (source id, share of zip area), largest first."""
    county = {}
    for r in csv.DictReader((ROOT / "data" / "zips.csv").open()):
        county[r["zip"]] = (float(r["share_in_johnson"]), float(r["share_in_wyandotte"]))
    govs = defaultdict(dict)
    for r in csv.DictReader((ROOT / "data" / "zip_places.csv").open()):
        share = float(r["share_of_zip_area"])
        if r["city"] == "(unincorporated)":
            jo, wy = county.get(r["zip"], (0, 0))
            source = "unified-government" if wy > jo else "johnson-county"
        else:
            source = PLACE_SOURCE.get(r["city"])
        if source and share >= 0.005:
            govs[r["zip"]][source] = govs[r["zip"]].get(source, 0) + share
    # The Unified Government is also the county government for all of Wyandotte.
    for z, (jo, wy) in county.items():
        if wy > 0:
            govs[z]["unified-government"] = max(govs[z].get("unified-government", 0), wy)
        if jo > 0 and "johnson-county" not in govs[z]:
            govs[z]["johnson-county"] = jo
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
    load_names()
    index = json.loads((ROOT / "data" / "condensed" / "index.json").read_text())
    issues = load_issues(index)
    out = {}
    for z, govs in sorted(zip_governments().items()):
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
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "zips.json").write_text(json.dumps(out, indent=1))
    table = {it["id"]: {**it, "government": NAMES.get(s, s)} for s, its in issues.items() for it in its}
    (OUT / "issues.json").write_text(json.dumps(table, indent=1))
    geo = json.loads((ROOT / "data" / "pilot_zips.geojson").read_text())
    for f in geo["features"]:
        f["properties"] = {"zip": f["properties"]["ZCTA5CE10"]}
    (OUT / "zips.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    total = sum(len(v) for v in issues.values())
    print(f"{len(out)} zips, {total} issues from {len(issues)} governments")


if __name__ == "__main__":
    main()
