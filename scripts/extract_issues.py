"""Write data/issues/<source>.yaml from data/condensed/<source>.md with Claude, through the Batch API.

Each government's condensed meetings go to Claude in one request, and the issues come back as
JSON matching ISSUE_SCHEMA. Only governments whose condensed text changed since the last run are
sent (hashes in data/issues/extracted.json), so hand-written issue files stay until their
meetings change. Without ANTHROPIC_API_KEY the script says so and exits without changing anything.

Usage: python scripts/extract_issues.py [--only lyons,erie] [--all] [--model claude-opus-5-5]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.regions import REGIONS  # noqa: E402

MODEL = os.environ.get("EXTRACT_MODEL") or "claude-opus-5-5"

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "where": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                    "public_comment": {"type": "boolean"},
                    "data_center": {"type": "boolean"},
                },
                "required": ["title", "summary", "where", "refs", "public_comment", "data_center"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}

SYSTEM = """You read the condensed agendas and minutes of one Colorado local government (a city, town or county) from the last three months and list the specific local issues it has been dealing with, for a public map where residents look up what their local governments are talking about.

Each meeting document in the input starts with a heading like "## [lyons#12] 2026-08-03 | Board of Trustees | minutes". The number after the # is that document's ref.

What makes a good issue:
- It is specific: a named project, place, ordinance, budget decision, fight or program, not a category. "Planet Bluegrass annexation on North St. Vrain Drive" rather than "Annexations"; "2027 budget and the 38-mill levy" rather than "Budget".
- Related items across meetings and boards are one issue. Gather every document where it came up into its refs.
- Routine business is left out: approving minutes or agendas, paying bills, consent items with nothing behind them, proclamations, staff introductions, board orientations, appointments of routine members, liquor licenses, executive sessions with no stated topic.
- The summary is one to three plain sentences on what happened and what is at stake, with names, amounts, addresses and dates from the documents. Write for a resident, not a clerk. Say only what the documents support; never guess at outcomes the documents don't state.
- where is the street address, intersection, neighborhood or facility the issue is about, or "Townwide" / "Citywide" / "Countywide" when it is not tied to one place, or "" if unclear.
- public_comment is true only when the documents show residents or outside groups spoke or wrote in about this issue.
- data_center is true only when the issue is about data centers (a proposed facility, rules, a moratorium, or their power or water use).

List the issues most worth a resident's attention first. Return between 4 and 15 issues; fewer if the documents hold little real business. If there is nothing but routine business, return an empty list."""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def to_yaml_entries(issues, valid_refs):
    out = []
    for it in issues:
        refs = sorted({r for r in it["refs"] if r in valid_refs})
        if not refs or not it["title"].strip():
            continue
        entry = {"title": it["title"].strip(), "summary": it["summary"].strip()}
        if it["where"].strip():
            entry["where"] = it["where"].strip()
        entry["refs"] = refs
        if it["data_center"]:
            entry["tags"] = ["data-center"]
        if it["public_comment"]:
            entry["public_comment"] = True
        out.append(entry)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--only", default="", help="comma-separated source ids")
    ap.add_argument("--all", action="store_true", help="send every source, changed or not")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()
    data = ROOT / REGIONS[args.region]["data"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; leaving issue files as they are.")
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    index = json.loads((data / "condensed" / "index.json").read_text())
    state_path = data / "issues" / "extracted.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    only = {s for s in args.only.split(",") if s}
    todo = {}
    for f in sorted((data / "condensed").glob("*.md")):
        source, text = f.stem, f.read_text()
        if only and source not in only:
            continue
        if not args.all and not only and state.get(source, {}).get("hash") == digest(text):
            continue
        todo[source] = text
    if not todo:
        print("No government's meetings changed since the last extraction.")
        return
    print(f"Extracting issues for {len(todo)} governments with {args.model}: {', '.join(todo)}")

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[
        Request(custom_id=source, params=MessageCreateParamsNonStreaming(
            model=args.model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ISSUE_SCHEMA}},
        ))
        for source, text in todo.items()
    ])
    print(f"Batch {batch.id} submitted")
    while batch.processing_status != "ended":
        time.sleep(30)
        batch = client.messages.batches.retrieve(batch.id)
    usage = {"input": 0, "output": 0}
    for result in client.messages.batches.results(batch.id):
        source = result.custom_id
        if result.result.type != "succeeded":
            print(f"{source}: {result.result.type}, keeping its old issues")
            continue
        msg = result.result.message
        usage["input"] += msg.usage.input_tokens + (msg.usage.cache_read_input_tokens or 0)
        usage["output"] += msg.usage.output_tokens
        if msg.stop_reason != "end_turn":
            print(f"{source}: stopped with {msg.stop_reason}, keeping its old issues")
            continue
        text = next(b.text for b in msg.content if b.type == "text")
        valid = {int(k.split("#")[1]) for k in index if k.split("#")[0] == source}
        entries = to_yaml_entries(json.loads(text)["issues"], valid)
        with open(data / "issues" / f"{source}.yaml", "w") as f:
            yaml.safe_dump(entries, f, sort_keys=False, allow_unicode=True, width=10_000)
        state[source] = {"hash": digest(todo[source]), "model": args.model, "date": time.strftime("%Y-%m-%d")}
        print(f"{source}: {len(entries)} issues")
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
    print(f"Tokens: {usage['input']:,} in, {usage['output']:,} out")


if __name__ == "__main__":
    main()
