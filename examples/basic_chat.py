#!/usr/bin/env python3
"""
Chat with Pepper from the terminal - no web UI, no API server.

    python examples/basic_chat.py                     # real robot (bridge must be running)
    PEPPER_FAKE_BRIDGE=true python examples/basic_chat.py   # no robot needed

Type a message and press Enter. Commands: /photo, /sensors, /status, /clear, /quit.
"""

import asyncio
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from main import Settings, build_provider  # noqa: E402
from src.ai import AIManager  # noqa: E402
from src.pepper import ConnectionConfig, FakeBridgeClient, PepperRobot, PrepareOptions  # noqa: E402


async def chat_loop():
    load_dotenv()
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING"))
    settings = Settings.from_env()

    config = ConnectionConfig(ip=settings.pepper_ip, bridge_port=settings.bridge_port, api_key=settings.bridge_api_key)
    robot = PepperRobot(config, bridge=FakeBridgeClient() if settings.fake_bridge else None)
    ai = AIManager(
        robot,
        build_provider(settings),
        speak_responses=settings.speak_responses,
        tablet_subtitles=settings.tablet_subtitles,
        react_to_touch=False,
    )

    prepare = PrepareOptions(enabled=settings.prepare_on_connect, autonomous_life=settings.autonomous_life)
    if not await robot.initialize(prepare=prepare):
        print(f"Could not connect to the bridge at {config.base_url}. Is it running?")
        return 1

    async def show_event(event_type, data):
        if event_type != "speech":
            print(f"  [event] {event_type}: {data}")

    robot.on_event(show_event)
    print(
        f"Connected to {robot.state.robot_name} (battery {robot.state.battery_level:.0f}%). "
        f"Model: {ai.provider.model}. Type /quit to exit.\n"
    )

    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                line = await loop.run_in_executor(None, input, "you> ")
            except EOFError:
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/clear":
                ai.clear_conversation_history()
                print("  (history cleared)")
                continue
            if line == "/status":
                print(f"  {(await robot.refresh_state()).as_dict()}")
                continue
            if line == "/sensors":
                print(f"  {await robot.get_sensors()}")
                continue
            if line == "/photo":
                photo = await robot.take_picture()
                out = Path("last_photo.jpg" if photo.media_type == "image/jpeg" else "last_photo.png")
                out.write_bytes(base64.b64decode(photo.base64_data))
                print(f"  saved {out} ({photo.width}x{photo.height})")
                continue

            result = await ai.process_user_input(line)
            for tc in result["tool_calls"]:
                print(f"  [tool] {tc['name']}({tc['input']}) -> {tc['result'][:160]}")
            print(f"pepper> {result['text']}\n")
    finally:
        await robot.shutdown(rest=settings.rest_on_exit)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(chat_loop()))
    except KeyboardInterrupt:
        pass
