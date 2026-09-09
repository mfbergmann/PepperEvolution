"""
Tests for ToolExecutor - dispatches AI tool calls to the robot.
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.ai.tool_executor import ToolExecutor, ToolOutcome
from src.pepper.bridge_client import BridgeError
from src.pepper.robot import Photo


class TestToolOutcome:

    def test_summary_and_result(self):
        outcome = ToolOutcome(True, {"spoken": "hi"})
        assert json.loads(outcome.summary()) == {"success": True, "spoken": "hi"}
        result = outcome.tool_result("t1")
        assert result["tool_use_id"] == "t1"
        assert "is_error" not in result
        assert isinstance(result["content"], str)

    def test_failure_is_error(self):
        result = ToolOutcome.failure("boom").tool_result("t2")
        assert result["is_error"] is True
        assert "boom" in result["content"]

    def test_image_result_contains_image_block(self):
        photo = Photo("image/jpeg", "QUJD", 640, 480)
        result = ToolOutcome(True, {"width": 640}, image=photo).tool_result("t3")
        blocks = result["content"]
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
        assert blocks[1]["type"] == "text"


class TestToolExecutor:

    @pytest.fixture
    def executor(self, mock_robot):
        return ToolExecutor(mock_robot)

    async def test_speak(self, executor, mock_robot):
        outcome = await executor.execute("speak", {"text": "Hello"})
        assert outcome.ok and outcome.data["spoken"] == "Hello"
        mock_robot.connection.bridge.speak.assert_called_once_with("Hello", language=None, animated=True, wait=True)

    async def test_speak_with_language_not_animated(self, executor, mock_robot):
        await executor.execute("speak", {"text": "Bonjour", "animated": False, "language": "French"})
        mock_robot.connection.bridge.speak.assert_called_once_with(
            "Bonjour", language="French", animated=False, wait=True
        )

    async def test_speak_empty(self, executor):
        outcome = await executor.execute("speak", {"text": ""})
        assert not outcome.ok

    async def test_move_forward(self, executor, mock_robot):
        outcome = await executor.execute("move_forward", {"distance": 1.0})
        assert outcome.ok and outcome.data["distance"] == 1.0
        mock_robot.connection.bridge.move_forward.assert_called_once_with(1.0, 0.3)

    async def test_move_forward_clamped(self, executor):
        outcome = await executor.execute("move_forward", {"distance": 10.0, "speed": 3})
        assert outcome.data["distance"] == 2.0
        assert outcome.data["speed"] == 0.5

    async def test_move_forward_refuses_when_obstacle(self, executor, mock_robot):
        mock_robot.sensors.get_all = AsyncMock(return_value={"sonar": {"front": 0.2, "back": 2.0}})
        outcome = await executor.execute("move_forward", {"distance": 1.0})
        assert not outcome.ok and "obstacle" in outcome.data["error"]
        mock_robot.connection.bridge.move_forward.assert_not_called()

    async def test_move_backward_checks_back_sonar(self, executor, mock_robot):
        mock_robot.sensors.get_all = AsyncMock(return_value={"sonar": {"front": 0.2, "back": 2.0}})
        outcome = await executor.execute("move_forward", {"distance": -0.5})
        assert outcome.ok
        mock_robot.connection.bridge.move_forward.assert_called_once()

    async def test_turn_clamped(self, executor):
        outcome = await executor.execute("turn", {"angle": 360})
        assert outcome.data["angle"] == 180

    async def test_move_head(self, executor, mock_robot):
        outcome = await executor.execute("move_head", {"yaw": 30, "pitch": -10})
        assert outcome.ok
        mock_robot.connection.bridge.move_head.assert_called_once_with(30.0, -10.0, 0.2)

    async def test_set_posture_invalid(self, executor):
        outcome = await executor.execute("set_posture", {"posture": "Handstand"})
        assert not outcome.ok

    async def test_play_animation_exact(self, executor, mock_robot):
        outcome = await executor.execute("play_animation", {"name": "animations/Stand/Gestures/Hey_1"})
        assert outcome.ok and outcome.data["meaning"] == "wave hello"

    async def test_play_animation_short_name_resolves(self, executor, mock_robot):
        outcome = await executor.execute("play_animation", {"name": "BowShort_1"})
        assert outcome.ok
        mock_robot.connection.bridge.play_animation.assert_called_once_with("animations/Stand/Gestures/BowShort_1")

    async def test_play_animation_unknown_suggests(self, executor, mock_robot):
        outcome = await executor.execute("play_animation", {"name": "animations/Stand/Gestures/Hey_9"})
        assert not outcome.ok
        assert "Hey_1" in outcome.data["error"]
        mock_robot.connection.bridge.play_animation.assert_not_called()

    async def test_set_eye_color(self, executor, mock_robot):
        outcome = await executor.execute("set_eye_color", {"color": "Blue"})
        assert outcome.ok
        mock_robot.connection.bridge.set_eye_leds.assert_called_once_with(color="blue")

    async def test_set_eye_color_invalid(self, executor):
        assert not (await executor.execute("set_eye_color", {"color": "plaid"})).ok

    async def test_take_photo_returns_image(self, executor, mock_robot):
        outcome = await executor.execute("take_photo", {})
        assert outcome.ok
        assert outcome.image is not None
        assert outcome.image.media_type == "image/jpeg"
        assert outcome.image.base64_data == "base64data"
        assert outcome.data["camera"] == "forehead"
        assert mock_robot.last_photo is outcome.image

    async def test_take_photo_failure(self, executor, mock_robot):
        mock_robot.connection.bridge.take_picture = AsyncMock(side_effect=BridgeError("camera busy"))
        outcome = await executor.execute("take_photo", {})
        assert not outcome.ok and "camera busy" in outcome.data["error"]

    async def test_get_sensors(self, executor):
        outcome = await executor.execute("get_sensors", {})
        assert outcome.ok and outcome.data["battery"] == 80

    async def test_show_on_tablet_text_and_url(self, executor, mock_robot):
        assert (await executor.execute("show_on_tablet", {"text": "Hi"})).data["shown"] == "text"
        assert (await executor.execute("show_on_tablet", {"url": "https://example.com"})).data["shown"] == "web"
        assert not (await executor.execute("show_on_tablet", {})).ok

    async def test_emergency_stop(self, executor, mock_robot):
        outcome = await executor.execute("emergency_stop", {})
        assert outcome.ok
        mock_robot.connection.bridge.emergency_stop.assert_called_once()

    async def test_unknown_tool(self, executor):
        outcome = await executor.execute("fly_to_moon", {})
        assert not outcome.ok and "Unknown tool" in outcome.data["error"]

    async def test_execution_error(self, executor, mock_robot):
        mock_robot.connection.bridge.speak = AsyncMock(side_effect=BridgeError("boom"))
        outcome = await executor.execute("speak", {"text": "test"})
        assert not outcome.ok and "boom" in outcome.data["error"]

    def test_clamp(self):
        assert ToolExecutor._clamp(5, 0, 10) == 5
        assert ToolExecutor._clamp(-5, 0, 10) == 0
        assert ToolExecutor._clamp(15, 0, 10) == 10
        assert ToolExecutor._clamp("abc", 0, 10) == 0
        assert ToolExecutor._clamp(None, 0, 10) == 0

    async def test_move_head_pitch_clamped_to_pepper_range(self, executor, mock_robot):
        outcome = await executor.execute("move_head", {"yaw": 0, "pitch": 60})
        assert outcome.data["pitch"] == 25.0
