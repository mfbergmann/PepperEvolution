"""
End-to-end test of the real bridge server process against the real host client.

Starts ``robot_bridge/pepper_bridge.py`` as a subprocess with the fake ``qi``
module from ``tests/fakenaoqi`` and talks to it with BridgeClient + EventStream.

By default it runs the bridge under the host interpreter (modern Tornado). Set
``PEPPER_BRIDGE_PYTHON`` to a Python 2.7 interpreter that has Tornado 3.1.1
installed to reproduce the robot's environment exactly.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.pepper.bridge_client import BridgeClient, BridgeError
from src.pepper.event_stream import EventStream

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "robot_bridge" / "pepper_bridge.py"
FAKE_QI_DIR = ROOT / "tests" / "fakenaoqi"

pytestmark = pytest.mark.skipif(os.environ.get("PEPPER_SKIP_INTEGRATION") == "1", reason="integration disabled")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _interpreter() -> str:
    return os.environ.get("PEPPER_BRIDGE_PYTHON") or sys.executable


@pytest.fixture(scope="module")
def bridge_process():
    interpreter = _interpreter()
    try:
        subprocess.run([interpreter, "-c", "import tornado"], check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        pytest.skip(f"{interpreter} has no tornado; cannot run the bridge")

    port = _free_port()
    env = dict(os.environ, PYTHONPATH=str(FAKE_QI_DIR), PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [interpreter, str(BRIDGE), f"--port={port}", "--api-key=testkey", "--log-level=INFO"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Wait for the listening line
    deadline = time.time() + 20
    lines = []
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if "listening on" in line:
            break
    else:
        proc.kill()
        pytest.fail("bridge did not start:\n" + "".join(lines))
    if proc.poll() is not None:
        pytest.fail("bridge exited early:\n" + "".join(lines))
    # The bridge listens before NAOqi is connected; wait until /health says ok.
    import urllib.error
    import urllib.request

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"X-API-Key": "testkey"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if json.loads(resp.read().decode()).get("ok"):
                    break
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("bridge never reported NAOqi connected")

    yield {"port": port, "proc": proc, "interpreter": interpreter}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
async def client(bridge_process):
    c = BridgeClient(base_url=f"http://127.0.0.1:{bridge_process['port']}", api_key="testkey", timeout=10)
    await c.connect()
    yield c
    await c.close()


class TestBridgeEndToEnd:

    async def test_health_and_status(self, client, bridge_process):
        health = await client.health()
        assert health["bridge"] == "pepper_bridge" and health["robot_name"] == "FakePepper"
        assert health["naoqi"] == "2.5.10.7"
        status = await client.status()
        assert status["battery"] == 81 and status["posture"] == "Stand"
        assert status["autonomous_life"] == "solitary"

    async def test_auth_required(self, bridge_process):
        bad = BridgeClient(base_url=f"http://127.0.0.1:{bridge_process['port']}", api_key="wrong")
        await bad.connect()
        try:
            with pytest.raises(BridgeError, match="Unauthorized"):
                await bad.health()
        finally:
            await bad.close()

    async def test_motion_refused_before_wake_up(self, client):
        with pytest.raises(BridgeError, match="resting"):
            await client.move_turn(30)

    async def test_prepare_then_speak_and_move(self, client):
        result = await client.prepare(autonomous_life="disabled", wake_up=True, posture="Stand", awareness=False)
        assert result["autonomous_life"]["state"] == "disabled"
        assert result["awake"] is True and result["awareness"] == {"enabled": False}
        status = await client.status()
        assert status["autonomous_life"] == "disabled" and status["awake"] is True and status["awareness"] is False

        spoke = await client.speak("Hello from the integration test", language="fr")
        assert spoke["spoken"] == "Hello from the integration test" and spoke["duration"] >= 0
        assert (await client.status())["language"] == "English"  # restored after the utterance

        move = await client.move_forward(0.3, 0.2)
        assert move["speed"] == 0.2 and move["completed"] is True
        assert (await client.move_turn(45))["angle"] == 45
        assert (await client.move_head(30, -10))["yaw"] == 30.0

    async def test_concurrent_requests_do_not_block_each_other(self, client):
        """A long speak must not stop /sensors from answering (handlers run off the IOLoop)."""
        long_text = " ".join(["word"] * 40)  # fake qi sleeps 0.05s/word = 2s
        speak_task = asyncio.create_task(client.speak(long_text))
        await asyncio.sleep(0.2)
        started = time.monotonic()
        sensors = await client.get_sensors()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"/sensors blocked for {elapsed:.2f}s behind /speak"
        assert sensors["sonar"] == {"front": 1.2, "back": 2.0}
        assert (await speak_task)["spoken"] == long_text

    async def test_sensors_shape(self, client):
        data = await client.get_sensors()
        assert set(data["touch"]) == {"head_front", "head_middle", "head_rear", "hand_left", "hand_right"}
        assert set(data["bumpers"]) == {"front_left", "front_right", "back"}
        assert data["battery"] == 81 and data["charging"] is False
        assert data["people_count"] == 1 and data["obstacle"] is False

    async def test_picture(self, client):
        pic = await client.take_picture(camera=0, resolution=1)
        assert pic["width"] == 8 and pic["height"] == 4
        assert pic["format"] in ("jpeg", "rgb") and pic["image"]

    async def test_animations_leds_tablet_volume(self, client):
        names = await client.list_animations()
        assert names == ["animations/Stand/Gestures/BowShort_1", "animations/Stand/Gestures/Hey_1"]
        assert (await client.play_animation("animations/Stand/Gestures/Hey_1"))["animation"].endswith("Hey_1")
        assert (await client.set_eye_leds(color="blue"))["rgb"] == "#0000ff"
        assert (await client.set_volume(70))["level"] == 70
        assert (await client.tablet_text("Hi there", title="Test"))["shown"] is True
        assert (await client.tablet_hide())["ok"] is True

    async def test_errors_are_json(self, client):
        with pytest.raises(BridgeError, match="unknown posture"):
            await client.set_posture("Handstand")
        with pytest.raises(BridgeError, match="text is required"):
            await client.speak("")
        with pytest.raises(BridgeError, match="unknown color"):
            await client.set_eye_leds(color="plaid")

    async def test_emergency_stop_and_recover(self, client):
        assert (await client.emergency_stop())["halted"] is True
        with pytest.raises(BridgeError, match="halted"):
            await client.move_turn(10)
        assert (await client.status())["halted"] is True
        assert (await client.wake_up())["awake"] is True
        assert (await client.move_turn(10))["completed"] is True

    async def test_event_stream_receives_touch_events(self, bridge_process):
        port = bridge_process["port"]
        stream = EventStream(f"ws://127.0.0.1:{port}/ws/events", api_key="testkey")
        received = []

        async def on_event(event_type, data):
            received.append((event_type, data))

        stream.on_any(on_event)
        await stream.start()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not any(e[0] == "touch" for e in received):
                await asyncio.sleep(0.1)
        finally:
            await stream.stop()
        touch = [e for e in received if e[0] == "touch"]
        assert touch, f"no touch event within 8s; got {received}"
        assert touch[0][1]["sensor"] == "head_front" and touch[0][1]["touched"] in (True, False)
        snapshot = [d for t, d in received if t == "sensors"]
        assert snapshot and snapshot[0]["battery"] == 81  # sent on connect

    async def test_speech_events_are_broadcast(self, client, bridge_process):
        stream = EventStream(f"ws://127.0.0.1:{bridge_process['port']}/ws/events", api_key="testkey")
        received = []

        async def on_event(event_type, data):
            received.append((event_type, data))

        stream.on_any(on_event)
        await stream.start()
        try:
            for _ in range(50):
                if stream.connected:
                    break
                await asyncio.sleep(0.1)
            await client.speak("Event check")
            await asyncio.sleep(0.5)
        finally:
            await stream.stop()
        states = [d["state"] for t, d in received if t == "speech"]
        assert states == ["start", "end"]
