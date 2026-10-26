"""Give governments with thin records a year of meetings instead of three months.

Marks every source whose last collection found THIN documents or fewer with `days: 365` in
collector/sources.yaml (edited as text so the comments stay). Sources already marked are left alone,
so a government that fills up after going back a year keeps its year.

Usage: python scripts/set_lookback.py [--thin 6] [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--thin", type=int, default=6)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    region = REGIONS[args.region]
    cov = json.loads((ROOT / region["data"] / "coverage.json").read_text())
    path = ROOT / region["sources"]
    lines = path.read_text().splitlines(keepends=True)

    out, current, marked, changed = [], None, set(), []
    for line in lines:
        m = re.match(r"- id: (\S+)", line)
        if m:
            current = m.group(1)
        elif current and re.match(r"  days:", line):
            marked.add(current)
        out.append(line)
    result = []
    for line in out:
        result.append(line)
        m = re.match(r"- id: (\S+)", line)
        if m:
            sid = m.group(1)
            c = cov.get(sid)
            if c and sid not in marked and c["documents"] <= args.thin:
                result.append(f"  days: {args.days}\n")
                changed.append(f"{sid} ({c['documents']} documents)")
    print(f"{len(changed)} sources now look back {args.days} days: {', '.join(changed)}")
    if not args.dry_run:
        path.write_text("".join(result))


if __name__ == "__main__":
    main()
