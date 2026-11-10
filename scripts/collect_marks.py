#!/usr/bin/env python3
"""
Keep the tester's blind marks as labels (docs/ORCHESTRATOR.md): one line per marked turn in
``results/marks/marks.jsonl`` (git-ignored, mode 600), keyed by turn id, with which model's reply was better.

    .venv/bin/python scripts/collect_marks.py results/system-one/minds/replay-blind.md \
        --baseline claude-sonnet-5-5 --candidate DeepSeek-V4-Flash-0731

The key is the ``-blind-key.json`` next to the sheet. Collecting the same sheet again replaces its lines.
"""

import argparse
import json
import os
import re
from datetime import datetime

MARK = re.compile(r"Better:\**\s*([AB=])", re.I)
HEADING = re.compile(r"^## (\d+)\.", re.M)


def parse(sheet: str) -> dict:
    """{n: "A" | "B" | "="} for every turn that was marked."""
    text = open(sheet, encoding="utf-8").read()
    marks = {}
    starts = [(int(m.group(1)), m.end()) for m in HEADING.finditer(text)] + [(None, len(text))]
    for (n, start), (_, end) in zip(starts, starts[1:]):
        found = MARK.search(text[start:end])
        if found:
            marks[n] = found.group(1).upper()
    return marks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--key")
    ap.add_argument("--baseline", required=True, help="the model behind 'baseline' in the key")
    ap.add_argument("--candidate", required=True, help="the model behind 'candidate' in the key")
    ap.add_argument("--out", default="results/marks/marks.jsonl")
    ap.add_argument("--by", default="tester")
    args = ap.parse_args()
    key_path = args.key or args.sheet.replace("-blind.md", "-blind-key.json")
    key = {k["n"]: k for k in json.load(open(key_path))}
    models = {"baseline": args.baseline, "candidate": args.candidate}
    sheet = os.path.basename(args.sheet)
    rows = []
    for n, mark in sorted(parse(args.sheet).items()):
        k = key[n]
        better = "equal" if mark == "=" else k[mark]
        session = k.get("session", "")
        rows.append(
            {
                "turn_id": k.get("turn_id") or (f"{session}@{k['at']}" if session else k["at"]),
                "session": session or None,
                "at": k["at"],
                "sheet": sheet,
                "n": n,
                "better": better,  # baseline, candidate or equal
                "better_model": models.get(better),
                "pair": [args.baseline, args.candidate],
                "by": args.by,
                "collected": datetime.now().isoformat(timespec="seconds"),
            }
        )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    kept = []
    if os.path.exists(args.out):
        kept = [line for line in open(args.out, encoding="utf-8") if json.loads(line).get("sheet") != sheet]
    with open(args.out, "w", encoding="utf-8") as f:
        f.writelines(kept + [json.dumps(r, ensure_ascii=False) + "\n" for r in rows])
    os.chmod(args.out, 0o600)
    tally = {b: sum(r["better"] == b for r in rows) for b in ("baseline", "candidate", "equal")}
    print(f"{len(rows)} marks from {sheet} -> {args.out}: {tally}")


if __name__ == "__main__":
    main()
