"""
Tests for the FastAPI server (REST + WebSocket hub).
"""

import base64
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.ai.tools import TOOLS
from src.audio import FakeTranscriber, VoiceInput, tone
from src.communication.api import APIServer, execute_command
from src.pepper.bridge_client import BridgeError
from src.pepper.robot import Photo


@pytest.fixture
def api_server(mock_robot, mock_ai_manager, tmp_path):
    (tmp_path / "index.html").write_text("<html>ui</html>")
    return APIServer(host="127.0.0.1", port=8000, ai_manager=mock_ai_manager, robot=mock_robot, web_dir=tmp_path)


@pytest_asyncio.fixture
async def client(api_server):
    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestAPIServer:

    async def test_root_serves_ui(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "ui" in resp.text

    async def test_health(self, client, mock_robot):
        mock_robot.connection.health_check = AsyncMock(return_value={"status": "connected", "version": "2.1.0"})
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    async def test_status(self, client):
        resp = await client.get("/status")
        data = resp.json()
        assert resp.status_code == 200
        assert data["robot_state"]["battery_level"] == 80
        assert data["robot_state"]["awake"] is True
        assert "sensors" in data and data["busy"] is False

    async def test_chat(self, client, mock_ai_manager):
        mock_ai_manager.process_user_input = AsyncMock(return_value={"text": "Hello!", "tool_calls": [], "model": "m"})
        resp = await client.post("/chat", json={"message": "Hi", "speak": False})
        assert resp.status_code == 200
        assert resp.json()["text"] == "Hello!"
        mock_ai_manager.process_user_input.assert_called_once_with("Hi", speak=False, client_id=None)

    async def test_chat_empty(self, client):
        assert (await client.post("/chat", json={"message": "  "})).status_code == 400

    async def test_tools(self, client):
        resp = await client.get("/tools")
        assert len(resp.json()["tools"]) == len(TOOLS)

    async def test_command_speak(self, client, mock_robot):
        resp = await client.post("/command/speak", json={"params": {"text": "Hello"}})
        assert resp.status_code == 200 and resp.json()["success"] is True
        mock_robot.connection.bridge.speak.assert_called_once_with("Hello", language=None, animated=True)

    async def test_command_without_body(self, client, mock_robot):
        resp = await client.post("/command/wake_up")
        assert resp.json()["success"] is True
        mock_robot.connection.bridge.wake_up.assert_called_once()

    async def test_command_unknown(self, client):
        resp = await client.post("/command/fly", json={"params": {}})
        assert resp.status_code == 200 and resp.json()["success"] is False
        assert "known" in resp.json()

    async def test_command_bridge_error(self, client, mock_robot):
        mock_robot.connection.bridge.rest = AsyncMock(side_effect=BridgeError("motors hot"))
        resp = await client.post("/command/rest")
        assert resp.json() == {"success": False, "command": "rest", "error": "motors hot"}

    async def test_command_photo_returns_image(self, client):
        resp = await client.post("/command/photo")
        assert resp.json()["result"]["base64"] == "base64data"

    async def test_photo_endpoints(self, client, mock_ai_manager, mock_robot):
        assert (await client.get("/photo/latest")).status_code == 404
        resp = await client.post("/photo")
        assert resp.status_code == 200 and resp.json()["width"] == 640
        mock_robot.last_photo = Photo("image/jpeg", base64.b64encode(b"JPEGDATA").decode(), 1, 1)
        latest = await client.get("/photo/latest")
        assert latest.status_code == 200 and latest.content == b"JPEGDATA"
        assert latest.headers["content-type"] == "image/jpeg"

    async def test_conversation_history(self, client, mock_ai_manager):
        await client.post("/chat", json={"message": "Hello"})
        resp = await client.get("/conversation/history")
        assert resp.status_code == 200 and len(resp.json()["history"]) == 2
        assert (await client.delete("/conversation/history")).json()["success"] is True
        assert (await client.get("/conversation/history")).json()["history"] == []

    async def test_voice_endpoints_without_a_backend(self, client):
        assert (await client.get("/voice/status")).json()["enabled"] is False
        resp = await client.post("/voice/utterance", content=b"\x00" * 6400)
        assert resp.status_code == 503

    async def test_voice_utterance(self, mock_robot, mock_ai_manager, tmp_path):
        voice = VoiceInput(mock_ai_manager, FakeTranscriber(["wave"]))
        server = APIServer(
            host="127.0.0.1", port=8000, ai_manager=mock_ai_manager, robot=mock_robot, web_dir=tmp_path, voice=voice
        )
        seen = []

        async def cb(payload):
            seen.append(payload)

        voice.on_transcript(cb)
        async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as c:
            status = (await c.get("/voice/status")).json()
            assert status["enabled"] is True and status["backend"] == "fake"
            resp = await c.post(
                "/voice/utterance?client_id=abc", content=tone(0.5), headers={"Content-Type": "audio/pcm"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["text"] == "wave" and data["response"]["text"] == "Hello! I'm Pepper."
            assert data["response"]["source"] == "voice" and data["response"]["client_id"] == "abc"
            assert seen[-1]["delivered"] is True and seen[-1]["client_id"] == "abc" and seen[-1]["source"] == "ptt"
            resp = await c.post("/voice/utterance", content=b"\x00" * 10)
            assert resp.status_code == 400
            resp = await c.post("/voice/utterance?sample_rate=0", content=tone(0.5))
            assert resp.status_code == 422
            resp = await c.post("/voice/utterance", content=b"\x00" * (16000 * 2 * 61))
            assert resp.status_code == 413

    async def test_voice_utterance_recognition_failure_is_an_error(self, mock_robot, mock_ai_manager, tmp_path):
        transcriber = FakeTranscriber()
        transcriber.transcribe = AsyncMock(side_effect=RuntimeError("model missing"))
        voice = VoiceInput(mock_ai_manager, transcriber)
        server = APIServer(
            host="127.0.0.1", port=8000, ai_manager=mock_ai_manager, robot=mock_robot, web_dir=tmp_path, voice=voice
        )
        async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as c:
            resp = await c.post("/voice/utterance", content=tone(0.5))
            assert resp.status_code == 502 and "model missing" in resp.json()["detail"]

    async def test_hub_broadcasts_responses(self, api_server, mock_ai_manager):
        sent = []

        class FakeWS:
            async def send_text(self, msg):
                sent.append(msg)

        ws = FakeWS()
        api_server.hub.clients.add(ws)
        await mock_ai_manager.process_user_input("Hello")
        assert any('"type": "chat_response"' in m for m in sent)
        await api_server.hub.on_robot_event("touch", {"sensor": "head_front", "touched": True})
        assert any('"type": "robot_event"' in m and "head_front" in m for m in sent)


class TestExecuteCommand:

    async def test_prepare_passes_options(self, mock_robot):
        result = await execute_command(mock_robot, "prepare", {"autonomous_life": "disabled", "posture": "Stand"})
        assert result["success"] is True
        mock_robot.connection.bridge.prepare.assert_called_once_with(
            autonomous_life="disabled", wake_up=True, posture="Stand", awareness=None
        )

    async def test_refresh_state(self, mock_robot):
        result = await execute_command(mock_robot, "refresh_state", {})
        assert result["result"]["posture"] == "Stand"
