#!/usr/bin/env python3
"""
Watch the robot's live event stream and sensors - handy for checking touch,
bumpers, sonar and people detection without involving the AI.

    python examples/event_monitor.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from src.pepper import ConnectionConfig, PepperRobot  # noqa: E402


async def monitor():
    load_dotenv()
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

    config = ConnectionConfig(
        ip=os.getenv("PEPPER_IP", "10.0.100.100"),
        bridge_port=int(os.getenv("BRIDGE_PORT", "8888")),
        api_key=os.getenv("BRIDGE_API_KEY", ""),
    )
    robot = PepperRobot(config)
    if not await robot.initialize(prepare=None):
        print(f"Could not connect to the bridge at {config.base_url}")
        return 1

    async def on_event(event_type, data):
        print(f"[{event_type}] {data}")

    robot.on_event(on_event)
    print(f"Watching {robot.state.robot_name}. Touch the head, wave a hand in front of the sonar... Ctrl-C to stop.")
    try:
        while True:
            sensors = await robot.get_sensors()
            print(
                f"  sensors: battery={sensors.get('battery')}% sonar={sensors.get('sonar')} "
                f"people={sensors.get('people_count')} touch={[k for k, v in sensors.get('touch', {}).items() if v]}"
            )
            await asyncio.sleep(5)
    finally:
        await robot.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(monitor()))
    except KeyboardInterrupt:
        pass
