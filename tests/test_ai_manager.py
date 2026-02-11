"""
Tests for AIManager - multi-turn tool-calling conversation loop.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall


class TestAIManager:

    @pytest.mark.asyncio
    async def test_simple_text_response(self, mock_ai_manager):
        result = await mock_ai_manager.process_user_input("Hello")
        assert result["text"] == "Hello! I'm Pepper."
        assert result["tool_calls"] == []
        assert len(mock_ai_manager.conversation_history) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self, mock_ai_manager, mock_ai_provider):
        """AI calls a tool, then responds with text."""
        # First call: tool use
        tool_response = AIResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="speak", input={"text": "Hello!"})],
            stop_reason="tool_use",
            model="claude-sonnet-4-5-20250929",
        )
        # Second call: text
        text_response = AIResponse(
            text="I just said hello!",
            tool_calls=[],
            stop_reason="end_turn",
            model="claude-sonnet-4-5-20250929",
        )
        mock_ai_provider.chat = AsyncMock(side_effect=[tool_response, text_response])

        # Mock the robot methods the executor will call
        mock_ai_manager.robot.speak = AsyncMock(return_value=True)

        result = await mock_ai_manager.process_user_input("Say hello")
        assert result["text"] == "I just said hello!"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "speak"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, mock_ai_manager, mock_ai_provider):
        """AI calls multiple tools in sequence."""
        responses = [
            AIResponse(
                text="",
                tool_calls=[ToolCall(id="t1", name="speak", input={"text": "Hi!"})],
                stop_reason="tool_use",
                model="test",
            ),
            AIResponse(
                text="",
                tool_calls=[ToolCall(id="t2", name="set_eye_color", input={"color": "blue"})],
                stop_reason="tool_use",
                model="test",
            ),
            AIResponse(
                text="Done! I said hi and set my eyes blue.",
                tool_calls=[],
                stop_reason="end_turn",
                model="test",
            ),
        ]
        mock_ai_provider.chat = AsyncMock(side_effect=responses)
        mock_ai_manager.robot.speak = AsyncMock(return_value=True)
        mock_ai_manager.robot.set_eye_color = AsyncMock(return_value=True)

        result = await mock_ai_manager.process_user_input("Say hi and set eyes blue")
        assert len(result["tool_calls"]) == 2

    @pytest.mark.asyncio
    async def test_max_tool_rounds(self, mock_ai_manager, mock_ai_provider):
        """Safety: stop after MAX_TOOL_ROUNDS."""
        mock_ai_manager.MAX_TOOL_ROUNDS = 3
        # Always return a tool call
        infinite_tool = AIResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="get_sensors", input={})],
            stop_reason="tool_use",
            model="test",
        )
        mock_ai_provider.chat = AsyncMock(return_value=infinite_tool)
        mock_ai_manager.robot.get_sensors = AsyncMock(return_value={"battery": 80})

        result = await mock_ai_manager.process_user_input("Loop forever")
        assert "carried away" in result["text"]
        assert len(result["tool_calls"]) == 3

    @pytest.mark.asyncio
    async def test_conversation_history_management(self, mock_ai_manager):
        for i in range(5):
            await mock_ai_manager.process_user_input(f"Message {i}")
        assert len(mock_ai_manager.conversation_history) == 10  # 5 user + 5 assistant

    @pytest.mark.asyncio
    async def test_clear_history(self, mock_ai_manager):
        await mock_ai_manager.process_user_input("Hello")
        assert len(mock_ai_manager.conversation_history) > 0
        mock_ai_manager.clear_conversation_history()
        assert len(mock_ai_manager.conversation_history) == 0

    @pytest.mark.asyncio
    async def test_get_history(self, mock_ai_manager):
        await mock_ai_manager.process_user_input("Hello")
        history = mock_ai_manager.get_conversation_history()
        assert len(history) == 2
        # Should be a copy
        mock_ai_manager.clear_conversation_history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_response_callback(self, mock_ai_manager):
        callback = AsyncMock()
        mock_ai_manager.on_response(callback)
        await mock_ai_manager.process_user_input("Hello")
        callback.assert_called_once()
