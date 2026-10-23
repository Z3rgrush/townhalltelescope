"""Boil each collected document down to its agenda items, for reading and issue extraction.

Writes data/condensed/<source>.md: one section per meeting with the lines that name business
(hearings, ordinances, rezonings, contracts, public comment), boilerplate removed.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 2500  # characters kept per document

SKIP = re.compile(
    r"roll call|pledge of allegiance|call to order|adjourn|invocation|^present:|^absent:|moved by|seconded by|"
    r"approved \d+\s*[-–]|denied \d|abstain|no vote required|page \d+|^\d+$|americans with disabilities|"
    r"accommodation|livestream|youtube|zoom|telephone|www\.|http|@|^\W*$",
    re.I,
)
BUSINESS = re.compile(
    r"ordinance|resolution|rezon|zoning|plat|site plan|special use|hearing|public comment|contract|agreement|"
    r"budget|tax|levy|bond|tif|incentive|development|project|street|road|sewer|storm|water|park|police|fire|"
    r"housing|apartment|warehouse|data center|grant|fee|rate|annex|permit|vacat|amend|approv|consider|award|"
    r"purchase|bid|appoint|discuss|presentation|request|application|plan\b|improvement",
    re.I,
)


def condense(text):
    out, seen, size = [], set(), 0
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" .•-–\t")
        if len(line) < 12 or SKIP.search(line) or not BUSINESS.search(line):
            continue
        key = line.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:300])
        size += len(out[-1])
        if size > LIMIT:
            break
    return out


def main():
    by_source = defaultdict(list)
    for line in (ROOT / "data" / "meetings.jsonl").open():
        r = json.loads(line)
        if r.get("text_file"):
            by_source[r["source"]].append(r)
    outdir = ROOT / "data" / "condensed"
    outdir.mkdir(exist_ok=True)
    total, index = 0, {}
    for source, rows in by_source.items():
        parts = [f"# {source}\n"]
        for i, r in enumerate(sorted(rows, key=lambda r: r["date"])):
            lines = condense((ROOT / r["text_file"]).read_text(errors="ignore"))
            if not lines:
                continue
            index[f"{source}#{i}"] = {k: r.get(k) for k in ("date", "body", "kind", "title", "url", "public_url")}
            parts.append(f"\n## [{source}#{i}] {r['date']} | {r['body']} | {r['kind']}\n")
            parts.extend(f"- {l}\n" for l in lines)
        text = "".join(parts)
        total += len(text)
        (outdir / f"{source}.md").write_text(text)
    (outdir / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{total:,} characters across {len(by_source)} sources")


if __name__ == "__main__":
    main()
