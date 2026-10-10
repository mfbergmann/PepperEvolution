#!/usr/bin/env python3
"""
Replay a recorded session through working memory (docs/MEMORY.md, "Session records and replay").

    python scripts/replay_world.py results/sessions/2026-10-08_185850

Feeds the session's inputs back through ``WorldModel.observe()`` in order, with the session's own clock:
- people events from ``events.jsonl``;
- Pepper's moves, from the ``observation`` lines in ``events.jsonl`` (since 0.6), or else parsed from ``host.log``
  (tool calls and routed actions);
- poses, when recorded.

Then prints, for every spoken turn, the "Around you" line that was recorded and the one the replayed world model
gives. For every time someone came back into view after Pepper turned, it prints where memory predicted them and
where the detector saw them. Session folders hold people's words: the output stays on this machine.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.world import WorldModel  # noqa: E402
from src.ai.reflection import TICK, consider  # noqa: E402
from src.world.observations import HEARD, MIND, MOTION, SAID  # noqa: E402

LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \|.*?- (.*)$")
TOOL_CALL = re.compile(r"Tool call: (turn|move_forward)\((\{.*\})\)")
TOOL_RESULT = re.compile(r"Tool (turn|move_forward) -> (\{.*\})")
ROUTED = re.compile(r"Routed at once: turning (left|right) (\d+) degrees|Routed at once: turning around")


def wall(text: str) -> float:
    return datetime.fromisoformat(text.replace(" ", "T")).timestamp()


def turn_seconds(degrees: float) -> float:
    """How long a turn in place takes on the robot (2026-10-08: 70° 3.0 s, 90° 3.4 s, 160° 4.6 s)."""
    return 2.0 + 0.016 * abs(degrees)


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def moves_from_log(path: str) -> List[Tuple[float, str, Dict[str, Any]]]:
    """(time, kind, data) motion observations reconstructed from host.log, for sessions before 0.6."""
    if not os.path.exists(path):
        return []
    out: List[Tuple[float, str, Dict[str, Any]]] = []
    pending: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LOG_LINE.match(line.strip())
            if not m:
                continue
            at, text = wall(m.group(1)), m.group(2)
            call = TOOL_CALL.search(text)
            if call:
                name, args = call.group(1), json.loads(call.group(2))
                kind = "turn" if name == "turn" else "drive"
                data = (
                    {"angle": float(args.get("angle", 0))}
                    if kind == "turn"
                    else {"distance": float(args.get("distance", 0.5))}
                )
                pending[kind] = (at, data)
                out.append((at, f"{kind}_started", data))
                continue
            result = TOOL_RESULT.search(text)
            if result:
                kind = "turn" if result.group(1) == "turn" else "drive"
                started = pending.pop(kind, None)
                if started is None:
                    continue
                body = json.loads(result.group(2))
                data = dict(started[1])
                data["completed"] = bool(body.get("success")) and body.get("completed", True) is not False
                if not body.get("success"):
                    data["refused"] = True
                if "already_done" in body:
                    continue  # the routed action already moved; see below
                out.append((at, kind, data))
                continue
            routed = ROUTED.search(text)
            if routed:
                if routed.group(1):
                    degrees = float(routed.group(2)) * (1 if routed.group(1) == "left" else -1)
                else:
                    degrees = 180.0
                out.append((at, "turn_started", {"angle": degrees}))
                out.append((at + turn_seconds(degrees), "turn", {"angle": degrees, "completed": True}))
    return out


def conversation(turns: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Tuple[float, str, Dict[str, Any]]]:
    """What was heard and said, and the greetings, as timeline inputs: Pepper's sentences are spread between its
    first word and the end of the turn (their own times are not recorded)."""
    out: List[Tuple[float, str, Dict[str, Any]]] = []
    for t in turns:
        began = wall(t.get("heard") or t.get("received") or t["at"])
        if t.get("source") in ("voice", "user"):
            addressed = (t.get("addressee") or {}).get("addressed", True)
            out.append((began, "heard", {"text": t.get("text", ""), "addressed": addressed, "intent": t.get("intent")}))
        spoken = t.get("spoken") or []
        if spoken:
            first = began + (t.get("first_word_s") or 0.0)
            end = wall(t["at"])
            for i, sentence in enumerate(spoken):
                at = first + (end - first) * (i / max(1, len(spoken) - 1)) if len(spoken) > 1 else end
                out.append((at, "said", {"text": sentence}))
    for e in events:
        if e.get("kind") == "greeting":
            out.append((wall(e["at"]), "greeting", {}))
    return out


def replay(folder: str, initiative: bool = False) -> str:
    events = load_jsonl(os.path.join(folder, "events.jsonl"))
    turns = load_jsonl(os.path.join(folder, "turns.jsonl"))
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
    checkpoints = [
        (wall(t.get("heard") or t.get("received") or t["at"]), "turn", t) for t in turns if t.get("source") == "voice"
    ]
    if initiative:
        inputs += conversation(turns, events)
        checkpoints = []
        start, end = (min(i[0] for i in inputs), max(i[0] for i in inputs)) if inputs else (0.0, 0.0)
        checkpoints = [(start + k * TICK, "tick", {}) for k in range(int((end - start) / TICK) + 2)]
    timeline = sorted(inputs + checkpoints, key=lambda item: item[0])
    done: set = set()
    fired = 0

    clock = [timeline[0][0] if timeline else 0.0]
    world = WorldModel(clock=lambda: clock[0])
    out = [f"# Replay of {os.path.basename(os.path.normpath(folder))}", ""]
    empty_since: Optional[float] = None
    for at, kind, row in timeline:
        clock[0] = at
        stamp = datetime.fromtimestamp(at).strftime("%H:%M:%S")
        if kind == "people":
            predicted = None
            if row.get("count") and world.count == 0:
                remembered = world.tracker.remembered(at)
                if remembered and remembered[0].bearing(world.pose) is not None:
                    predicted = remembered[0].bearing(world.pose)
            world.handle_people(row.get("count"), row.get("people") or [], pose=row.get("pose"))
            seen = [p.get("yaw") for p in row.get("people") or [] if p.get("yaw") is not None]
            if predicted is not None and seen:
                out.append(
                    f"{stamp}  back in view: memory said {predicted:+.1f}°, the detector saw {seen[0]:+.1f}° "
                    f"(off by {abs(predicted - seen[0]):.1f}°, after {at - (empty_since or at):.0f} s out of view)"
                )
            empty_since = at if row.get("count") == 0 else empty_since
        elif kind == "logged_move":
            data = {k: v for k, v in row.items() if k != "kind"}
            world.note(MOTION, row["kind"], **data)
            if not row["kind"].endswith("_started"):
                out.append(f"{stamp}  {row['kind']} {data.get('angle', data.get('distance'))}")
        elif kind in ("pose", "motion"):
            data = {k: v for k, v in row.items() if k not in ("at", "kind", "source", "obs")}
            world.note(kind, row.get("obs", kind), **data)
        elif kind == "heard":
            world.note(HEARD, "heard", via="voice", **row)
        elif kind == "said":
            world.note(SAID, "said", **row)
        elif kind == "greeting":
            world.note(MIND, "greeting")
        elif kind == "tick":
            chosen = consider(world, at, done)
            if chosen is not None:
                done.add(chosen.key)
                fired += 1
                world.timeline.add("initiative", at, rule=chosen.rule)
                out.append(f"{stamp}  INITIATIVE ({chosen.rule}): {chosen.prompt}")
                out.append(f"          {world.summary()}")
        elif kind == "turn":
            out.append(f"{stamp}  [voice] {row.get('text', '')}")
            if row.get("around"):
                out.append(f"          recorded: {row['around']}")
            out.append(f"          replayed: {world.summary()}")
    if initiative:
        minutes = (timeline[-1][0] - timeline[0][0]) / 60 if timeline else 0
        out.append(f"\n{fired} initiative(s) in {minutes:.0f} minutes")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--initiative", action="store_true", help="when would Pepper have acted unprompted?")
    args = parser.parse_args()
    if not os.path.isdir(args.folder):
        sys.exit(f"not a folder: {args.folder}")
    print(replay(args.folder, initiative=args.initiative))


if __name__ == "__main__":
    main()
