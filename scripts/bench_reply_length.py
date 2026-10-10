#!/usr/bin/env python3
"""
How long are Pepper's spoken replies? Replay recorded conversations through the model with the current system
prompt and with a candidate, and compare (#19).

    python scripts/bench_reply_length.py results/sessions/2026-10-08_185850 [more folders] [--limit 30]

For every recorded voice or typed turn that Pepper answered without using a tool, the model is asked again with
the same conversation so far (what was said and what Pepper said, up to ``HISTORY`` exchanges back) and the same
"Around you" line. Only the first answer is measured, because tools are not run. The prompts are compared on
characters and sentences per reply, and on the share of replies with four sentences or more.

Uses the API (AI_MODEL / ANTHROPIC_API_KEY from .env): about 3,000 input tokens per reply, mostly cached. Session
folders hold people's words: the output stays on this machine.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv  # noqa: E402

from src.ai.models import SYSTEM_PROMPT, AnthropicProvider  # noqa: E402
from src.ai.speech import strip_animation_tags  # noqa: E402
from src.ai.tools import TOOLS  # noqa: E402

HISTORY = 8  # exchanges of context before the replayed turn


def sentences(text: str) -> int:
    parts = [p for p in re.split(r"(?<=[.!?])\s+", strip_animation_tags(text).strip()) if p.strip()]
    return len(parts)


def load(folder: str) -> List[Dict[str, Any]]:
    path = os.path.join(folder, "turns.jsonl")
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return sorted(rows, key=lambda r: r.get("heard") or r.get("received") or r.get("at"))


def cases(folders: List[str]) -> List[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """(messages before, the turn) for every answered voice/typed turn without tools."""
    out = []
    for folder in folders:
        history: List[Dict[str, Any]] = []
        for turn in load(folder):
            spoken = " ".join(turn.get("spoken") or [])
            if not spoken:
                continue  # unanswered (side talk) or silent: not in the model's history either
            if turn.get("source") in ("voice", "user") and not turn.get("tools") and not turn.get("intent"):
                out.append((trim(history), turn))
            history += exchange(turn, spoken)
    return out


def exchange(turn: Dict[str, Any], spoken: str) -> List[Dict[str, Any]]:
    """The turn as the model saw it: the person's words, its tool calls and their results, then what it said."""
    messages: List[Dict[str, Any]] = [{"role": "user", "content": turn["text"]}]
    tools = turn.get("tools") or []
    if tools:
        uses, results = [], []
        for i, tool in enumerate(tools):
            tool_id = f"toolu_replay_{abs(hash((turn.get('at'), i))) % 10**12}"
            uses.append({"type": "tool_use", "id": tool_id, "name": tool["name"], "input": tool.get("input") or {}})
            result = tool.get("result") or json.dumps({"success": bool(tool.get("ok"))})
            results.append({"type": "tool_result", "tool_use_id": tool_id, "content": str(result)[:500]})
        messages += [{"role": "assistant", "content": uses}, {"role": "user", "content": results}]
    messages.append({"role": "assistant", "content": spoken})
    return messages


def trim(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The last HISTORY exchanges, cut at a person's words so tool calls stay paired."""
    starts = [i for i, m in enumerate(history) if m["role"] == "user" and isinstance(m["content"], str)]
    return list(history[starts[-HISTORY] :]) if len(starts) > HISTORY else list(history)


def system_blocks(prompt: str, turn: Dict[str, Any]) -> List[Dict[str, Any]]:
    dynamic = "Current state: battery 93%; posture Stand; motors awake; voice language English."
    if turn.get("around"):
        dynamic += "\n" + turn["around"]
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": dynamic}]


async def ask(provider: AnthropicProvider, prompt: str, history, turn) -> str:
    messages = history + [{"role": "user", "content": turn["text"]}]
    response = await provider.chat(messages, tools=TOOLS, system=system_blocks(prompt, turn))
    return response.text or ""


def summary(name: str, replies: List[str]) -> str:
    chars = [len(strip_animation_tags(r)) for r in replies]
    counts = [sentences(r) for r in replies]
    long = sum(1 for c in counts if c >= 4) / len(counts)
    return (
        f"{name:22s} median {statistics.median(chars):5.0f} chars, mean {statistics.mean(counts):.2f} sentences, "
        f"{long:.0%} with 4 or more"
    )


async def run(folders: List[str], candidate: str, limit: Optional[int]):
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    provider = AnthropicProvider(
        os.environ["ANTHROPIC_API_KEY"], os.getenv("AI_MODEL", "claude-sonnet-5-5"), effort=os.getenv("AI_EFFORT")
    )
    picked = cases(folders)[:limit] if limit else cases(folders)
    recorded = [" ".join(t["spoken"]) for _, t in picked]
    current, revised = [], []
    for history, turn in picked:
        current.append(await ask(provider, SYSTEM_PROMPT, history, turn))
        revised.append(await ask(provider, candidate, history, turn))
    print(f"{len(picked)} turns")
    print(summary("recorded", recorded))
    print(summary("current prompt", current))
    print(summary("candidate prompt", revised))
    for (_, turn), a, b in list(zip(picked, current, revised))[:12]:
        print(f"\n> {turn['text']}\n  now:       {strip_animation_tags(a)}\n  candidate: {strip_animation_tags(b)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folders", nargs="+")
    parser.add_argument("--candidate", help="a file with the candidate system prompt (default: the current one)")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    candidate = open(args.candidate, encoding="utf-8").read() if args.candidate else SYSTEM_PROMPT
    asyncio.run(run(args.folders, candidate, args.limit))


if __name__ == "__main__":
    main()
