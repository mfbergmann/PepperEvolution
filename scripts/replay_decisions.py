#!/usr/bin/env python3
"""
Recompute recorded decisions with src/orchestra/route.py and compare them with what was recorded (docs/ORCHESTRATOR.md,
phase 1b). This is the check that moving the branching out of AIManager changed nothing.

For every turn in the given sessions (default: all under SESSION_DIR):

- **gate**: the addressee verdict from the recorded probability and situation flags. Flags a record does not have
  were not yet computed when it was made, and count as false, which is how that version behaved.
- **path**: the path from the recorded verdict, router choice and outcome, against the path the record shows
  (``path`` since the ledger; for older records, read from the stop reason and the router's ``executed``).
  Whether the body was free is not recorded; a routed action that did not run is taken as one the body was not free
  for, so this checks the mapping, not the body check.

Prints counts and, for mismatches, the session and line only (no words: the records hold people's speech).

    .venv/bin/python scripts/replay_decisions.py [SESSION_DIR...] [--thresholds 0.6 0.4 0.2] [--router 0.7]
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.decide.router import plan  # noqa: E402
from src.orchestra import route  # noqa: E402
from src.orchestra.route import GateSituation, Thresholds, accept_route, decide_turn, gate_verdicts  # noqa: E402

# Records made before a later fix: the decision differs because the code did, not because of the extraction.
EXPLAINED = {
    ("2026-10-08_182631", 4): "a routed look_at_me still ended the turn (fixed the same day in 1263bee)",
    ("2026-10-20_183011", 5): "an intent phrase the list matched before bf75e97 (2026-10-20, 19:27)",
}


def recorded_path(t: dict) -> str:
    if t.get("path"):
        return t["path"]
    router = t.get("router") or {}
    if "intent" in t:
        return route.ROUTED_INTENT if router.get("choice") in ("stop", "be_quiet") and not _exact(t) else route.INTENT
    if t.get("stop_reason") == "not_addressed":
        return route.NOT_ADDRESSED
    if t.get("stop_reason") == "cancelled":
        return route.CANCELLED
    if router.get("executed"):
        return route.LOOK_THEN_MIND if router.get("choice") == "look_at_me" else route.ROUTED_THEN_MIND
    return route.MIND


def _exact(t: dict) -> bool:
    from src.ai.intents import match_intent

    return match_intent(t.get("text", "")) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--thresholds", nargs=3, type=float, default=(0.6, 0.4, 0.2))
    ap.add_argument("--router", type=float, default=0.7)
    args = ap.parse_args()
    root = os.getenv("SESSION_DIR", "results/sessions")
    sessions = args.sessions or sorted(glob.glob(os.path.join(root, "*", "")))
    thresholds = Thresholds(*args.thresholds)
    gate = {"checked": 0, "agree": 0}
    paths = {"checked": 0, "agree": 0}
    mismatches, explained = [], []
    for session in sessions:
        name = os.path.basename(session.rstrip("/"))
        path = os.path.join(session, "turns.jsonl")
        if not os.path.exists(path):
            continue
        for line_no, line in enumerate(open(path, encoding="utf-8"), 1):
            t = json.loads(line)
            a = t.get("addressee")
            meant = True
            svd = False
            if a and "p" in a:
                situation = GateSituation(
                    bool(a.get("someone_looking")),
                    bool(a.get("in_conversation")),
                    bool(a.get("after_question")),
                    bool(a.get("alone")),
                )
                meant = gate_verdicts(a["p"], situation, thresholds).final(bool(a.get("second_voice")))
                svd = bool(a.get("second_voice_decided"))
                gate["checked"] += 1
                recorded = a["addressed"]
                router_choice = (t.get("router") or {}).get("choice")
                overridden = recorded and not meant and svd and router_choice in ("stop", "be_quiet")
                if meant == recorded or overridden:
                    gate["agree"] += 1
                else:
                    mismatches.append((name, line_no, "gate", f"p={a['p']} recorded={recorded} now={meant}"))
            if "intent" in t and _exact(t):
                continue  # control phrases act before any decision
            if t.get("source") not in ("user", "voice") or t.get("stop_reason") == "cancelled":
                continue
            r = t.get("router") or {}
            routed, routed_intent = False, None
            if r.get("choice") is not None and r.get("p") is not None:
                planned = plan(r["choice"], r["p"], t.get("text", ""))
                is_intent = planned is not None and planned.intent is not None
                body_free = is_intent or bool(r.get("executed"))
                routed = accept_route(r["p"], args.router, planned is not None, is_intent, body_free)
                routed_intent = planned.intent if routed else None
            now = decide_turn(meant if a else True, svd, routed_intent, routed)
            was = recorded_path(t)
            paths["checked"] += 1
            if now == was:
                paths["agree"] += 1
            elif (name, line_no) in EXPLAINED:
                explained.append((name, line_no, f"recorded={was} now={now}: {EXPLAINED[(name, line_no)]}"))
            else:
                mismatches.append((name, line_no, "path", f"recorded={was} now={now}"))
    print(f"gate verdicts: {gate['agree']} of {gate['checked']} agree")
    print(f"paths:         {paths['agree']} of {paths['checked']} agree, {len(explained)} explained by later fixes")
    for e in explained:
        print("  explained", *e)
    for m in mismatches:
        print("  mismatch", *m)
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
