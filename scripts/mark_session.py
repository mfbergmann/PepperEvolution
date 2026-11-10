#!/usr/bin/env python3
"""
Declare who was there in a recorded session (docs/ORCHESTRATOR.md, src/orchestra/manifest.py).

Sessions start as ``unknown``. After reviewing one, mark it ``solo`` (the tester alone with Pepper) or ``others``
(anyone else was there, or may have been). Only ``solo`` sessions may be replayed to destinations cleared for solo
data only (docs/DATA_FLOWS.md), such as Brine.

    .venv/bin/python scripts/mark_session.py --list
    .venv/bin/python scripts/mark_session.py results/sessions/2026-11-05_154622 solo --note "alone in the office"
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestra import manifest  # noqa: E402
from src.orchestra.engines import PRESENCE  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?")
    ap.add_argument("presence", nargs="?", choices=PRESENCE)
    ap.add_argument("--note", default="")
    ap.add_argument("--by", default="tester")
    ap.add_argument("--list", action="store_true", help="every session under --root and its presence")
    ap.add_argument("--root", default=os.getenv("SESSION_DIR", "results/sessions"))
    args = ap.parse_args()
    if args.list or not args.session:
        for folder in sorted(glob.glob(os.path.join(args.root, "*", ""))):
            data = manifest.read(folder)
            print(f"{os.path.basename(folder.rstrip('/'))}  {data['presence']:8s}  {data.get('note', '')}")
        return
    if not os.path.isdir(args.session):
        sys.exit(f"{args.session}: not a session folder")
    if not args.presence:
        print(manifest.read(args.session))
        return
    data = manifest.mark(args.session, args.presence, note=args.note, by=args.by)
    print(f"{os.path.basename(args.session.rstrip('/'))}: {data['presence']}")


if __name__ == "__main__":
    main()
