#!/usr/bin/env python3
"""
End-to-end smoke test of the host application with the real model.

Starts ``main.py`` against either the in-memory fake robot or a running bridge
(the virtual Pepper from ``scripts/virtual_pepper.sh``, or the real robot),
runs a short conversation through ``POST /chat`` including a mid-turn "stop",
and prints what happened: tool calls, spoken sentences, eye-colour changes,
fillers, warnings. Costs a few model calls.

    python scripts/smoke_host.py --fake                          # no robot
    python scripts/smoke_host.py --bridge http://127.0.0.1:8899  # virtual Pepper (scripts/virtual_pepper.sh)
    python scripts/smoke_host.py --bridge http://10.0.100.100:8888 --no-move   # the robot, without driving

Needs ANTHROPIC_API_KEY (from .env). Uses the current interpreter for main.py.
"""

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run(args) -> int:
    port = free_port()
    workdir = Path(tempfile.mkdtemp(prefix="pepper-smoke-"))
    env = dict(
        os.environ,
        API_PORT=str(port),
        API_HOST="127.0.0.1",
        STT_BACKEND="fake",
        TABLET_SUBTITLES="false",
        REST_ON_EXIT="false",
        LOG_LEVEL="INFO",
        LOG_FILE=str(workdir / "host.log"),
        BACKCHANNEL_AFTER=str(args.filler_after),
    )
    if args.fake:
        env["PEPPER_FAKE_BRIDGE"] = "true"
    else:
        url = urlparse(args.bridge)
        env.update(PEPPER_FAKE_BRIDGE="false", PEPPER_IP=url.hostname or "127.0.0.1", BRIDGE_PORT=str(url.port or 8888))
    log = open(workdir / "stdout.log", "w")
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=180) as c:
            for _ in range(600):
                if proc.poll() is not None:
                    print("host exited early; see", workdir / "stdout.log")
                    return 1
                try:
                    if (await c.get("/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                print("host did not start")
                return 1
            state = (await c.get("/status")).json()["robot_state"]
            print("robot:", {k: state.get(k) for k in ("robot_name", "awake", "autonomous_life", "posture")})
            print("voice:", (await c.get("/voice/status")).json().get("backend"))

            prompts = ["Look to your left, then say hello in French.", "Make your eyes green and wave."]
            if not args.no_move:
                prompts.append("Turn 20 degrees, then tell me what your sensors say in one sentence.")
            for msg in prompts:
                t0 = time.monotonic()
                r = (await c.post("/chat", json={"message": msg, "speak": True})).json()
                print(f"\n> {msg}\n  {time.monotonic() - t0:.1f}s rounds={r['rounds']} stop={r['stop_reason']}")
                print("  tools:", [(tc["name"], tc["ok"], tc["result"][:60]) for tc in r["tool_calls"]])
                print("  spoken:", r["spoken"])

            long_task = "Tell me a long story about a robot, one sentence at a time."
            if not args.no_move:
                long_task = "Drive forward half a metre, then turn around, then " + long_task[0].lower() + long_task[1:]
            t0 = time.monotonic()
            turn = asyncio.create_task(c.post("/chat", json={"message": long_task, "speak": True}))
            await asyncio.sleep(args.stop_after)
            stop = (await c.post("/chat", json={"message": "stop", "speak": True})).json()
            print(
                f"\n> stop at +{time.monotonic() - t0:.1f}s -> intent={stop.get('intent')} spoken={stop.get('spoken')}"
            )
            r = (await turn).json()
            print(f"  aborted turn ended at +{time.monotonic() - t0:.1f}s rounds={r['rounds']} text={r['text'][:70]!r}")
            print("  tools:", [(tc["name"], tc["ok"], tc["result"][:60]) for tc in r["tool_calls"]])
            print("  history:", len((await c.get("/conversation/history")).json()["history"]), "messages")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    text = (workdir / "stdout.log").read_text(errors="replace")
    if args.fake:
        eyes = re.findall(r"set_eye_leds \{'color': '(\w+)'\}", text)
        print("\neye colour sequence:", eyes)
    fillers = [
        m
        for m in re.findall(r"'text': '([^']*)'", text)
        if m in ("Hmm.", "Let me think.", "One moment.", "Let me see.")
    ]
    print("fillers spoken:", fillers)
    problems = [ln.split(" | ")[-1][:140] for ln in text.splitlines() if "| ERROR" in ln or "| WARNING" in ln]
    print("errors/warnings:", problems[:8] or "none")
    print("logs:", workdir)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--fake", action="store_true", help="use the in-memory fake robot")
    target.add_argument("--bridge", help="bridge URL, e.g. http://127.0.0.1:8899 (virtual) or http://10.0.100.100:8888")
    parser.add_argument(
        "--no-move", action="store_true", help="do not ask for base moves (a real robot in a small room)"
    )
    parser.add_argument("--stop-after", type=float, default=3.0, help="seconds into the long task before saying stop")
    parser.add_argument("--filler-after", type=float, default=1.5, help="BACKCHANNEL_AFTER for the run")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (put it in .env)")
        sys.exit(2)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
