#!/usr/bin/env python3
"""
Score a compare_minds.py replay: mechanical checks per model, and a blind A/B sheet for the tester.

    .venv/bin/python scripts/score_minds.py results/system-one/minds/replay.jsonl [--blind 25]

Writes the blind sheet and its key next to the replay (git-ignored). Prints aggregates only.
"""

import argparse
import json
import os
import random
import re
import statistics
from typing import Any, Dict, List

BODY = {"turn", "move_forward", "move_head", "point_at", "look_at", "move_to", "go_to_place", "explore"}
# words that announce an action: said without the matching tool call, the action never happens
CLAIMS = re.compile(
    r"\b(turning|let me turn|i'?ll turn|there we go|i'?m facing|taking a (look|photo|picture)|let me (look|check|see)"
    r"|looking (now|over)|i turned|moving (forward|closer)|nodding)\b",
    re.I,
)


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()])


def summary(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    first = [r[key]["first_text_s"] for r in rows if r[key]["first_text_s"] is not None]
    total = [r[key]["total_s"] for r in rows]
    out = {
        "turns": len(rows),
        "errors": sum(bool(r[key]["error"]) for r in rows),
        "first_text_s_median": round(statistics.median(first), 2) if first else None,
        "first_text_s_p90": round(sorted(first)[int(0.9 * (len(first) - 1))], 2) if first else None,
        "total_s_median": round(statistics.median(total), 2),
        "words_median": statistics.median(len(r[key]["text"].split()) for r in rows),
        "four_plus_sentences": sum(sentences(r[key]["text"]) >= 4 for r in rows),
        "empty_no_tool": sum(not r[key]["text"].strip() and not r[key]["tools"] for r in rows),
        "used_a_tool": sum(bool(r[key]["tools"]) for r in rows),
    }
    agree = moves_unasked = missed = claims = photo_need = photo_took = 0
    for r in rows:
        mine = [t["name"] for t in r[key]["tools"]]
        live = r["recorded"]["tools"]
        agree += (mine[:1] == live[:1]) or (not mine and not live)
        moves_unasked += bool(set(mine) & BODY) and not (set(live) & BODY)
        missed += bool(set(live) & (BODY | {"take_photo"})) and not mine
        claims += bool(CLAIMS.search(r[key]["text"])) and not mine
        if "take_photo" in live[:1]:
            photo_need += 1
            photo_took += "take_photo" in mine
    out.update(
        {
            "first_tool_agrees_with_live": agree,
            "body_move_live_did_not_make": moves_unasked,
            "no_tool_where_live_acted": missed,
            "announces_action_without_tool": claims,
            "photo_turns_live": photo_need,
            "photo_turns_took_photo": photo_took,
        }
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("replay")
    ap.add_argument("--blind", type=int, default=25)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    rows = [json.loads(line) for line in open(args.replay)]
    rows = [r for r in rows if not r["baseline"]["error"] and not r["candidate"]["error"]] or rows
    print(f"{len(rows)} turns from {len({r['session'] for r in rows})} host runs")
    table = {k: summary(rows, k) for k in ("baseline", "candidate")}
    for metric in table["baseline"]:
        print(f"  {metric:34s} sonnet {table['baseline'][metric]!s:>8}   deepseek {table['candidate'][metric]!s:>8}")

    # Blind sheet: turns with words from both, A and B shuffled, the key apart.
    rng = random.Random(args.seed)
    pool = [r for r in rows if r["baseline"]["text"].strip() and r["candidate"]["text"].strip()]
    picked = sorted(rng.sample(pool, min(args.blind, len(pool))), key=lambda r: r["at"])
    base = os.path.splitext(args.replay)[0]
    key, lines = [], [
        "# Blind comparison: which reply is better?",
        "",
        "For each turn, write A, B or = after **Better:**. Tool calls are shown in brackets: they"
        " would have happened. Replies were made on the same context; neither saw photos.",
        "",
    ]
    for i, r in enumerate(picked, 1):
        a_is = rng.choice(["baseline", "candidate"])
        b_is = "candidate" if a_is == "baseline" else "baseline"
        key.append({"n": i, "A": a_is, "B": b_is, "at": r["at"]})

        def shown(k):
            tools = "".join(f" [{t['name']} {json.dumps(t['input'])}]" for t in r[k]["tools"])
            return (r[k]["text"].strip() or "(no words)") + tools

        lines += [
            f"## {i}. {r['at'][:16]}",
            "",
            f"*{r.get('around') or ''}*",
            "",
            f"**Heard:** {r['heard']}",
            "",
            f"**A:** {shown(a_is)}",
            "",
            f"**B:** {shown(b_is)}",
            "",
            "**Better:** ",
            "",
        ]
    for path, text in ((base + "-blind.md", "\n".join(lines)), (base + "-blind-key.json", json.dumps(key, indent=1))):
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o600)
    with open(base + "-scores.json", "w") as f:
        json.dump(table, f, indent=1)
    print(f"blind sheet: {base}-blind.md ({len(picked)} turns); key kept apart in {base}-blind-key.json")


if __name__ == "__main__":
    main()
