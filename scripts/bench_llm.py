#!/usr/bin/env python3
"""
Raw model latency with Pepper's real system prompt and tools, no robot involved.

Times each prompt from request to first streamed text, to the first full sentence
(what Pepper would start saying), and to the end, and records which tool (if any)
the model chose. Claude models go through the Anthropic API; anything else goes to
an OpenAI-compatible server such as Ollama.

    python scripts/bench_llm.py claude-sonnet-5:medium
    python scripts/bench_llm.py --ollama http://alien3:11434 llama3.1:8b qwen3:8b
    python scripts/bench_llm.py --repeat 3 --out results/llm/bench.json claude-sonnet-5:medium llama3.1:8b

The first request to an Ollama model includes loading it into GPU memory; it is
run once as a warm-up and not counted.
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ai.models import SYSTEM_PROMPT  # noqa: E402
from src.ai.tools import TOOLS  # noqa: E402

STATE = (
    "Current state: standing, awake, battery 80 %. Date: Tuesday 29 September 2026, 14:00. "
    "Around you: one person, about 1.2 m away, looking at you, here for about 2 minutes."
)

PROMPTS = [
    ("greeting", "Hello Pepper, how are you today?"),
    ("look_left", "Look to your left and tell me what's there."),
    ("photo", "What can you see in front of you?"),
    ("gesture", "Can you wave at me and say goodbye in a fun way?"),
    ("factual", "How far away is the moon, roughly?"),
    ("chit_chat", "What's your favourite thing about being a robot?"),
    (
        "arrival",
        "[Sensor event] Someone just walked up to you, about 1.2 m away and looking at you. "
        "Greet them in one short, friendly sentence, or stay quiet if a greeting doesn't fit.",
    ),
]

SENTENCE_END = re.compile(r"[.!?](\s|$)")


class Timer:
    def __init__(self):
        self.t0 = time.monotonic()
        self.first_text: Optional[float] = None
        self.first_sentence: Optional[float] = None
        self.text = ""

    def feed(self, fragment: str):
        now = time.monotonic() - self.t0
        if fragment and self.first_text is None:
            self.first_text = now
        self.text += fragment
        if self.first_sentence is None and SENTENCE_END.search(self.text):
            self.first_sentence = now


async def run_claude(model: str, effort: Optional[str], prompt: str) -> Dict[str, Any]:
    import os

    from dotenv import load_dotenv

    from src.ai.models import AnthropicProvider

    load_dotenv(ROOT / ".env")
    provider = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], model, effort=effort, max_tokens=4000)
    timer = Timer()

    async def on_text(fragment: str):
        timer.feed(fragment)

    system = [{"type": "text", "text": SYSTEM_PROMPT}, {"type": "text", "text": STATE}]
    resp = await provider.chat([{"role": "user", "content": prompt}], tools=TOOLS, system=system, on_text=on_text)
    total = time.monotonic() - timer.t0
    return {
        "first_text": timer.first_text,
        "first_sentence": timer.first_sentence,
        "total": total,
        "text": resp.text,
        "tools": [tc.name for tc in resp.tool_calls],
        "error": resp.usage.get("error") if resp.stop_reason == "error" else None,
    }


def openai_tools() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in TOOLS
    ]


async def run_openai(base_url: str, model: str, prompt: str, think: Optional[bool]) -> Dict[str, Any]:
    import openai

    client = openai.AsyncOpenAI(base_url=base_url.rstrip("/") + "/v1", api_key="ollama")
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + STATE}, {"role": "user", "content": prompt}]
    extra: Dict[str, Any] = {}
    if think is not None:
        extra["reasoning_effort"] = "none" if not think else "medium"
    timer = Timer()
    tools: Dict[int, Dict[str, str]] = {}
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, tools=openai_tools(), stream=True, max_tokens=1000, **extra
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                timer.feed(delta.content)
            for tc in delta.tool_calls or []:
                entry = tools.setdefault(tc.index, {"name": "", "args": ""})
                if tc.function and tc.function.name:
                    entry["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    entry["args"] += tc.function.arguments
        error = None
    except Exception as exc:  # noqa: BLE001 - reported per prompt
        error = str(exc)
    return {
        "first_text": timer.first_text,
        "first_sentence": timer.first_sentence,
        "total": time.monotonic() - timer.t0,
        "text": timer.text,
        "tools": [t["name"] for t in tools.values()],
        "error": error,
    }


async def bench(args) -> List[Dict[str, Any]]:
    rows = []
    for spec in args.models:
        model, _, effort = spec.partition(":") if spec.startswith("claude") else (spec, "", "")
        is_claude = model.startswith("claude")
        print(f"\n== {spec}", flush=True)
        if not is_claude:
            await run_openai(args.ollama, model, "Hi.", args.think)  # load the model; not counted
        for name, prompt in PROMPTS:
            if args.prompts and name not in args.prompts:
                continue
            for _ in range(args.repeat):
                if is_claude:
                    r = await run_claude(model, effort or None, prompt)
                else:
                    r = await run_openai(args.ollama, model, prompt, args.think)
                r.update(model=spec, prompt=name)
                rows.append(r)
                fs = f"{r['first_sentence']:4.1f}s" if r["first_sentence"] is not None else "  -  "
                said = r["text"].strip().replace("\n", " ")[:70]
                print(
                    f"  {name:<10} sentence {fs} total {r['total']:4.1f}s tools {r['tools']} {r['error'] or ''}"
                    f"\n             {said!r}",
                    flush=True,
                )
    return rows


def summarise(rows: List[Dict[str, Any]]):
    print(f"\n{'model':<28} {'first text':>10} {'1st sentence':>12} {'total':>7} {'errors':>6}")
    for spec in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == spec]

        def med(key):
            xs = [r[key] for r in mine if r[key] is not None and not r["error"]]
            return f"{statistics.median(xs):9.2f}s" if xs else "        - "

        errors = sum(1 for r in mine if r["error"])
        print(f"{spec:<28} {med('first_text')} {med('first_sentence'):>12} {med('total'):>7} {errors:>6}")
    print("(medians; first sentence = when Pepper could start speaking)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="+", help="claude-<model>[:effort] or an Ollama model name")
    parser.add_argument(
        "--ollama", default="http://alien3:11434", help="OpenAI-compatible server for non-Claude models"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--prompts", type=lambda s: s.split(","), help="comma-separated prompt names")
    parser.add_argument(
        "--think",
        type=lambda s: s.lower() in ("1", "true", "yes", "on"),
        default=None,
        help="for reasoning models on Ollama: true/false (default: the model's own default)",
    )
    parser.add_argument("--out", help="write every row as JSON here")
    args = parser.parse_args()
    rows = asyncio.run(bench(args))
    summarise(rows)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
