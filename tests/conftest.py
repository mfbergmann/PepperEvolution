"""
Pytest configuration and fixtures for PepperEvolution v2 tests.

Two layers of doubles are available:
- ``mock_connection`` / ``mock_robot``: AsyncMock-based, for unit tests that
  assert on exact bridge calls.
- ``fake_robot``: a real PepperRobot wired to FakeBridgeClient, for tests that
  exercise the whole host stack without a robot.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.ai.manager import AIManager
from src.ai.models import AIResponse, AnthropicProvider
from src.pepper.bridge_client import BridgeClient
from src.pepper.connection import ConnectionConfig, PepperConnection
from src.pepper.fake_bridge import FakeBridgeClient
from src.pepper.robot import PepperRobot, RobotState

BRIDGE_BASE = "http://10.0.100.100:8888"

SENSORS = {
    "ok": True,
    "battery": 80,
    "charging": False,
    "touch": {"head_front": False, "head_middle": False, "head_rear": False, "hand_left": False, "hand_right": False},
    "bumpers": {"front_left": False, "front_right": False, "back": False},
    "sonar": {"front": 1.5, "back": 1.2},
    "obstacle": False,
    "people_count": 0,
    "people_ids": [],
}


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
    """PepperConnection with a mocked bridge and no event stream."""
    conn = PepperConnection(connection_config, events=False)
    conn.connected = True
    bridge = AsyncMock(spec=BridgeClient)
    bridge.health = AsyncMock(return_value={"ok": True, "version": "2.1.0"})
    bridge.status = AsyncMock(
        return_value={
            "ok": True,
            "battery": 80,
            "charging": False,
            "posture": "Stand",
            "robot_name": "Pepper",
            "autonomous_life": "disabled",
            "awake": True,
            "language": "English",
            "volume": 60,
        }
    )
    bridge.get_sensors = AsyncMock(return_value=dict(SENSORS))
    bridge.speak = AsyncMock(return_value={"ok": True, "duration": 1.2})
    bridge.stop_speaking = AsyncMock(return_value={"ok": True})
    bridge.move_forward = AsyncMock(return_value={"ok": True})
    bridge.move_turn = AsyncMock(return_value={"ok": True})
    bridge.move_head = AsyncMock(return_value={"ok": True})
    bridge.move_to = AsyncMock(return_value={"ok": True})
    bridge.set_posture = AsyncMock(return_value={"ok": True})
    bridge.take_picture = AsyncMock(
        return_value={"ok": True, "image": "base64data", "width": 640, "height": 480, "format": "jpeg"}
    )
    bridge.play_animation = AsyncMock(return_value={"ok": True})
    bridge.list_animations = AsyncMock(return_value=["animations/Stand/Gestures/Hey_1"])
    bridge.set_eye_leds = AsyncMock(return_value={"ok": True})
    bridge.set_chest_leds = AsyncMock(return_value={"ok": True})
    bridge.emergency_stop = AsyncMock(return_value={"ok": True})
    bridge.stop = AsyncMock(return_value={"ok": True})
    bridge.wake_up = AsyncMock(return_value={"ok": True})
    bridge.rest = AsyncMock(return_value={"ok": True})
    bridge.prepare = AsyncMock(return_value={"ok": True})
    bridge.set_volume = AsyncMock(return_value={"ok": True})
    bridge.set_awareness = AsyncMock(return_value={"ok": True})
    bridge.set_autonomous_life = AsyncMock(return_value={"ok": True})
    bridge.record_audio = AsyncMock(return_value={"ok": True, "audio": "base64audio"})
    bridge.tablet_text = AsyncMock(return_value={"ok": True})
    bridge.tablet_web = AsyncMock(return_value={"ok": True})
    bridge.tablet_image = AsyncMock(return_value={"ok": True})
    bridge.tablet_hide = AsyncMock(return_value={"ok": True})
    bridge.close = AsyncMock()
    bridge.connect = AsyncMock()
    conn.bridge = bridge
    return conn


@pytest.fixture
def mock_robot(mock_connection):
    """PepperRobot with mocked connection (robot methods are real, bridge is mocked)."""
    robot = PepperRobot.__new__(PepperRobot)
    robot.connection = mock_connection
    robot.sensors = MagicMock()
    robot.sensors.get_all = AsyncMock(return_value={"battery": 80, "touch": {}, "sonar": {"front": 1.5, "back": 1.2}})
    robot.actuators = MagicMock()
    robot.state = RobotState(
        battery_level=80,
        posture="Stand",
        robot_name="Pepper",
        autonomous_life="disabled",
        awake=True,
        language="English",
        is_connected=True,
    )
    robot.animations = ["animations/Stand/Gestures/Hey_1", "animations/Stand/Gestures/BowShort_1"]
    robot.last_photo = None
    robot.halted = False
    robot.direct_commands_running = 0
    robot.photo_resolution = 2
    robot.last_prepare = {}
    robot.last_eye_color = None
    robot.logger = MagicMock()
    robot._event_callbacks = []
    robot._state_task = None
    return robot


@pytest.fixture
def fake_bridge():
    bridge = FakeBridgeClient()
    bridge.speech_delay = 0
    return bridge


@pytest_asyncio.fixture
async def fake_robot(fake_bridge):
    """A real PepperRobot talking to the in-memory FakeBridgeClient."""
    robot = PepperRobot(ConnectionConfig(ip="fake"), bridge=fake_bridge)
    assert await robot.initialize()
    yield robot
    await robot.shutdown()


@pytest.fixture
def mock_ai_provider():
    """Mock AI provider that returns predictable responses."""
    provider = AsyncMock(spec=AnthropicProvider)
    provider.model = "claude-opus-5"
    provider.chat = AsyncMock(
        return_value=AIResponse(text="Hello! I'm Pepper.", tool_calls=[], stop_reason="end_turn", model="claude-opus-5")
    )
    return provider


@pytest.fixture
def mock_ai_manager(mock_robot, mock_ai_provider):
    """AIManager with mocked robot and provider; speech off so tests stay fast."""
    return AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
