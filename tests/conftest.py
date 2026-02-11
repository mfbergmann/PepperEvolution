"""
Pytest configuration and fixtures for PepperEvolution v2 tests.

Uses respx to mock HTTP calls to the bridge server.
No NAOqi or MockQi needed.
"""

import pytest
import pytest_asyncio
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock

from src.pepper.connection import ConnectionConfig, PepperConnection
from src.pepper.bridge_client import BridgeClient
from src.pepper.robot import PepperRobot
from src.ai.models import AnthropicProvider, OpenAIProvider, AIResponse, ToolCall
from src.ai.manager import AIManager
from src.ai.tool_executor import ToolExecutor


BRIDGE_BASE = "http://10.0.100.100:8888"


@pytest.fixture
def connection_config():
    return ConnectionConfig(ip="10.0.100.100", bridge_port=8888)


@pytest.fixture
def bridge_client(connection_config):
    return BridgeClient(base_url=connection_config.base_url)


@pytest_asyncio.fixture
async def connected_bridge_client(bridge_client):
    """A BridgeClient that has been connected (has an httpx client)."""
    await bridge_client.connect()
    yield bridge_client
    await bridge_client.close()


@pytest.fixture
def mock_connection(connection_config):
    """PepperConnection with mocked bridge and events."""
    conn = PepperConnection(connection_config)
    conn.connected = True
    # Replace bridge with a mock
    conn.bridge = AsyncMock(spec=BridgeClient)
    conn.bridge.health = AsyncMock(return_value={"ok": True, "version": "2.0.0"})
    conn.bridge.status = AsyncMock(return_value={
        "ok": True, "battery": 80, "posture": "Stand",
        "robot_name": "Pepper", "autonomous_life": "solitary",
    })
    conn.bridge.get_sensors = AsyncMock(return_value={
        "ok": True, "battery": 80,
        "touch": {"head_front": False, "head_middle": False, "head_rear": False,
                   "hand_left": False, "hand_right": False},
        "sonar": {"left": 1.5, "right": 1.2},
        "people_count": 0,
    })
    conn.bridge.speak = AsyncMock(return_value={"ok": True})
    conn.bridge.move_forward = AsyncMock(return_value={"ok": True})
    conn.bridge.move_turn = AsyncMock(return_value={"ok": True})
    conn.bridge.move_head = AsyncMock(return_value={"ok": True})
    conn.bridge.set_posture = AsyncMock(return_value={"ok": True})
    conn.bridge.take_picture = AsyncMock(return_value={
        "ok": True, "image": "base64data", "width": 640, "height": 480, "format": "jpeg",
    })
    conn.bridge.play_animation = AsyncMock(return_value={"ok": True})
    conn.bridge.set_eye_leds = AsyncMock(return_value={"ok": True})
    conn.bridge.set_chest_leds = AsyncMock(return_value={"ok": True})
    conn.bridge.emergency_stop = AsyncMock(return_value={"ok": True})
    conn.bridge.stop = AsyncMock(return_value={"ok": True})
    conn.bridge.wake_up = AsyncMock(return_value={"ok": True})
    conn.bridge.rest = AsyncMock(return_value={"ok": True})
    conn.bridge.set_volume = AsyncMock(return_value={"ok": True})
    conn.bridge.set_awareness = AsyncMock(return_value={"ok": True})
    conn.bridge.set_autonomous_life = AsyncMock(return_value={"ok": True})
    conn.bridge.record_audio = AsyncMock(return_value={"ok": True, "audio": "base64audio"})
    conn.bridge.move_to = AsyncMock(return_value={"ok": True})
    conn.bridge.close = AsyncMock()
    return conn


@pytest.fixture
def mock_robot(mock_connection):
    """PepperRobot with mocked connection."""
    robot = PepperRobot.__new__(PepperRobot)
    robot.connection = mock_connection
    robot.sensors = MagicMock()
    robot.sensors.get_all = AsyncMock(return_value={"battery": 80, "touch": {}, "sonar": {}})
    robot.actuators = MagicMock()
    robot.state = MagicMock()
    robot.state.battery_level = 80
    robot.state.posture = "Stand"
    robot.state.robot_name = "Pepper"
    robot.state.autonomous_life = "solitary"
    robot.state.is_connected = True
    robot.logger = MagicMock()
    robot._event_callbacks = []
    return robot


@pytest.fixture
def mock_ai_provider():
    """Mock AI provider that returns predictable responses."""
    provider = AsyncMock(spec=AnthropicProvider)
    provider.chat = AsyncMock(return_value=AIResponse(
        text="Hello! I'm Pepper.",
        tool_calls=[],
        stop_reason="end_turn",
        model="claude-sonnet-4-5-20250929",
    ))
    return provider


@pytest.fixture
def mock_ai_manager(mock_robot, mock_ai_provider):
    """AIManager with mocked robot and provider."""
    manager = AIManager(mock_robot, mock_ai_provider)
    return manager
