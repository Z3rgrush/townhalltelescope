"""Rank each zip's and county's issues from <data>/issues/*.yaml and write the map's data files
into <docs>/data/.

An issue's weight is the number of distinct meeting dates where it came up. A zip gets issues
from every government that serves its residents, scaled down when that government serves only
a small share of them: a town gets the share living inside it, a county government the share in
its unincorporated land. Counties work the same way, from each county government and the towns
with residents in the county. Shares come from data/zip_areas.csv (scripts/geography.py) and fall
back to land area for zips nobody lives in. Writes docs/data/issues.json (every issue, by id),
zips.json and counties.json (each area's governments and ranked issue ids), and both outlines.
Paths come from collector/regions.py.
"""
import argparse
import csv
import re
import sys
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
from regions import REGIONS  # noqa: E402

TOP = 8
TOP_COUNTY = 12
MIN_SHARE = 0.005  # of a zip's residents; small enough to keep a mountain town in its huge zip
MIN_CITY = 0.10  # of a town's residents, for the town to count in a county it barely crosses into

# A source id is the government's name as a slug ("Commerce City" -> commerce-city, Boulder
# County -> boulder-county). Denver and Broomfield are consolidated city-counties, so the city
# source is also the county government.
CITY_COUNTIES = {"Denver": "denver", "Broomfield": "broomfield"}
NAMES = {}


def slug(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()  # Cañon City -> canon-city
    return re.sub(r"[^a-z]+", "-", name.lower()).strip("-")


def place_source(place):
    return slug(place)


def county_source(county):
    return CITY_COUNTIES.get(county) or slug(county) + "-county"


def name_of(source, fallback):
    return NAMES.get(source, fallback)


def load_names(region):
    for s in yaml.safe_load((ROOT / region["sources"]).open()):
        NAMES.setdefault(s["id"], s["name"])


def load_issues(data, index):
    issues = defaultdict(list)
    for f in sorted((data / "issues").glob("*.yaml")):
        source = f.stem
        for it in yaml.safe_load(f.open()):
            docs = [index[k] for k in (f"{source}#{r}" for r in it["refs"]) if k in index]
            if not docs:
                # Refs are positions in the condensed file; they go stale when it's rebuilt without re-extracting.
                print(f"{source}: skipping '{it['title']}', none of its meetings are in the window any more")
                continue
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
                "topics": it.get("topics", []),
                "site_zip": it.get("site_zip"),
                "meetings": len({d["date"] for d in docs}),
                "sources": sources,
            })
    return issues


def load_pieces(data):
    """zip -> list of (county, place or "", population, land)."""
    pieces = defaultdict(list)
    for r in csv.DictReader((data / "zip_areas.csv").open()):
        pieces[r["zip"]].append((r["county"], r["place"], int(r["population"]), int(r["land_m2"])))
    return pieces


def zip_governments(pieces):
    """zip -> list of (source id, display name, share of the zip's residents), largest first."""
    out = {}
    for z, parts in pieces.items():
        col = 2 if sum(p[2] for p in parts) else 3  # residents, or land where nobody lives
        total = sum(p[col] for p in parts)
        govs, in_county = defaultdict(float), defaultdict(float)
        names = {}
        for county, place, *m in parts:
            share = m[col - 2] / total
            in_county[county] += share
            if place:
                src = place_source(place)
                names[src] = name_of(src, place)
                govs[src] += share
            elif county not in CITY_COUNTIES:
                src = county_source(county)
                names[src] = name_of(src, f"{county} County")
                govs[src] += share
        # Every county government serves the whole county, towns included, so it is listed even
        # when the zip has no unincorporated land. Its share stays the unincorporated one, so
        # rank() gives it the minimum weight inside towns.
        keep = set()
        for county, share in in_county.items():
            src = county_source(county)
            if share >= MIN_SHARE:
                names.setdefault(src, name_of(src, f"{county} County"))
                govs.setdefault(src, 0.0)
                keep.add(src)
        out[z] = sorted(((s, names[s], sh) for s, sh in govs.items() if sh >= MIN_SHARE or s in keep),
                        key=lambda t: -t[2])
    return out


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


def county_governments(pieces):
    """county -> list of (source id, display name, share of the town's residents in the county).
    The county government comes first, then the towns with residents in the county."""
    town = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    counties = set()
    for parts in pieces.values():
        for county, place, pop, land in parts:
            counties.add(county)
            if place:
                town[place][county][0] += pop
                town[place][county][1] += land
    out = {}
    for county in sorted(counties):
        src = county_source(county)
        govs = [(src, name_of(src, f"{county} County"), 1.0)]
        for place, by_county in sorted(town.items()):
            if county not in by_county or place_source(place) == src:
                continue
            col = 0 if sum(v[0] for v in by_county.values()) else 1
            share = by_county[county][col] / sum(v[col] for v in by_county.values())
            if share >= MIN_CITY:
                govs.append((place_source(place), name_of(place_source(place), place), share))
        out[county] = govs
    return out


