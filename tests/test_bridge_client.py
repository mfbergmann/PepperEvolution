"""
Tests for BridgeClient - HTTP client to the bridge server.
"""

import pytest
import respx
import httpx

from src.pepper.bridge_client import BridgeClient, BridgeError

BRIDGE_BASE = "http://10.0.100.100:8888"


class TestBridgeClient:

    @respx.mock
    @pytest.mark.asyncio
    async def test_health(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=httpx.Response(
            200, json={"ok": True, "version": "2.0.0"}
        ))
        result = await client.health()
        assert result["ok"] is True
        assert result["version"] == "2.0.0"
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_status(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.get(f"{BRIDGE_BASE}/status").mock(return_value=httpx.Response(
            200, json={"ok": True, "battery": 85, "posture": "Stand"}
        ))
        result = await client.status()
        assert result["battery"] == 85
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_speak(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/speak").mock(return_value=httpx.Response(
            200, json={"ok": True}
        ))
        result = await client.speak("Hello world")
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_move_forward(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/move/forward").mock(return_value=httpx.Response(
            200, json={"ok": True, "distance": 0.5}
        ))
        result = await client.move_forward(0.5)
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_move_turn(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/move/turn").mock(return_value=httpx.Response(
            200, json={"ok": True, "angle": 90}
        ))
        result = await client.move_turn(90)
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_take_picture(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.get(f"{BRIDGE_BASE}/picture").mock(return_value=httpx.Response(
            200, json={"ok": True, "image": "abc123", "width": 640, "height": 480, "format": "jpeg"}
        ))
        result = await client.take_picture()
        assert result["image"] == "abc123"
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_sensors(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.get(f"{BRIDGE_BASE}/sensors").mock(return_value=httpx.Response(
            200, json={"ok": True, "battery": 70, "touch": {}, "sonar": {"left": 1.0, "right": 1.5}}
        ))
        result = await client.get_sensors()
        assert result["battery"] == 70
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_set_eye_leds(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/leds/eyes").mock(return_value=httpx.Response(
            200, json={"ok": True}
        ))
        result = await client.set_eye_leds(color="blue")
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_emergency_stop(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/emergency_stop").mock(return_value=httpx.Response(
            200, json={"ok": True}
        ))
        result = await client.emergency_stop()
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_unauthorized(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=httpx.Response(
            401, json={"ok": False, "error": "unauthorized"}
        ))
        with pytest.raises(BridgeError, match="Unauthorized"):
            await client.health()
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_bridge_error(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/speak").mock(return_value=httpx.Response(
            500, json={"ok": False, "error": "TTS service unavailable"}
        ))
        with pytest.raises(BridgeError, match="TTS service unavailable"):
            await client.speak("test")
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_play_animation(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/animation").mock(return_value=httpx.Response(
            200, json={"ok": True}
        ))
        result = await client.play_animation("animations/Stand/Gestures/Hey_1")
        assert result["ok"] is True
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_set_posture(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/posture").mock(return_value=httpx.Response(
            200, json={"ok": True, "posture": "Crouch"}
        ))
        result = await client.set_posture("Crouch")
        assert result["posture"] == "Crouch"
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_record_audio(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        await client.connect()
        respx.post(f"{BRIDGE_BASE}/audio/record").mock(return_value=httpx.Response(
            200, json={"ok": True, "audio": "base64wav", "format": "wav", "duration": 3.0}
        ))
        result = await client.record_audio(3.0)
        assert result["audio"] == "base64wav"
        await client.close()

    def test_not_connected(self):
        client = BridgeClient(base_url=BRIDGE_BASE)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.client

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_key_header(self):
        c = BridgeClient(base_url=BRIDGE_BASE, api_key="secret123")
        await c.connect()
        respx.get(f"{BRIDGE_BASE}/health").mock(return_value=httpx.Response(
            200, json={"ok": True}
        ))
        await c.health()
        request = respx.calls[0].request
        assert request.headers["X-API-Key"] == "secret123"
        await c.close()
