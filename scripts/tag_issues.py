"""File issues that were extracted before topics existed under the fixed topic list, and flag gun talk.

New extractions (scripts/extract_issues.py) already carry topics and the guns flag. This fills them in for
issues that lack topics, from each issue's title and summary, one Batch API request per government.
Without ANTHROPIC_API_KEY it says so and exits without changing anything.

Usage: python scripts/tag_issues.py [--only lyons,erie] [--model claude-sonnet-5-5]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from collector.regions import REGIONS  # noqa: E402
from extract_issues import GUNS, MODEL, TOPICS  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "guns": {"type": "boolean"},
                    "topics": {"type": "array", "items": {"type": "string", "enum": TOPICS}},
                },
                "required": ["n", "guns", "topics"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}

SYSTEM = f"""You file local government issues from Colorado cities, towns and counties under topics, for a public map.

Each input issue has a number n, a title, a summary and sometimes a place. For every issue, return its n and:
- topics: the one to three topics from the allowed list that the issue is mostly about, most fitting first.
- {GUNS}

Return every issue you were given, once each."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--only", default="", help="comma-separated source ids")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()
    data = ROOT / REGIONS[args.region]["data"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; leaving issue files as they are.")
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    only = {s for s in args.only.split(",") if s}
    todo = {}
    for f in sorted((data / "issues").glob("*.yaml")):
        issues = yaml.safe_load(f.open()) or []
        if (only and f.stem not in only) or all("topics" in it for it in issues):
            continue
        todo[f.stem] = issues
    if not todo:
        print("Every issue already has topics.")
        return
    print(f"Tagging issues for {len(todo)} governments with {args.model}")

    def prompt(issues):
        return json.dumps([{"n": i, "title": it["title"], "summary": it["summary"], "where": it.get("where", "")}
                           for i, it in enumerate(issues)], ensure_ascii=False)

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[
        Request(custom_id=source, params=MessageCreateParamsNonStreaming(
            model=args.model,
            max_tokens=8000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt(issues)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        ))
        for source, issues in todo.items()
    ])
    print(f"Batch {batch.id} submitted")
    while batch.processing_status != "ended":
        time.sleep(30)
        batch = client.messages.batches.retrieve(batch.id)
    guns = 0
    for result in client.messages.batches.results(batch.id):
        source, issues = result.custom_id, todo[result.custom_id]
        if result.result.type != "succeeded" or result.result.message.stop_reason != "end_turn":
            print(f"{source}: not tagged ({result.result.type})")
            continue
        text = next(b.text for b in result.result.message.content if b.type == "text")
        for row in json.loads(text)["issues"]:
            if not 0 <= row["n"] < len(issues):
                continue
            it = issues[row["n"]]
            it["topics"] = list(dict.fromkeys(row["topics"]))[:3]
            tags = [t for t in it.get("tags", []) if t != "guns"] + (["guns"] if row["guns"] else [])
            guns += row["guns"]
            if tags:
                it["tags"] = tags
            else:
                it.pop("tags", None)
        with open(data / "issues" / f"{source}.yaml", "w") as f:
            yaml.safe_dump(issues, f, sort_keys=False, allow_unicode=True, width=10_000)
    print(f"Tagged {sum(len(v) for v in todo.values())} issues; {guns} about guns")


if __name__ == "__main__":
    main()