def rank(govs, issues):
    ranked = []
    for source, share in govs:
        weight = min(1.0, 0.4 + share * 1.5)
        for it in issues.get(source, []):
            score = it["meetings"] * weight * (1.25 if it["public_comment"] else 1)
            ranked.append((score, it))
    ranked.sort(key=lambda t: -t[0])
    return ranked


def zip_county(pieces):
    """zip -> the county holding most of its residents (or land)."""
    out = {}
    for z, parts in pieces.items():
        col = 2 if sum(p[2] for p in parts) else 3
        by = defaultdict(int)
        for p in parts:
            by[p[0]] += p[col]
        out[z] = max(by, key=by.get)
    return out


def build_counties(pieces, issues):
    zc = zip_county(pieces)
    out = {}
    for county, govs in county_governments(pieces).items():
        ranked = rank([(s, sh) for s, _, sh in govs], issues)
        tags = {}
        for _, it in ranked:
            for t in it["tags"]:
                entry = tags.setdefault(t, {"ids": [], "site": False})
                entry["ids"].append(it["id"])
                entry["site"] |= zc.get(it["site_zip"]) == county
        # The word cloud: each topic weighted by how much its issues came up, like the issue ranking.
        topics = defaultdict(lambda: [0.0, []])
        for score, it in ranked:
            for t in it["topics"]:
                topics[t][0] += score
                topics[t][1].append(it["id"])
        out[county] = {
            "governments": [{"name": n, "share": round(sh, 3), "has_records": s in issues} for s, n, sh in govs],
            "issues": [it["id"] for _, it in ranked[:TOP_COUNTY]],
            "tagged": tags,
            "topics": {t: {"weight": round(w, 1), "issues": ids[:TOP_COUNTY]}
                       for t, (w, ids) in sorted(topics.items(), key=lambda kv: -kv[1][0])},
        }
    standouts(out)
    return {"counties": out, "zips": zc}


SMOOTH = 30  # weight a county's topic shares are pulled toward the state's, so tiny records can't top a topic


def standouts(counties):
    """For each county, the topics it gives more of its discussion to than any other county does.

    A topic's share is its weight over all the county's topic weight, smoothed toward the statewide
    share. Counties that lead no topic get their three highest-ranked ones instead."""
    have = {c: v["topics"] for c, v in counties.items() if v["topics"]}
    totals = {c: sum(t["weight"] for t in ts.values()) for c, ts in have.items()}
    state = defaultdict(float)
    for ts in have.values():
        for t, v in ts.items():
            state[t] += v["weight"]
    grand = sum(state.values()) or 1
    state = {t: w / grand for t, w in state.items()}
    share = {c: {t: (ts.get(t, {}).get("weight", 0) + SMOOTH * state[t]) / (totals[c] + SMOOTH) for t in state}
             for c, ts in have.items()}
    rank = {}
    for t in state:
        order = sorted(have, key=lambda c: -share[c][t])
        for i, c in enumerate(order):
            rank[c, t] = i + 1
    for c, v in counties.items():
        if c not in have:
            v["standouts"] = []
            continue
        mine = [t for t in have[c] if have[c][t]["weight"] > 0]
        lead = [t for t in mine if rank[c, t] == 1]
        pick = lead or sorted(mine, key=lambda t: (rank[c, t], -share[c][t]))[:3]
        pick.sort(key=lambda t: (rank[c, t], -share[c][t] / state[t]))
        v["standouts"] = [{"topic": t, "rank": rank[c, t], "of": len(have),
                           "share": round(have[c][t]["weight"] / totals[c], 3), "state": round(state[t], 3),
                           "issues": have[c][t]["issues"]} for t in pick]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=sorted(REGIONS))
    args = ap.parse_args()
    region = REGIONS[args.region]
    data, out_dir = ROOT / region["data"], ROOT / region["docs"] / "data"
    load_names(region)
    index = json.loads((data / "condensed" / "index.json").read_text())
    issues = load_issues(data, index)
    pieces = load_pieces(data)
    out = {}
    for z, govs in sorted(zip_governments(pieces).items()):
        ranked = rank([(s, sh) for s, _, sh in govs], issues)
        out[z] = {
            "governments": [{"name": n, "share": round(sh, 3), "has_records": s in issues} for s, n, sh in govs],
            "issues": [it["id"] for _, it in ranked[:TOP]],
            "tagged": tagged(z, ranked),
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "zips.json").write_text(json.dumps(out, indent=1))
    table = {it["id"]: {**it, "government": NAMES.get(s, s)} for s, its in issues.items() for it in its}
    (out_dir / "issues.json").write_text(json.dumps(table, indent=1))
    (out_dir / "zips.geojson").write_text((data / "zips.geojson").read_text())
    counties = build_counties(pieces, issues)
    (out_dir / "counties.json").write_text(json.dumps(counties, indent=1))
    (out_dir / "counties.geojson").write_text((data / "counties.geojson").read_text())
    total = sum(len(v) for v in issues.values())
    print(f"{len(counties['counties'])} counties, {len(out)} zips, {total} issues from {len(issues)} governments")


if __name__ == "__main__":
    main()
