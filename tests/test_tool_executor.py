"""
Tests for ToolExecutor - dispatches AI tool calls to the robot.
"""

import json
import pytest
from unittest.mock import AsyncMock

from src.ai.tool_executor import ToolExecutor


class TestToolExecutor:

    @pytest.fixture
    def executor(self, mock_robot):
        return ToolExecutor(mock_robot)

    @pytest.mark.asyncio
    async def test_speak(self, executor, mock_robot):
        mock_robot.speak = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("speak", {"text": "Hello"}))
        assert result["success"] is True
        assert result["spoken"] == "Hello"
        mock_robot.speak.assert_called_once_with("Hello", animated=False)

    @pytest.mark.asyncio
    async def test_speak_animated(self, executor, mock_robot):
        mock_robot.speak = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("speak", {"text": "Hi!", "animated": True}))
        assert result["success"] is True
        mock_robot.speak.assert_called_once_with("Hi!", animated=True)

    @pytest.mark.asyncio
    async def test_speak_empty(self, executor):
        result = json.loads(await executor.execute("speak", {"text": ""}))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_move_forward(self, executor, mock_robot):
        mock_robot.move_forward = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("move_forward", {"distance": 1.0}))
        assert result["success"] is True
        assert result["distance"] == 1.0

    @pytest.mark.asyncio
    async def test_move_forward_clamped(self, executor, mock_robot):
        mock_robot.move_forward = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("move_forward", {"distance": 10.0}))
        assert result["distance"] == 2.0  # clamped

    @pytest.mark.asyncio
    async def test_turn(self, executor, mock_robot):
        mock_robot.turn = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("turn", {"angle": 90}))
        assert result["success"] is True
        assert result["angle"] == 90

    @pytest.mark.asyncio
    async def test_turn_clamped(self, executor, mock_robot):
        mock_robot.turn = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("turn", {"angle": 360}))
        assert result["angle"] == 180  # clamped

    @pytest.mark.asyncio
    async def test_move_head(self, executor, mock_robot):
        mock_robot.move_head = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("move_head", {"yaw": 30, "pitch": -10}))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_set_posture(self, executor, mock_robot):
        mock_robot.set_posture = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("set_posture", {"posture": "Stand"}))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_set_posture_invalid(self, executor):
        result = json.loads(await executor.execute("set_posture", {"posture": "Handstand"}))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_play_animation(self, executor, mock_robot):
        mock_robot.play_animation = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("play_animation", {"name": "animations/Stand/Gestures/Hey_1"}))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_set_eye_color(self, executor, mock_robot):
        mock_robot.set_eye_color = AsyncMock(return_value=True)
        result = json.loads(await executor.execute("set_eye_color", {"color": "blue"}))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_take_photo(self, executor, mock_robot):
        mock_robot.take_picture = AsyncMock(return_value={
            "image": "base64data123", "width": 640, "height": 480
        })
        result = json.loads(await executor.execute("take_photo", {}))
        assert result["success"] is True
        assert result["width"] == 640

    @pytest.mark.asyncio
    async def test_take_photo_failure(self, executor, mock_robot):
        mock_robot.take_picture = AsyncMock(return_value=None)
        result = json.loads(await executor.execute("take_photo", {}))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_get_sensors(self, executor, mock_robot):
        mock_robot.get_sensors = AsyncMock(return_value={"battery": 80, "touch": {}, "sonar": {}})
        result = json.loads(await executor.execute("get_sensors", {}))
        assert result["success"] is True
        assert result["battery"] == 80

    @pytest.mark.asyncio
    async def test_emergency_stop(self, executor, mock_robot):
        mock_robot.emergency_stop = AsyncMock()
        result = json.loads(await executor.execute("emergency_stop", {}))
        assert result["success"] is True
        mock_robot.emergency_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_tool(self, executor):
        result = json.loads(await executor.execute("fly_to_moon", {}))
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_execution_error(self, executor, mock_robot):
        mock_robot.speak = AsyncMock(side_effect=Exception("boom"))
        result = json.loads(await executor.execute("speak", {"text": "test"}))
        assert result["success"] is False
        assert "boom" in result["error"]

    def test_clamp(self):
        assert ToolExecutor._clamp(5, 0, 10) == 5
        assert ToolExecutor._clamp(-5, 0, 10) == 0
        assert ToolExecutor._clamp(15, 0, 10) == 10
        assert ToolExecutor._clamp("abc", 0, 10) == 0
        assert ToolExecutor._clamp(None, 0, 10) == 0
