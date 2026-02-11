"""
Tests for AI model providers.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.ai.models import (
    AnthropicProvider,
    OpenAIProvider,
    AIResponse,
    ToolCall,
    SYSTEM_PROMPT,
)


class TestAIResponse:

    def test_defaults(self):
        r = AIResponse()
        assert r.text == ""
        assert r.tool_calls == []
        assert r.stop_reason == ""

    def test_with_tool_calls(self):
        tc = ToolCall(id="1", name="speak", input={"text": "hi"})
        r = AIResponse(text="", tool_calls=[tc], stop_reason="tool_use")
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "speak"


class TestAnthropicProvider:

    @pytest.mark.asyncio
    async def test_chat_text_response(self):
        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.api_key = "test"
        provider.model = "claude-sonnet-4-5-20250929"
        provider.logger = MagicMock()

        # Mock the client
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello there!"

        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_resp.stop_reason = "end_turn"
        mock_resp.model = "claude-sonnet-4-5-20250929"

        provider.client = MagicMock()
        provider.client.messages = MagicMock()
        provider.client.messages.create = AsyncMock(return_value=mock_resp)

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="Be helpful",
        )
        assert result.text == "Hello there!"
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_chat_tool_call(self):
        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.api_key = "test"
        provider.model = "claude-sonnet-4-5-20250929"
        provider.logger = MagicMock()

        # Mock tool use block
        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.id = "tool_123"
        mock_tool_block.name = "speak"
        mock_tool_block.input = {"text": "Hello!"}

        mock_resp = MagicMock()
        mock_resp.content = [mock_tool_block]
        mock_resp.stop_reason = "tool_use"
        mock_resp.model = "claude-sonnet-4-5-20250929"

        provider.client = MagicMock()
        provider.client.messages = MagicMock()
        provider.client.messages.create = AsyncMock(return_value=mock_resp)

        result = await provider.chat(
            messages=[{"role": "user", "content": "Say hello"}],
            tools=[{"name": "speak", "description": "speak", "input_schema": {"type": "object", "properties": {}}}],
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "speak"
        assert result.tool_calls[0].input == {"text": "Hello!"}

    @pytest.mark.asyncio
    async def test_chat_error(self):
        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.api_key = "test"
        provider.model = "claude-sonnet-4-5-20250929"
        provider.logger = MagicMock()

        provider.client = MagicMock()
        provider.client.messages = MagicMock()
        provider.client.messages.create = AsyncMock(side_effect=Exception("API down"))

        result = await provider.chat(messages=[{"role": "user", "content": "Hi"}])
        assert "error" in result.text.lower()
        assert result.stop_reason == "error"


class TestOpenAIProvider:

    @pytest.mark.asyncio
    async def test_chat_text_response(self):
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.api_key = "test"
        provider.model = "gpt-4o"
        provider.logger = MagicMock()

        mock_choice = MagicMock()
        mock_choice.message.content = "Hello!"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.model = "gpt-4o"

        provider.client = MagicMock()
        provider.client.chat = MagicMock()
        provider.client.chat.completions = MagicMock()
        provider.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="Be helpful",
        )
        assert result.text == "Hello!"
        assert result.tool_calls == []

    def test_convert_tools(self):
        tools = [
            {"name": "speak", "description": "Speak text", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}},
        ]
        converted = OpenAIProvider._convert_tools(tools)
        assert len(converted) == 1
        assert converted[0]["type"] == "function"
        assert converted[0]["function"]["name"] == "speak"


class TestSystemPrompt:

    def test_system_prompt_content(self):
        assert "Pepper" in SYSTEM_PROMPT
        assert "TRiPL Lab" in SYSTEM_PROMPT
        assert "speak" in SYSTEM_PROMPT.lower()
