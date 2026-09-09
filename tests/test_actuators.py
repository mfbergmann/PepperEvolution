"""
Tests for ActuatorManager.
"""

from unittest.mock import AsyncMock

import pytest

from src.actuators.manager import ActuatorManager
from src.pepper.bridge_client import BridgeError


class TestActuatorManager:

    @pytest.fixture
    def actuator_manager(self, mock_connection):
        return ActuatorManager(mock_connection)

    async def test_speak(self, actuator_manager):
        assert await actuator_manager.speak("Hello") is True

    async def test_speak_failure(self, actuator_manager):
        actuator_manager.connection.bridge.speak = AsyncMock(side_effect=BridgeError("fail"))
        assert await actuator_manager.speak("Hello") is False

    @pytest.mark.parametrize(
        "method,args",
        [
            ("move_forward", (0.5,)),
            ("turn", (90,)),
            ("move_head", (10, -5)),
            ("move_to", (0.5, 0.0)),
            ("set_posture", ("Stand",)),
            ("stop", ()),
            ("emergency_stop", ()),
            ("set_eye_color", ("blue",)),
            ("set_chest_led", ("red",)),
            ("play_animation", ("animations/Stand/Gestures/Hey_1",)),
            ("wake_up", ()),
            ("rest", ()),
            ("set_volume", (75,)),
            ("set_awareness", (True,)),
            ("set_autonomous_life", ("disabled",)),
            ("tablet_text", ("hi",)),
            ("tablet_hide", ()),
        ],
    )
    async def test_actions_return_true(self, actuator_manager, method, args):
        assert await getattr(actuator_manager, method)(*args) is True

    async def test_take_picture(self, actuator_manager):
        result = await actuator_manager.take_picture()
        assert result["image"] == "base64data"

    async def test_take_picture_failure(self, actuator_manager):
        actuator_manager.connection.bridge.take_picture = AsyncMock(side_effect=BridgeError("fail"))
        assert await actuator_manager.take_picture() is None

    async def test_record_audio(self, actuator_manager):
        result = await actuator_manager.record_audio(3.0)
        assert result["audio"] == "base64audio"
