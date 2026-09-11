#!/usr/bin/env python3
"""
Listen to the robot's microphone stream from the bridge and show a level
meter - checks that /ws/audio delivers frames and that capture is muted while
Pepper speaks, without involving speech recognition or the AI.

    python examples/mic_monitor.py            # level meter until Ctrl-C
    python examples/mic_monitor.py --speak    # also make Pepper say a sentence and report dropped frames
    python examples/mic_monitor.py --wav out.wav --seconds 10   # save what the microphone hears
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from src.audio import rms, wav_bytes  # noqa: E402
from src.pepper import AudioStream, ConnectionConfig, PepperRobot  # noqa: E402


async def monitor(args):
    load_dotenv()
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING"))

    config = ConnectionConfig(
        ip=os.getenv("PEPPER_IP", "10.0.100.100"),
        bridge_port=int(os.getenv("BRIDGE_PORT", "8888")),
        api_key=os.getenv("BRIDGE_API_KEY", ""),
    )
    robot = PepperRobot(config)
    if not await robot.initialize(prepare=None):
        print(f"Could not connect to the bridge at {config.base_url}")
        return 1

    stream = AudioStream(config.audio_ws_url, api_key=config.api_key)
    captured = bytearray()
    frames = 0

    async def on_audio(pcm: bytes):
        nonlocal frames
        frames += 1
        if args.wav:
            captured.extend(pcm)
        level = rms(pcm)
        bar = "#" * min(60, int(level / 250))
        print(f"\r{frames:6d} frames  level {level:6.0f} |{bar:<60}|", end="", flush=True)

    stream.on_audio(on_audio)
    await stream.start()
    print(f"Listening to {robot.state.robot_name}'s front microphone via {config.audio_ws_url}. Ctrl-C to stop.")
    try:
        for _ in range(50):
            if stream.streaming:
                break
            await asyncio.sleep(0.1)
        info = await robot.bridge.audio_stream_info()
        print(f"bridge: {info}")
        if not stream.streaming:
            print(f"The bridge did not start capturing: {stream.last_error or info}")
            return 1

        if args.speak:
            before = (await robot.bridge.audio_stream_info())["dropped"]
            print("\nSpeaking; frames captured meanwhile must be dropped, not streamed...")
            await robot.speak("Testing the microphone. I should not hear myself while I talk.", animated=False)
            await asyncio.sleep(0.6)
            after = (await robot.bridge.audio_stream_info())["dropped"]
            print(f"dropped while speaking: {after - before} frames ({'ok' if after > before else 'NOT MUTED'})")

        deadline = asyncio.get_running_loop().time() + args.seconds if args.seconds else None
        while deadline is None or asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
    finally:
        print()
        await stream.stop()
        if args.wav and captured:
            Path(args.wav).write_bytes(wav_bytes(bytes(captured), stream.sample_rate))
            print(f"saved {len(captured) // 2 / stream.sample_rate:.1f} s to {args.wav}")
        await robot.shutdown()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--speak", action="store_true", help="say a sentence and check that capture was muted")
    parser.add_argument("--wav", help="save the captured audio to this WAV file on exit")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (default: Ctrl-C)")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(monitor(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
