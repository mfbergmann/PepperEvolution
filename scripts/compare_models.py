#!/usr/bin/env python3
"""
Compare models for Pepper's conversational turns, on the robot.

For each configuration (model and effort), starts ``main.py`` against the bridge with
that model, sends the same scripted prompts through ``POST /chat`` in one fresh
conversation, and records per turn: time to first sound and to first real words
(from the WebSocket's spoken-sentence stream), total time, tool calls, what was
spoken, token usage and cost. Photo turns save the image so descriptions can be
checked against it.

    python scripts/compare_models.py --bridge http://10.0.100.100:8888 --out results/models
    python scripts/compare_models.py --bridge ... --only claude-sonnet-5:low --prompts greeting,photo

Prints a summary table and writes ``results.json`` (every turn) to ``--out``. Costs
a few cents per configuration; the robot talks, turns its head and gestures, but
does not drive.
"""

import argparse
import asyncio
import base64
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent

CONFIGS = [
    ("claude-opus-5", "low"),
    ("claude-opus-5-5", "low"),
    ("claude-sonnet-5", "low"),
    ("claude-sonnet-5", "medium"),
    ("claude-haiku-4-5", ""),
]

# $ per million input / output tokens (Claude API list prices, checked 2026-09-22); cache reads at 10 % of input.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

PROMPTS = [
    ("greeting", "Hello Pepper, how are you today?"),
    ("look_left", "Look to your left and tell me what's there."),
    ("photo", "What can you see in front of you?"),
    ("gesture", "Can you wave at me and say goodbye in a fun way?"),
    ("multi_step", "Turn your eyes blue, look to your right, then look back at me and tell me what you saw."),
    ("factual", "How far away is the moon, roughly?"),
    ("chit_chat", "What's your favourite thing about being a robot?"),
]

FILLERS = {"Hmm.", "Let me think.", "One moment.", "Let me see."}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cost(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    fresh = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) * 1.25
    cached = usage.get("cache_read_input_tokens", 0) * 0.1
    return ((fresh + cached) * pin + usage.get("output_tokens", 0) * pout) / 1e6


async def run_config(model: str, effort: str, args, out: Path) -> list:
    port = free_port()
    url = httpx.URL(args.bridge)
    env = dict(
        os.environ,
        AI_MODEL=model,
        AI_EFFORT=effort or "none",
        API_PORT=str(port),
        API_HOST="127.0.0.1",
        PEPPER_FAKE_BRIDGE="false",
        PEPPER_IP=url.host,
        BRIDGE_PORT=str(url.port or 8888),
        STT_BACKEND="none",
        VOICE_INPUT="false",
        REACT_TO_TOUCH="false",
        TABLET_SUBTITLES="false",
        PREPARE_ON_CONNECT="true",
        LOG_FILE=str(out / f"{model}_{effort or 'default'}.log"),
    )
    log = open(out / f"{model}_{effort or 'default'}.stdout", "w")
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    turns = []
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=180) as c:
            for _ in range(240):
                if proc.poll() is not None:
                    raise RuntimeError(f"host exited; see {log.name}")
                try:
                    if (await c.get("/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            feed = []
            async with connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as ws:

                async def reader():
                    async for raw in ws:
                        d = json.loads(raw)
                        if d.get("type") == "chat_partial":
                            feed.append((time.monotonic(), d["text"]))

                task = asyncio.create_task(reader())
                for name, prompt in PROMPTS:
                    if args.prompts and name not in args.prompts:
                        continue
                    await asyncio.sleep(1.0)
                    start = len(feed)
                    t0 = time.monotonic()
                    r = (await c.post("/chat", json={"message": prompt, "speak": True})).json()
                    total = time.monotonic() - t0
                    spoken_at = feed[start:]
                    first_sound = spoken_at[0][0] - t0 if spoken_at else None
                    first_words = next((ts - t0 for ts, text in spoken_at if text not in FILLERS), None)
                    photo_file = None
                    if r.get("photo"):
                        photo_file = out / f"{model}_{effort or 'default'}_{name}.jpg"
                        photo_file.write_bytes(base64.b64decode(r["photo"]["base64"]))
                    turn = {
                        "model": model,
                        "effort": effort,
                        "prompt": name,
                        "first_sound": first_sound,
                        "first_words": first_words,
                        "total": total,
                        "tools": [tc["name"] for tc in r.get("tool_calls", [])],
                        "tools_failed": [tc["name"] for tc in r.get("tool_calls", []) if not tc.get("ok")],
                        "spoken": r.get("spoken", []),
                        "text": r.get("text", ""),
                        "stop_reason": r.get("stop_reason"),
                        "rounds": r.get("rounds"),
                        "usage": r.get("usage", {}),
                        "cost": cost(model, r.get("usage", {})),
                        "photo": str(photo_file) if photo_file else None,
                    }
                    turns.append(turn)
                    fw = f"{first_words:4.1f}s" if first_words is not None else "  -  "
                    print(f"  {name:<11} first words {fw}  total {total:4.1f}s  tools {turn['tools']}", flush=True)
                task.cancel()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    return turns


def summarise(all_turns: list):
    print(f"\n{'config':<26} {'first sound':>11} {'first words':>11} {'total':>7} {'words 1st':>9} {'cost':>8}")
    for model, effort in CONFIGS:
        turns = [t for t in all_turns if t["model"] == model and t["effort"] == effort]
        if not turns:
            continue
        med = lambda xs: statistics.median(xs) if xs else float("nan")  # noqa: E731
        fs = med([t["first_sound"] for t in turns if t["first_sound"] is not None])
        fw = med([t["first_words"] for t in turns if t["first_words"] is not None])
        tot = med([t["total"] for t in turns])
        words_first = sum(1 for t in turns if t["spoken"] and t["spoken"][0] not in FILLERS)
        spend = sum(t["cost"] for t in turns)
        label = f"{model}:{effort or '-'}"
        print(f"{label:<26} {fs:10.1f}s {fw:10.1f}s {tot:6.1f}s {words_first:>4}/{len(turns):<4} ${spend:7.3f}")
    print("(medians per turn; 'words 1st' = turns whose first sound was a real answer, not a filler)")


async def main_async(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    configs = CONFIGS
    if args.only:
        wanted = set(args.only)
        configs = [(m, e) for m, e in CONFIGS if f"{m}:{e or '-'}" in wanted or m in wanted]
    all_turns = []
    for model, effort in configs:
        print(f"\n== {model} effort={effort or '(none)'}", flush=True)
        try:
            all_turns += await run_config(model, effort, args, out)
        except Exception as exc:  # noqa: BLE001 - keep going with the other models
            print(f"  FAILED: {exc}")
        (out / "results.json").write_text(json.dumps(all_turns, indent=1))
    summarise(all_turns)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bridge", required=True, help="bridge URL, e.g. http://10.0.100.100:8888")
    parser.add_argument("--out", default="results/models", help="folder for results.json, logs and photos")
    parser.add_argument("--only", action="append", help="model or model:effort to run (repeatable)")
    parser.add_argument("--prompts", type=lambda s: s.split(","), help="comma-separated prompt names")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
