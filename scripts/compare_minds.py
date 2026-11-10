#!/usr/bin/env python3
"""
Replay recorded turns through two minds on identical context, and compare (issue #4, docs/DATA_FLOWS.md).

For every turn in which the mind ran (stop reason ``end_turn``) of the given sessions, both providers get the same
system prompt (today's, with the memory prompt where the session had memory), a state block rebuilt from the record
(its date and time and its "Around you" line), the earlier turns of that run as plain text pairs (what was heard,
what Pepper said, as recorded), and the same tool list. Tools are captured, never run: this compares the first
response of each turn only. A fresh baseline pass is made rather than using the recorded replies, because those had
photos and live state the replay cannot rebuild; the recorded reply is kept as a reference.

Only sessions where the tester was alone with Pepper may be replayed to a hosted service (docs/DATA_FLOWS.md). The
output holds transcripts, so it goes to the git-ignored results/ folder only.

    .venv/bin/python scripts/compare_minds.py --out results/system-one/minds/replay.jsonl SESSION_DIR...
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from src.ai.models import (  # noqa: E402
    CLOSING,
    MEMORY_PROMPT,
    SYSTEM_PROMPT,
    AnthropicProvider,
    OllamaProvider,
)
from src.ai.speech import strip_animation_tags  # noqa: E402
from src.ai.tools import MEMORY_TOOLS, TOOLS  # noqa: E402

HISTORY_TURNS = 20  # as AIManager
EXCLUDED = ("2026-10-22_164938", "2026-11-05_160237")  # someone else was there: never to a hosted service


def state_block(rec: Dict[str, Any]) -> str:
    at = datetime.fromisoformat(rec["at"])
    text = (
        "Current state: battery 80%; posture Stand; motors awake; autonomous life solitary; voice language English. "
        f"Local date and time: {at.strftime('%A %d %B %Y, %H:%M')}."
    )
    if rec.get("around"):
        text += f"\n{rec['around']}"
    return text


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        import ast

        return ast.literal_eval(value)


async def ask(provider: Any, system: list, history: list, text: str, tools: list) -> Dict[str, Any]:
    started = time.monotonic()
    first: List[float] = []

    async def on_text(_piece: str):
        if not first:
            first.append(time.monotonic() - started)

    response = await provider.chat(
        history + [{"role": "user", "content": text}], tools=tools, system=system, on_text=on_text
    )
    return {
        "text": strip_animation_tags(response.text or ""),
        "tools": [{"name": c.name, "input": c.input} for c in response.tool_calls],
        "first_text_s": round(first[0], 3) if first else None,
        "total_s": round(time.monotonic() - started, 3),
        "stop": response.stop_reason,
        "error": (response.usage or {}).get("error") if response.is_error else None,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--candidate", default="DeepSeek-V4-Flash-0731")
    ap.add_argument("--candidate-url", default="https://brine.sharcnet.ca")
    ap.add_argument("--candidate-key-file", default="~/.config/brine/key")
    ap.add_argument("--candidate-rule", default="", help="a line added to the candidate's system prompt only")
    ap.add_argument("--candidate-only", action="store_true", help="skip the baseline (it is then left empty)")
    args = ap.parse_args()
    load_dotenv()
    for s in args.sessions:
        if any(x in s for x in EXCLUDED):
            sys.exit(f"{s}: someone other than the tester was there; not replayed to a hosted service")
    baseline = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], args.baseline, effort=args.effort)
    with open(os.path.expanduser(args.candidate_key_file)) as f:
        candidate = OllamaProvider(args.candidate_url, args.candidate, api_key=f.read().strip())
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    done = 0
    with open(args.out, "w") as out:
        os.chmod(args.out, 0o600)
        for session in args.sessions:
            memory = "Long-term memory:" in open(os.path.join(session, "host.log"), errors="replace").read()
            prompt = SYSTEM_PROMPT.replace(CLOSING, (MEMORY_PROMPT if memory else "") + CLOSING)
            tools = TOOLS + (MEMORY_TOOLS if memory else [])
            history: list = []
            for line in open(os.path.join(session, "turns.jsonl")):
                rec = json.loads(line)
                spoken = " ".join(as_list(rec.get("spoken")))
                if rec.get("stop_reason") == "end_turn" and rec.get("text"):
                    system = [
                        {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": state_block(rec)},
                    ]
                    trimmed = history[-2 * HISTORY_TURNS :]
                    candidate_system = system
                    if args.candidate_rule:
                        candidate_system = [
                            {**system[0], "text": prompt.replace(CLOSING, "\n\n" + args.candidate_rule + CLOSING)},
                            system[1],
                        ]
                    if args.candidate_only:
                        a = {"text": "", "tools": [], "first_text_s": None, "total_s": 0.0, "stop": None, "error": None}
                        b = await ask(candidate, candidate_system, trimmed, rec["text"], tools)
                    else:
                        a, b = await asyncio.gather(
                            ask(baseline, system, trimmed, rec["text"], tools),
                            ask(candidate, candidate_system, trimmed, rec["text"], tools),
                        )
                    out.write(
                        json.dumps(
                            {
                                "session": os.path.basename(session.rstrip("/")),
                                "at": rec["at"],
                                "source": rec.get("source"),
                                "heard": rec["text"],
                                "around": rec.get("around"),
                                "history_turns": len(trimmed) // 2,
                                "recorded": {
                                    "text": spoken,
                                    "tools": [t.get("name") for t in as_list(rec.get("tools"))],
                                    "first_word_s": rec.get("first_word_s"),
                                },
                                "baseline": a,
                                "candidate": b,
                            }
                        )
                        + "\n"
                    )
                    out.flush()
                    done += 1
                    print(
                        f"{done:3d} {os.path.basename(session)} sonnet {a['first_text_s']}s "
                        f"deepseek {b['first_text_s']}s {'ERR ' + str(b['error'])[:80] if b['error'] else ''}",
                        flush=True,
                    )
                if rec.get("stop_reason") in ("end_turn", "intent") and rec.get("text"):
                    history += [
                        {"role": "user", "content": rec["text"]},
                        {"role": "assistant", "content": spoken or "(no reply)"},
                    ]


if __name__ == "__main__":
    asyncio.run(main())
