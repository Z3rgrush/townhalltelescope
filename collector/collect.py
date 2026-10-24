"""Collect the last N days of agendas and minutes for every source and save their text.

Usage: python -m collector.collect [--region ks] [--days 90] [--only overland-park,leawood]

Writes, under the region's data folder (data/ for Kansas, data/co/ for Colorado):
  data/meetings.jsonl   one row per document (source, body, date, kind, url, text file)
  data/text/<source>/   extracted plain text, one file per document
  data/coverage.md      what worked and what didn't, per source
"""
import argparse
import hashlib
import json
import re
import traceback
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from .adapters import ADAPTERS
from .common import extract_text
from .regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ks", choices=REGIONS)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-text", action="store_true", help="list documents without downloading them")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    region = REGIONS[args.region]
    DATA = ROOT / region["data"]
    sources = yaml.safe_load(open(ROOT / region["sources"]))
    only = {s for s in args.only.split(",") if s}

    old = {}
    manifest = DATA / "meetings.jsonl"
    if manifest.exists():
        for line in manifest.open():
            row = json.loads(line)
            old[row["url"]] = row

    rows, coverage = [], []
    for src in sources:
        if only and src["id"] not in only:
            continue
        print(f"\n== {src['name']} ({src['adapter']})")
        log_lines = []

        def log(msg):
            print(msg)
            log_lines.append(msg)

        docs, error = [], None
        try:
            # "also" lists extra platforms for the same government (Broomfield's boards and council videos).
            for cfg in [src] + [{**src, **x} for x in src.get("also", [])]:
                docs += ADAPTERS[cfg["adapter"]](cfg, start, end, log)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            traceback.print_exc()

        # Keep minutes when a meeting has them; otherwise its agenda stands in until minutes are posted.
        by_meeting = defaultdict(list)
        for d in docs:
            by_meeting[(d.body, d.date)].append(d)
        keep = []
        for group in by_meeting.values():
            minutes = [d for d in group if d.kind == "minutes"]
            # CivicWeb posts each document as both "- Pdf" and "- Html"/"- Adopted"; keep one copy.
            variants = {}
            # keep_agendas: some cities scan their minutes as images, so the agenda is the readable copy.
            for d in (group if src.get("keep_agendas") else minutes or group):
                key = re.sub(r"\s*-\s*(pdf|html|adopted)\b", "", d.title, flags=re.I).strip().lower()
                if key not in variants or re.search(r"-\s*pdf\b", variants[key].title, re.I):
                    variants[key] = d
            keep.extend(variants.values())

        stats = defaultdict(int)
        for d in keep:
            inline = d.extra.pop("text", None)  # adapters that read text from an API hand it over directly
            row = {
                "source": d.source, "body": d.body, "date": d.date.isoformat(), "kind": d.kind,
                "title": d.title[:200], "url": d.url, **d.extra,
            }
            if args.no_text:
                stats["listed"] += 1
                rows.append(row)
                continue
            prev = old.get(d.url)
            if prev and prev.get("status") == "ok" and (ROOT / prev["text_file"]).exists():
                rows.append(prev)
                stats["ok"] += 1
                continue
            try:
                text, status = (inline, "ok") if inline else extract_text(d.url)
            except Exception as e:
                text, status = "", f"download error: {e}"
            row["status"] = status
            if text.strip():
                name = f"{d.date.isoformat()}_{slug(d.body, 30)}_{d.kind}_{hashlib.md5(d.url.encode()).hexdigest()[:6]}.txt"
                path = DATA / "text" / d.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                row["text_file"] = str(path.relative_to(ROOT))
                row["chars"] = len(text)
            stats["ok" if status == "ok" else "failed"] += 1
            print(f"  [{status}] {d.date} {d.kind:8} {d.body[:30]:30} {d.url[:90]}")
            rows.append(row)

        meetings = len(by_meeting)
        coverage.append((src, meetings, len(keep), dict(stats), error, log_lines))

    # Rows for sources not run this time are carried over unchanged.
    ran = {c[0]["id"] for c in coverage}
    rows += [r for r in old.values() if r["source"] not in ran]
    rows = [r for r in rows if r["date"] >= start.isoformat()]
    rows.sort(key=lambda r: (r["source"], r["date"], r["url"]))
    with manifest.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    with (DATA / "coverage.md").open("w") as f:
        f.write(f"# Collection coverage\n\nWindow: {start} to {end}\n\n")
        f.write("| Source | Adapter | Meetings | Documents | Text OK | Failed | Error |\n|---|---|---|---|---|---|---|\n")
        for src, meetings, ndocs, stats, error, _ in coverage:
            f.write(f"| {src['name']} | {src['adapter']} | {meetings} | {ndocs} | {stats.get('ok', 0)} | {stats.get('failed', 0)} | {error or ''} |\n")
        f.write("\n## Logs\n")
        for src, *_, lines in coverage:
            f.write(f"\n### {src['name']}\n```\n" + "\n".join(lines) + "\n```\n")


if __name__ == "__main__":
    main()
