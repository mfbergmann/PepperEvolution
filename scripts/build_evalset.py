#!/usr/bin/env python3
"""
Build the evaluation set (docs/ORCHESTRATOR.md, phase 2) from the solo session tree: the turns a replay
of the routing may later be judged on, as keys and flags only — never what anyone said.

    .venv/bin/python scripts/build_evalset.py \
        --sessions results/sessions --marks results/marks/marks.jsonl --out results/evalset/evalset-v1.jsonl

If ``--out`` already exists it is read as the previous set: those keys keep their split and stay in the
set, so a comparison made on one set is still comparable after later builds. Prints the summary only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestra import evalset  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default="results/sessions")
    ap.add_argument("--marks", default="results/marks/marks.jsonl")
    ap.add_argument("--out", default="results/evalset/evalset-v1.jsonl")
    args = ap.parse_args()
    previous = evalset.read(args.out)  # the set stays fixed: the same key always sits on the same side
    turns = evalset.build(args.sessions, args.marks, previous=previous)
    evalset.write(turns, args.out)
    print(json.dumps(evalset.summary(turns), indent=1))


if __name__ == "__main__":
    main()
