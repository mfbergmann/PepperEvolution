#!/usr/bin/env python3
"""
Replay recorded sessions through a WorldModel with expectations on, and report how often each expectation was met.

    python scripts/replay_expectations.py results/sessions/2026-10-08_185850

Feeds the session's inputs back exactly as replay_world.py does, in time order with the session's own clock:
- people events and the motion/pose observation lines from events.jsonl (plus logged moves from host.log when no
  motion lines were recorded), then advances the clock past the end so every open expectation is judged.

Session folders hold people's words, so this prints nothing they said: only times, counts and reasons, and the output
stays on this machine.
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.world import WorldModel  # noqa: E402
from src.world.observations import EXPECT, MOTION  # noqa: E402
from scripts.replay_world import load_jsonl, moves_from_log, wall  # noqa: E402

STATS = Dict[str, Dict[str, int]]


def _resolve(name: str, base: str) -> str:
    return name if os.path.isdir(name) else os.path.join(base, name)


def replay(folder: str) -> Tuple[str, STATS]:
    events = load_jsonl(os.path.join(folder, "events.jsonl"))
    inputs: List[Tuple[float, str, Dict[str, Any]]] = []
    recorded_moves = False
    for e in events:
        if e.get("kind") == "people":
            inputs.append((wall(e["at"]), "people", e))
        elif e.get("kind") == "observation" and e.get("source") in ("pose", "motion"):
            recorded_moves = recorded_moves or e.get("source") == "motion"
            inputs.append((wall(e["at"]), e["source"], e))
    if not recorded_moves:
        inputs += [(t, "logged_move", {"kind": k, **d}) for t, k, d in moves_from_log(os.path.join(folder, "host.log"))]
    inputs.sort(key=lambda item: item[0])

    clock = [inputs[0][0] if inputs else 0.0]
    world = WorldModel(clock=lambda: clock[0], expectations=True)
    surprises: List[Tuple[float, str]] = []

    def collect(obs):
        if obs.source == EXPECT and obs.kind == "surprise":
            surprises.append((obs.at, obs.data["reason"]))

    world.add_sink(collect)
    last_at = clock[0]
    for at, kind, row in inputs:
        clock[0] = at
        if kind == "people":
            world.handle_people(row.get("count"), row.get("people") or [], pose=row.get("pose"))
        elif kind == "logged_move":
            data = {k: v for k, v in row.items() if k != "kind"}
            world.note(MOTION, row["kind"], **data)
        elif kind in ("pose", "motion"):
            data = {k: v for k, v in row.items() if k not in ("at", "kind", "source", "obs")}
            world.note(kind, row.get("obs", kind), **data)
        last_at = at
    clock[0] = last_at + 10
    world.watch.tick(clock[0])

    stats = world.watch.stats
    out = [f"# {os.path.basename(os.path.normpath(folder))}"]
    for kind in ("face", "closer"):
        s = stats[kind]
        out.append(
            f"  {kind}: made {s['made']}, met {s['met']}, surprised {s['surprised']}, cancelled {s['cancelled']}"
        )
    for at, reason in surprises:
        stamp = datetime.fromtimestamp(at).strftime("%H:%M:%S")
        out.append(f"  {stamp}  surprise: {reason}")
    return "\n".join(out) + "\n", stats


def main(folders: Optional[List[str]] = None):
    base = os.getenv("SESSION_DIR", "results/sessions")
    paths = folders or sorted(p for p in os.listdir(base) if os.path.isdir(os.path.join(base, p)))
    totals: STATS = {k: {"made": 0, "met": 0, "surprised": 0, "cancelled": 0} for k in ("face", "closer")}
    blocks: List[str] = []
    for name in paths:
        folder = _resolve(name, base)
        if not os.path.isdir(folder):
            continue
        text, stats = replay(folder)
        blocks.append(text)
        for kind in totals:
            for key in totals[kind]:
                totals[kind][key] += stats[kind][key]
    print("\n".join(blocks))
    print("totals:")
    for kind in ("face", "closer"):
        t = totals[kind]
        print(f"  {kind}: made {t['made']}, met {t['met']}, surprised {t['surprised']}, cancelled {t['cancelled']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "folders", nargs="*", help="one or more session folders (default: every folder under SESSION_DIR)"
    )
    args = parser.parse_args()
    main(args.folders or None)
