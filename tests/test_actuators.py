"""
Tests for ActuatorManager.
"""

import pytest
from unittest.mock import AsyncMock

from src.actuators.manager import ActuatorManager
from src.pepper.bridge_client import BridgeError


class TestActuatorManager:

    @pytest.fixture
    def actuator_manager(self, mock_connection):
        return ActuatorManager(mock_connection)

    @pytest.mark.asyncio
    async def test_speak(self, actuator_manager):
        assert await actuator_manager.speak("Hello") is True

    @pytest.mark.asyncio
    async def test_speak_failure(self, actuator_manager):
        actuator_manager.connection.bridge.speak = AsyncMock(side_effect=BridgeError("fail"))
        assert await actuator_manager.speak("Hello") is False

    @pytest.mark.asyncio
    async def test_move_forward(self, actuator_manager):
        assert await actuator_manager.move_forward(0.5) is True

    @pytest.mark.asyncio
    async def test_turn(self, actuator_manager):
        assert await actuator_manager.turn(90) is True

    @pytest.mark.asyncio
    async def test_move_head(self, actuator_manager):
        assert await actuator_manager.move_head(10, -5) is True

    @pytest.mark.asyncio
    async def test_set_posture(self, actuator_manager):
        assert await actuator_manager.set_posture("Stand") is True

    @pytest.mark.asyncio
    async def test_stop(self, actuator_manager):
        assert await actuator_manager.stop() is True

    @pytest.mark.asyncio
    async def test_emergency_stop(self, actuator_manager):
        assert await actuator_manager.emergency_stop() is True

    @pytest.mark.asyncio
    async def test_set_eye_color(self, actuator_manager):
        assert await actuator_manager.set_eye_color("blue") is True

    @pytest.mark.asyncio
    async def test_set_chest_led(self, actuator_manager):
        assert await actuator_manager.set_chest_led("red") is True

    @pytest.mark.asyncio
    async def test_play_animation(self, actuator_manager):
        assert await actuator_manager.play_animation("animations/Stand/Gestures/Hey_1") is True

    @pytest.mark.asyncio
    async def test_wake_up(self, actuator_manager):
        assert await actuator_manager.wake_up() is True

    @pytest.mark.asyncio
    async def test_rest(self, actuator_manager):
        assert await actuator_manager.rest() is True

    @pytest.mark.asyncio
    async def test_set_volume(self, actuator_manager):
        assert await actuator_manager.set_volume(75) is True

    @pytest.mark.asyncio
    async def test_set_awareness(self, actuator_manager):
        assert await actuator_manager.set_awareness(True) is True

    @pytest.mark.asyncio
    async def test_take_picture(self, actuator_manager):
        result = await actuator_manager.take_picture()
        assert result["image"] == "base64data"

    @pytest.mark.asyncio
    async def test_record_audio(self, actuator_manager):
        result = await actuator_manager.record_audio(3.0)
        assert result["audio"] == "base64audio"
