#!/usr/bin/env python3
"""
Compare two decision-model servers on every recorded judgement: the upgrade check for Alien3's Ollama.

An Ollama upgrade can force the decision models (Nimble, Clef Flash) to be downloaded again, and the registry's
``latest`` may then be newer weights than the ones benchmarked (2026-11-19: 0.40.16 refused the October downloads,
and the fresh ones had different weights and no built-in system prompt). So a new server, with its models, is started
next to the live one on another port and compared here before anything switches (docs/SETUP_PROFILES.md, "Upgrading
the decision models").

For every recorded open-mic turn with an addressee judgement, and every turn the router judged, both servers get the
same input. The input is rebuilt from the session: the last three things heard, and the words. The check counts how
often the production verdict flips at the recorded thresholds, and how far the probabilities moved. It prints counts
and session/line references only, never words: session records hold people's speech. The recorded probabilities
came from whatever models were live then, so the old server is asked again rather than trusted from the record.

    .venv/bin/python scripts/compare_decide.py --old http://alien3:11435 --new http://alien3:11438 [SESSION_DIR...]

Exit status 1 when the new server flips more verdicts than ``--max-flips`` allows (default: 2% of the gated turns).
"""

import argparse
import asyncio
import glob
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from src.decide import DecisionClient  # noqa: E402
from src.decide.addressee import CONTEXT_UTTERANCES, judge_addressee  # noqa: E402
from src.decide.router import choose, plan  # noqa: E402
from src.orchestra.route import GateSituation, Thresholds, gate_verdicts  # noqa: E402

ROUTER_THRESHOLD = 0.7


def cases(sessions: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The gated turns and the routed turns, with the inputs rebuilt as AIManager built them."""
    gated, routed = [], []
    for session in sessions:
        path = os.path.join(session, "turns.jsonl")
        if not os.path.exists(path):
            continue
        name = os.path.basename(session.rstrip("/"))
        heard: List[str] = []
        for line_no, line in enumerate(open(path, encoding="utf-8"), 1):
            t = json.loads(line)
            if t.get("source") not in ("user", "voice"):
                continue
            text = t.get("text", "")
            where = f"{name}:{line_no}"
            a = t.get("addressee")
            if a and a.get("p") is not None:
                situation = GateSituation(
                    bool(a.get("someone_looking")),
                    bool(a.get("in_conversation")),
                    bool(a.get("after_question")),
                    bool(a.get("alone")),
                )
                gated.append({"where": where, "before": heard[-CONTEXT_UTTERANCES:], "text": text, "s": situation})
            if (t.get("router") or {}).get("choice") is not None:
                routed.append({"where": where, "previous": heard[-1] if heard else "", "text": text})
            heard.append(text)
    return gated, routed


def parse_models(text: str) -> Dict[str, str]:
    """``DECIDE_MODELS``' format: "nimble=nimble-2k,clef-flash=clef-flash-4k" (as main.py reads it)."""
    pairs = (item.split("=", 1) for item in text.split(",") if "=" in item)
    return {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}


async def ask_all(url: str, gated, routed, timeout: float, models: Dict[str, str], servers: Dict[str, str]):
    client = DecisionClient(url, timeout=timeout, models=models, servers=servers)
    await client.warm(["nimble"])
    gate = [await judge_addressee(client, g["before"], g["text"]) for g in gated]
    route = [await choose(client, r["previous"], r["text"]) for r in routed]
    failures = client.failures
    await client.close()
    return gate, route, failures


def started(chosen, text: str):
    if not chosen or chosen[1] < ROUTER_THRESHOLD:
        return None
    routed = plan(chosen[0], chosen[1], text)
    return routed.action if routed is not None else None


async def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument(
        "--models",
        default=os.getenv("DECIDE_MODELS", ""),
        help="served names, as DECIDE_MODELS (default: from the environment or .env); used on both servers",
    )
    ap.add_argument("--old-servers", default="", help="as DECIDE_SERVERS, for the old side")
    ap.add_argument("--new-servers", default="", help="as DECIDE_SERVERS, for the new side")
    ap.add_argument("--max-flips", type=float, default=0.02, help="allowed share of gate verdict flips")
    args = ap.parse_args()
    models = parse_models(args.models)
    root = os.getenv("SESSION_DIR", "results/sessions")
    sessions = args.sessions or sorted(glob.glob(os.path.join(root, "*", "")))
    gated, routed = cases(sessions)
    print(f"{len(gated)} gated turns and {len(routed)} routed turns from {len(sessions)} sessions")
    print(f"models: {models or 'as asked (nimble, clef-flash)'}")
    old = await ask_all(args.old, gated, routed, args.timeout, models, parse_models(args.old_servers))
    new = await ask_all(args.new, gated, routed, args.timeout, models, parse_models(args.new_servers))
    thresholds = Thresholds()
    flips, moves = [], []
    for g, p_old, p_new in zip(gated, old[0], new[0]):
        if p_old is None or p_new is None:
            continue
        moves.append(abs(p_new - p_old))
        v_old = gate_verdicts(p_old, g["s"], thresholds).usual
        v_new = gate_verdicts(p_new, g["s"], thresholds).usual
        if v_old != v_new:
            flips.append((g["where"], round(p_old, 2), round(p_new, 2), "now answered" if v_new else "now ignored"))
    route_changes = []
    for r, c_old, c_new in zip(routed, old[1], new[1]):
        a_old, a_new = started(c_old, r["text"]), started(c_new, r["text"])
        if a_old != a_new:
            route_changes.append((r["where"], a_old or "(mind)", a_new or "(mind)"))
    print(f"failures: old {old[2]}, new {new[2]}")
    if moves:
        print(
            f"addressee: {len(flips)} of {len(moves)} verdicts flip; probability moved by median "
            f"{statistics.median(moves):.3f}, 90th percentile {sorted(moves)[int(0.9 * (len(moves) - 1))]:.3f}"
        )
    for f in flips:
        print("   flip", *f)
    print(f"router: {len(route_changes)} of {len(routed)} turns start a different action at once")
    for c in route_changes:
        print("   route", *c)
    allowed = args.max_flips * max(1, len(moves))
    sys.exit(1 if len(flips) > allowed or new[2] else 0)


if __name__ == "__main__":
    asyncio.run(main())
