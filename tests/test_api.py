"""
Tests for the FastAPI server.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from src.communication.api import APIServer
from src.ai.manager import AIManager
from src.ai.tools import TOOLS


@pytest.fixture
def api_server(mock_robot, mock_ai_manager):
    server = APIServer(
        host="127.0.0.1",
        port=8000,
        ai_manager=mock_ai_manager,
        robot=mock_robot,
    )
    return server


@pytest_asyncio.fixture
async def client(api_server):
    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestAPIServer:

    @pytest.mark.asyncio
    async def test_root(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "PepperEvolution"

    @pytest.mark.asyncio
    async def test_health(self, client, mock_robot):
        mock_robot.connection.health_check = AsyncMock(return_value={"status": "connected", "version": "2.0.0"})
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_status(self, client, mock_robot):
        mock_robot.get_sensors = AsyncMock(return_value={"battery": 80})
        resp = await client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "robot_state" in data
        assert "sensors" in data

    @pytest.mark.asyncio
    async def test_chat(self, client, mock_ai_manager):
        mock_ai_manager.process_user_input = AsyncMock(return_value={
            "text": "Hello!", "tool_calls": [], "model": "test"
        })
        resp = await client.post("/chat", json={"message": "Hi"})
        assert resp.status_code == 200
        assert resp.json()["text"] == "Hello!"

    @pytest.mark.asyncio
    async def test_tools(self, client):
        resp = await client.get("/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == len(TOOLS)

    @pytest.mark.asyncio
    async def test_command_speak(self, client, mock_robot):
        mock_robot.connection.bridge.speak = AsyncMock(return_value={"ok": True})
        resp = await client.post("/command/speak", json={"params": {"text": "Hello"}})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_command_unknown(self, client):
        resp = await client.post("/command/fly", json={"params": {}})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    @pytest.mark.asyncio
    async def test_conversation_history(self, client, mock_ai_manager):
        mock_ai_manager.process_user_input = AsyncMock(return_value={
            "text": "Hi!", "tool_calls": [], "model": "test"
        })
        await client.post("/chat", json={"message": "Hello"})
        resp = await client.get("/conversation/history")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_clear_history(self, client, mock_ai_manager):
        resp = await client.delete("/conversation/history")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
