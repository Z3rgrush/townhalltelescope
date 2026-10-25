"""Add the governments the master list found on a platform the collector already reads to collector/sources.yaml.

Reads data/governments.csv (scripts/master_list.py) and appends a source entry for each government
with status "found" and a collector, unless its id is already a source. Largest first.

Usage: python scripts/add_sources.py [--limit 50] [--dry-run]
"""
import argparse
import csv
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402


def entry(g):
    """The sources.yaml entry for a master-list row, or None when its platform needs hand setup."""
    cfg = dict(kv.split("=", 1) for kv in g["config"].split() if "=" in kv)
    url = g["meetings_url"]
    src = {"id": g["id"], "name": g["name"], "county": g["county"], "adapter": g["collector"]}
    match g["collector"]:
        case "legistar":
            src["client"] = cfg["client"]
        case "civicclerk" | "primegov":
            src["tenant"] = cfg["tenant"]
        case "agendacenter":
            src["base"] = cfg["url"].split("/AgendaCenter")[0].split("/agendacenter")[0]
        case "granicus":
            src["base"] = f"https://{cfg['host']}"
            src["view_id"] = int(cfg.get("view_id", 1))
        case "escribe" | "civicweb":
            src["base"] = f"https://{cfg['host']}"
        case "onbase":
            src["base"] = url.split("OnBaseAgendaOnline")[0] + "OnBaseAgendaOnline"
        case "linkscan":
            src["pages"] = [cfg.get("url") or url]
        case _:
            return None
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    region = REGIONS[args.region]
    path = ROOT / region["sources"]
    have = {s["id"] for s in yaml.safe_load(path.open())}
    rows = [g for g in csv.DictReader((ROOT / region["data"] / "governments.csv").open())
            if g["status"] == "found" and g["collector"] and g["id"] not in have]
    rows.sort(key=lambda g: -int(g["population"]))
    new, skipped = [], []
    for g in rows:
        src = entry(g)
        (new if src else skipped).append(src or g)
    if args.limit:
        new = new[:args.limit]
    for g in skipped:
        print(f"skipped {g['name']} ({g['collector']}): needs hand setup")
    print(f"adding {len(new)} sources: {', '.join(s['id'] for s in new)}")
    if args.dry_run or not new:
        return
    with path.open("a") as f:
        f.write("\n# Added from the master list (data/governments.csv), largest first.\n")
        f.write(yaml.safe_dump(new, sort_keys=False, allow_unicode=True, width=200))


if __name__ == "__main__":
    main()
