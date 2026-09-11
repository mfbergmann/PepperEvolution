"""
Opt-in checks of the bridge against a real NAOqi: the headless virtual Pepper
(``scripts/virtual_pepper.sh``) or, with care, the robot itself.

    PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:8899 pytest tests/test_virtual_naoqi.py -v

Everything here is skipped unless ``PEPPER_VIRTUAL_BRIDGE`` is set. The
desktop NAOqi has no hardware layer, so the tests only assert what both a
virtual and a physical robot can satisfy; the checks that differ (audio,
animations, camera) accept either outcome and print what they saw.
"""

import asyncio
import os

import pytest
import pytest_asyncio

from src.pepper.audio_stream import AudioStream
from src.pepper.bridge_client import BridgeClient, BridgeError
from src.pepper.event_stream import EventStream

BASE = os.environ.get("PEPPER_VIRTUAL_BRIDGE", "")
API_KEY = os.environ.get("BRIDGE_API_KEY", "")

pytestmark = pytest.mark.skipif(not BASE, reason="set PEPPER_VIRTUAL_BRIDGE=http://host:port to run against a NAOqi")


@pytest_asyncio.fixture
async def client():  # one client per test: each test runs on its own event loop
    c = BridgeClient(BASE, api_key=API_KEY, timeout=30, action_timeout=90)
    await c.connect()
    yield c
    await c.close()


def ws_url(path: str) -> str:
    return BASE.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + path


class TestAgainstRealNaoqi:
    async def test_health_and_status(self, client):
        health = await client.health()
        assert health["naoqi_connected"] is True and health["version"]
        status = await client.status()
        assert status["language"] and status["autonomous_life"] in ("solitary", "interactive", "safeguard", "disabled")
        assert "awareness" in status and "halted" in status

    async def test_prepare_disables_life_and_wakes(self, client):
        result = await client.prepare("disabled", True, "Stand", True)
        assert result["awake"] is True and result["autonomous_life"]["state"] == "disabled"
        assert result["posture"]["posture"] == "Stand"
        assert result.get("errors") == [], result.get("errors")
        awareness = result["awareness"]
        assert awareness["enabled"] is True and awareness["tracking_mode"] == "Head"
        print("stimuli:", awareness["stimuli"], "unavailable:", awareness.get("stimuli_unavailable"))
        status = await client.status()
        assert status["awake"] is True and status["autonomous_life"] == "disabled"

    async def test_speech_plain_animated_and_language(self, client):
        plain = await client.speak("Hello from the test", animated=False)
        assert plain["spoken"] == "Hello from the test" and plain["duration"] >= 0
        animated = await client.speak("Nice to meet you", animated=True)
        assert animated["animated"] is True
        french = await client.speak("Bonjour", language="fr", animated=False)
        assert french["language"] == "fr"
        assert (await client.status())["language"] == "English"
        await client.stop_speaking()
        assert (await client.set_volume(50))["level"] == 50

    async def test_head_moves_within_the_pepper_envelope(self, client):
        moved = await client.move_head(20, -10)
        assert moved["yaw"] == 20.0 and moved["pitch"] == -10.0 and "awareness_paused" in moved
        clamped = await client.move_head(0, 60)
        assert clamped["pitch"] == 25.5
        far = await client.move_head(200, 0)
        assert far["yaw"] == 119.5
        await client.move_head(0, 0)

    async def test_base_moves_complete_and_stop(self, client):
        turn = await client.move_turn(20)
        assert turn["completed"] is True
        sensors = await client.get_sensors()
        if sensors["obstacle"]:
            pytest.skip("something is in front of the robot; not driving")
        forward = await client.move_forward(0.1)
        assert forward["completed"] is True
        back = await client.move_forward(-0.1)
        assert back["completed"] is True
        assert (await client.stop())["ok"] is True
        assert (await client.set_posture("Stand"))["reached"] is True

    async def test_awareness_options_reach_naoqi(self, client):
        result = await client.set_awareness(
            True, tracking_mode="Head", engagement_mode="SemiEngaged", stimuli=["People"]
        )
        assert result["tracking_mode"] == "Head" and result["engagement_mode"] == "SemiEngaged"
        assert result["stimuli"] == ["People"]
        assert (await client.status())["awareness"] is True
        with pytest.raises(BridgeError):
            await client.set_awareness(True, tracking_mode="Spin")
        assert (await client.set_awareness(False))["enabled"] is False

    async def test_leds(self, client):
        assert (await client.set_eye_leds(color="blue"))["rgb"] == "#0000ff"
        assert (await client.set_chest_leds(r=1, g=0.5, b=0))["rgb"] == "#ff8000"
        await client.set_eye_leds(color="white")

    async def test_animations(self, client):
        installed = await client.list_animations()
        print("installed animations:", len(installed))
        if not installed:
            with pytest.raises(BridgeError) as info:  # the desktop build has no animation package
                await client.play_animation("animations/Stand/Gestures/Hey_1")
            print("animation error:", info.value)
        else:
            result = await client.play_animation(installed[0])
            assert result["animation"] == installed[0]

    async def test_camera(self, client):
        try:
            shot = await client.take_picture(0, 1)
        except BridgeError as exc:
            print("no camera:", exc)
            return
        assert shot["width"] > 0 and shot["image"]

    async def test_emergency_stop_and_recovery(self, client):
        stopped = await client.emergency_stop()
        assert stopped["halted"] is True
        assert (await client.status())["halted"] is True
        with pytest.raises(BridgeError):
            await client.move_forward(0.1)
        with pytest.raises(BridgeError):
            await client.move_head(10, 0)
        woke = await client.wake_up()
        assert woke["halted"] is False and woke["awake"] is True
        await client.move_head(0, 0)

    async def test_event_stream(self, client):
        stream = EventStream(ws_url("/ws/events"), api_key=API_KEY)
        received = []

        async def on_event(kind, data):
            received.append(kind)

        stream.on_any(on_event)
        await stream.start()
        try:
            for _ in range(50):
                if "sensors" in received:
                    break
                await asyncio.sleep(0.1)
            await client.speak("event check", animated=False)
            await asyncio.sleep(0.5)
        finally:
            await stream.stop()
        assert "sensors" in received and received.count("speech") >= 2

    async def test_audio_stream_starts_or_fails_cleanly(self, client):
        stream = AudioStream(ws_url("/ws/audio"), api_key=API_KEY)
        await stream.start()
        try:
            for _ in range(80):
                if stream.streaming or stream.last_error:
                    break
                await asyncio.sleep(0.1)
        finally:
            await stream.stop()
        assert stream.streaming or stream.last_error, "neither frames nor an error within 8 s"
        print("audio:", "streaming" if stream.streaming else stream.last_error)
        await asyncio.sleep(0.5)
        info = await client.audio_stream_info()
        assert info["clients"] == 0
