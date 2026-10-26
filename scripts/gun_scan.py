"""Make sure gun policy talk reaches the map even when it was a small item in a long meeting.

Issue extraction keeps the 4 to 15 issues most worth a resident's attention, so a short firearm item
can be left out. This finds governments whose condensed meetings mention firearms, asks Claude for
just the firearm items in them, and adds each one to the government's issues with the guns tag
(or tags the existing issue that covers the same meetings). Governments are rescanned only when
their condensed meetings change (hashes in data/issues/gun_scan.json).

Usage: python scripts/gun_scan.py [--only lyons,erie] [--all]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from collector.regions import REGIONS  # noqa: E402
from extract_issues import GUNS, ISSUE_SCHEMA, MODEL, digest, to_yaml_entries  # noqa: E402

# "Second Amendment to the lease" is a contract change, and Gun Club Road is a road.
MENTION = re.compile(r"firearm|\bguns?\b(?!\s+club)|shooting range|concealed carry|second amendment (?!to\b)"
                     r"|assault weapon|semi-automatic|ghost gun|red flag", re.I)

SYSTEM = f"""You read the condensed agendas and minutes of one Colorado local government from the last three months and list only the items about firearms, for a public map where residents look up what their local governments are talking about.

Each meeting document starts with a heading like "## [lyons#12] 2026-08-03 | Board of Trustees | minutes". The number after the # is that document's ref.

An item counts when {GUNS.removeprefix("guns is true only when ")} Contract amendments that happen to be called a "Second Amendment", roads named Gun Club, and routine evidence or firearm destruction by police do not count.

Write each item as an issue a resident can follow: a specific title, one to three plain sentences on what happened with names and dates from the documents, where it is about (or ""), every ref where it came up, public_comment true only when residents or groups spoke on it, data_center false, guns true, and its topics from the allowed list (include "Guns"). Say only what the documents support. If nothing qualifies, return an empty list."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--only", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()
    data = ROOT / REGIONS[args.region]["data"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; skipping the gun scan.")
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    index = json.loads((data / "condensed" / "index.json").read_text())
    state_path = data / "issues" / "gun_scan.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    only = {s for s in args.only.split(",") if s}
    todo = {}
    for f in sorted((data / "condensed").glob("*.md")):
        source, text = f.stem, f.read_text()
        if (only and source not in only) or not (data / "issues" / f"{source}.yaml").exists():
            continue
        if not args.all and not only and state.get(source) == digest(text):
            continue
        if MENTION.search(text):
            todo[source] = text
        else:
            state[source] = digest(text)
    if not todo:
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
        print("No new firearm mentions to scan.")
        return
    print(f"Scanning {len(todo)} governments for firearm items: {', '.join(todo)}")

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[
        Request(custom_id=source, params=MessageCreateParamsNonStreaming(
            model=args.model,
            max_tokens=6000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ISSUE_SCHEMA}},
        ))
        for source, text in todo.items()
    ])
    print(f"Batch {batch.id} submitted")
    while batch.processing_status != "ended":
        time.sleep(30)
        batch = client.messages.batches.retrieve(batch.id)
    for result in client.messages.batches.results(batch.id):
        source = result.custom_id
        if result.result.type != "succeeded" or result.result.message.stop_reason != "end_turn":
            print(f"{source}: not scanned ({result.result.type})")
            continue
        text = next(b.text for b in result.result.message.content if b.type == "text")
        valid = {int(k.split("#")[1]) for k in index if k.split("#")[0] == source}
        found = to_yaml_entries(json.loads(text)["issues"], valid)
        path = data / "issues" / f"{source}.yaml"
        issues = yaml.safe_load(path.open()) or []
        added = tagged = 0
        for g in found:
            # An existing issue that shares a meeting and was already about guns covers it.
            same = [it for it in issues if set(it["refs"]) & set(g["refs"]) and "guns" in it.get("tags", [])]
            if same:
                continue
            match = next((it for it in issues if set(it["refs"]) & set(g["refs"])
                          and MENTION.search(it["title"] + " " + it["summary"])), None)
            if match:
                match["tags"] = list(dict.fromkeys(match.get("tags", []) + ["guns"]))
                match["topics"] = list(dict.fromkeys(match.get("topics", []) + ["Guns"]))[:3]
                tagged += 1
            else:
                issues.append(g)
                added += 1
        with open(path, "w") as f:
            yaml.safe_dump(issues, f, sort_keys=False, allow_unicode=True, width=10_000)
        state[source] = digest(todo[source])
        print(f"{source}: {len(found)} firearm items, {added} added, {tagged} existing issues tagged")
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
