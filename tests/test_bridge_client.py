"""
Tests for BridgeClient - HTTP client to the bridge server.
"""

import json

import httpx
import pytest
import respx

from src.pepper.bridge_client import BridgeClient, BridgeError

BRIDGE_BASE = "http://10.0.100.100:8888"


def ok(**data):
    return httpx.Response(200, json={"ok": True, **data})


class TestBridgeClient:

    @respx.mock
    async def test_health(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=ok(version="2.1.0", robot_name="Pepper"))
        result = await connected_bridge_client.health()
        assert result["ok"] is True
        assert result["version"] == "2.1.0"

    @respx.mock
    async def test_status(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/status").mock(return_value=ok(battery=85, posture="Stand", awake=True))
        result = await connected_bridge_client.status()
        assert result["battery"] == 85
        assert result["awake"] is True

    @respx.mock
    async def test_speak_sends_body_and_uses_action_timeout(self, connected_bridge_client):
        route = respx.post(f"{BRIDGE_BASE}/speak").mock(return_value=ok(duration=1.5))
        result = await connected_bridge_client.speak("Hello world", language="French")
        assert result["duration"] == 1.5
        body = json.loads(route.calls[0].request.content)
        assert body == {
            "text": "Hello world",
            "language": "French",
            "animated": True,
            "wait": True,
            "body_language": "contextual",
        }

    @respx.mock
    async def test_speak_no_wait(self, connected_bridge_client):
        route = respx.post(f"{BRIDGE_BASE}/speak").mock(return_value=ok(queued=True))
        await connected_bridge_client.speak("Hi", wait=False)
        assert json.loads(route.calls[0].request.content)["wait"] is False

    @respx.mock
    async def test_move_forward(self, connected_bridge_client):
        route = respx.post(f"{BRIDGE_BASE}/move/forward").mock(return_value=ok(distance=0.5))
        result = await connected_bridge_client.move_forward(0.5, speed=0.2)
        assert result["ok"] is True
        assert json.loads(route.calls[0].request.content) == {"distance": 0.5, "speed": 0.2}

    @respx.mock
    async def test_move_turn(self, connected_bridge_client):
        respx.post(f"{BRIDGE_BASE}/move/turn").mock(return_value=ok(angle=90))
        result = await connected_bridge_client.move_turn(90)
        assert result["angle"] == 90

    @respx.mock
    async def test_prepare(self, connected_bridge_client):
        route = respx.post(f"{BRIDGE_BASE}/prepare").mock(return_value=ok(awake=True))
        await connected_bridge_client.prepare(autonomous_life="disabled", wake_up=True, posture="Stand")
        body = json.loads(route.calls[0].request.content)
        assert body == {"autonomous_life": "disabled", "wake_up": True, "posture": "Stand"}

    @respx.mock
    async def test_take_picture(self, connected_bridge_client):
        route = respx.get(f"{BRIDGE_BASE}/picture").mock(
            return_value=ok(image="abc123", width=640, height=480, format="jpeg")
        )
        result = await connected_bridge_client.take_picture(camera=1, resolution=1)
        assert result["image"] == "abc123"
        assert route.calls[0].request.url.params["camera"] == "1"

    @respx.mock
    async def test_get_sensors(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/sensors").mock(
            return_value=ok(battery=70, touch={}, sonar={"front": 1.0, "back": 1.5}, obstacle=False)
        )
        result = await connected_bridge_client.get_sensors()
        assert result["sonar"]["front"] == 1.0

    @respx.mock
    async def test_set_eye_leds(self, connected_bridge_client):
        route = respx.post(f"{BRIDGE_BASE}/leds/eyes").mock(return_value=ok())
        await connected_bridge_client.set_eye_leds(color="blue")
        assert json.loads(route.calls[0].request.content)["color"] == "blue"

    @respx.mock
    async def test_emergency_stop(self, connected_bridge_client):
        respx.post(f"{BRIDGE_BASE}/emergency_stop").mock(return_value=ok())
        assert (await connected_bridge_client.emergency_stop())["ok"] is True

    @respx.mock
    async def test_list_animations(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/animations").mock(return_value=ok(animations=["a/b", "c/d"], count=2))
        assert await connected_bridge_client.list_animations() == ["a/b", "c/d"]

    @respx.mock
    async def test_tablet(self, connected_bridge_client):
        text_route = respx.post(f"{BRIDGE_BASE}/tablet/text").mock(return_value=ok())
        hide_route = respx.post(f"{BRIDGE_BASE}/tablet/hide").mock(return_value=ok())
        await connected_bridge_client.tablet_text("Hi", title="Pepper")
        await connected_bridge_client.tablet_hide()
        assert json.loads(text_route.calls[0].request.content) == {"text": "Hi", "title": "Pepper"}
        assert hide_route.called

    @respx.mock
    async def test_unauthorized(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/health").mock(
            return_value=httpx.Response(401, json={"ok": False, "error": "unauthorized"})
        )
        with pytest.raises(BridgeError, match="Unauthorized"):
            await connected_bridge_client.health()

    @respx.mock
    async def test_bridge_error(self, connected_bridge_client):
        respx.post(f"{BRIDGE_BASE}/speak").mock(
            return_value=httpx.Response(500, json={"ok": False, "error": "TTS service unavailable"})
        )
        with pytest.raises(BridgeError, match="TTS service unavailable"):
            await connected_bridge_client.speak("test")

    @respx.mock
    async def test_non_json_error(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=httpx.Response(404, text="<html>Not Found</html>"))
        with pytest.raises(BridgeError, match="HTTP 404"):
            await connected_bridge_client.health()

    @respx.mock
    async def test_timeout_becomes_bridge_error(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/health").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(BridgeError, match="timed out"):
            await connected_bridge_client.health()

    @respx.mock
    async def test_connection_error_becomes_bridge_error(self, connected_bridge_client):
        respx.get(f"{BRIDGE_BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(BridgeError, match="unreachable"):
            await connected_bridge_client.health()

    def test_not_connected(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.client

    @respx.mock
    async def test_api_key_header(self):
        c = BridgeClient(base_url=BRIDGE_BASE, api_key="secret123")
        await c.connect()
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=ok())
        await c.health()
        assert respx.calls[0].request.headers["X-API-Key"] == "secret123"
        await c.close()
