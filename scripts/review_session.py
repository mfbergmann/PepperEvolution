#!/usr/bin/env python3
"""
Review a recorded session: a timed transcript with flags, from a SESSION_DIR folder.

    python scripts/review_session.py results/sessions/2026-10-06_141502
    python scripts/review_session.py results/sessions/2026-10-06_141502 --out review.md

Reads turns.jsonl and events.jsonl (src/session.py). For each turn: who said what (and the source), whether the
addressee gate let it through (p), what the command router chose and how soon the action started, the time to
Pepper's first word, tools with their durations, and what Pepper said. Events in between: people arriving and
leaving, greetings, camera events. Flags at the end point at what to look at first. The output holds people's words:
keep it with the session records, out of git.
"""

import argparse
import json
import os
import statistics
import sys
from typing import Any, Dict, List

SLOW_FIRST_WORD = 4.0  # seconds from hearing to Pepper's first word
LONG_REPLY_SENTENCES = 4
LONG_REPLY_CHARS = 350


def load(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def clock(at: str) -> str:
    return at[11:23] if at and len(at) >= 23 else at or "?"


def median(values: List[float]) -> str:
    return f"{statistics.median(values):.2f} s" if values else "-"


def turn_lines(turn: Dict[str, Any]) -> List[str]:
    out = []
    src = turn.get("source", "?")
    lines = [f"{clock(turn.get('at', ''))}  [{src}] {turn.get('text', '')}"]
    gate = turn.get("addressee")
    if gate:
        verdict = "answered" if gate.get("addressed") else "NOT answered (side talk)"
        looking = ", someone looking" if gate.get("someone_looking") else ""
        looking += ", in a conversation" if gate.get("in_conversation") else ""
        lines.append(f"      gate: p={gate.get('p')} {verdict}{looking} ({gate.get('seconds')} s)")
    router = turn.get("router")
    if router and router.get("choice"):
        started = f", started {router.get('started_s')} s after hearing" if router.get("executed") else ""
        what = f" -> {router.get('action')}" if router.get("executed") else " (left to Claude)"
        lines.append(f"      router: {router['choice']} p={router.get('p')}{what}{started}")
        if router.get("result"):
            lines.append(f"      result: {router['result']}")
        if router.get("failed"):
            lines.append(f"      FAILED: {router['failed']}")
    if turn.get("intent"):
        lines.append(f"      intent: {turn['intent']}")
    for tool in turn.get("tools", []):
        ok = "" if tool.get("ok") else "  FAILED"
        lines.append(f"      tool {tool.get('name')}({json.dumps(tool.get('input', {}))}) {tool.get('seconds')} s{ok}")
    spoken = turn.get("spoken") or []
    if spoken:
        first = turn.get("first_word_s")
        lines.append(f"      Pepper ({first} s to first word, {turn.get('duration_s')} s in all): {' '.join(spoken)}")
    elif turn.get("stop_reason") not in ("not_addressed",):
        lines.append(f"      (no speech; {turn.get('stop_reason')})")
    out.extend(lines)
    return out


def event_line(event: Dict[str, Any]) -> str:
    kind = event.get("kind")
    at = clock(event.get("at", ""))
    if kind == "people":
        return f"{at}  · people in view: {event.get('count')}"
    if kind == "greeting":
        return f"{at}  · greeting someone at {event.get('distance')} m"
    if kind == "handshake":
        return f"{at}  · handshake: {'taken' if event.get('taken') else 'not taken'} after {event.get('waited')} s"
    if kind == "camera_event":
        return f"{at}  · camera: {event.get('what')} (p={event.get('p')})"
    if kind in ("touch", "bumper"):
        return f"{at}  · {kind}: {event.get('sensor')}"
    return ""


def review(folder: str) -> str:
    turns = load(os.path.join(folder, "turns.jsonl"))
    events = load(os.path.join(folder, "events.jsonl"))
    timeline = [(t.get("at", ""), "turn", t) for t in turns] + [(e.get("at", ""), "event", e) for e in events]
    timeline.sort(key=lambda item: item[0])
    out = [f"# Session {os.path.basename(os.path.normpath(folder))}", ""]
    last_people = None
    for _, kind, row in timeline:
        if kind == "turn":
            out.extend(turn_lines(row))
        else:
            if row.get("kind") == "people":
                if row.get("count") == last_people:
                    continue  # gaze-only changes: noise in a transcript
                last_people = row.get("count")
            line = event_line(row)
            if line:
                out.append(line)

    voice = [t for t in turns if t.get("source") == "voice"]
    answered = [t for t in voice if t.get("stop_reason") not in ("not_addressed",) and not t.get("intent")]
    first_words = [t["first_word_s"] for t in answered if t.get("first_word_s") is not None]
    routed = [t["router"] for t in turns if (t.get("router") or {}).get("executed")]
    gated_out = [t for t in voice if t.get("stop_reason") == "not_addressed"]
    camera = [e.get("seconds") for e in events if e.get("kind") == "camera" and e.get("seconds") is not None]
    flags = []
    for t in answered:
        if (t.get("first_word_s") or 0) > SLOW_FIRST_WORD:
            flags.append(f"slow first word ({t['first_word_s']} s): {t.get('text')!r}")
        spoken = t.get("spoken") or []
        if len(spoken) >= LONG_REPLY_SENTENCES or sum(len(s) for s in spoken) > LONG_REPLY_CHARS:
            flags.append(f"long reply ({len(spoken)} sentences): {t.get('text')!r}")
    for t in turns:
        for tool in t.get("tools", []):
            if not tool.get("ok"):
                flags.append(f"tool {tool.get('name')} failed: {tool.get('result', '')[:120]}")
        gate = t.get("addressee") or {}
        if (
            gate.get("addressed")
            and not gate.get("someone_looking")
            and not gate.get("in_conversation")
            and (gate.get("p") or 1) < 0.8
        ):
            flags.append(f"answered with nobody looking at Pepper (p={gate.get('p')}): {t.get('text')!r}")
    for t in gated_out:
        flags.append(f"not answered (check it was side talk): {t.get('text')!r} p={t['addressee'].get('p')}")

    out += [
        "",
        "## Summary",
        f"- turns: {len(turns)} ({len(voice)} by voice, {len(gated_out)} not answered as side talk, "
        f"{sum(1 for t in turns if t.get('intent'))} control phrases)",
        f"- time to Pepper's first word (voice, answered): median {median(first_words)}",
        f"- actions started by the router: {len(routed)}, median start {median([r['started_s'] for r in routed])} "
        "after hearing (add the recogniser's end-of-speech silence for 'after you stopped speaking')",
        f"- camera judgements: {len(camera)}, median {median(camera)}; camera events: "
        f"{sum(1 for e in events if e.get('kind') == 'camera_event')}",
        "",
        "## Flags",
    ]
    out += [f"- {f}" for f in flags] or ["- none"]
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="a session folder (SESSION_DIR/<date_time>)")
    parser.add_argument("--out", help="also write the review to this file (inside the session folder if relative)")
    args = parser.parse_args()
    if not os.path.isdir(args.folder):
        sys.exit(f"not a folder: {args.folder}")
    text = review(args.folder)
    print(text)
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(args.folder, args.out)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
