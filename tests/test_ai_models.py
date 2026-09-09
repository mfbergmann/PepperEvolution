"""
Tests for AI model providers.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from src.ai.models import (
    DEFAULT_ANTHROPIC_MODEL,
    REFUSAL_TEXT,
    SYSTEM_PROMPT,
    AIResponse,
    AnthropicProvider,
    OpenAIProvider,
    ToolCall,
    supports_effort,
)


class TestAIResponse:

    def test_defaults(self):
        r = AIResponse()
        assert r.text == "" and r.tool_calls == [] and r.stop_reason == "" and not r.is_error

    def test_with_tool_calls(self):
        tc = ToolCall(id="1", name="speak", input={"text": "hi"})
        r = AIResponse(text="", tool_calls=[tc], stop_reason="tool_use")
        assert r.tool_calls[0].name == "speak"


class TestEffortSupport:

    def test_current_models_support_effort(self):
        assert supports_effort("claude-opus-5")
        assert supports_effort("claude-sonnet-5")
        assert supports_effort("claude-sonnet-4-6")
        assert supports_effort("claude-opus-4-7")

    def test_older_models_do_not(self):
        assert not supports_effort("claude-sonnet-4-5-20250929")
        assert not supports_effort("claude-haiku-4-5")
        assert not supports_effort("claude-3-5-sonnet-20241022")


def make_provider(model=DEFAULT_ANTHROPIC_MODEL, effort="low"):
    """Build an AnthropicProvider without a real client."""
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.api_key = "test"
    provider.model = model
    provider.logger = MagicMock()
    provider.effort = effort if effort and supports_effort(model) else None
    provider.max_tokens = 1024
    import anthropic

    provider._anthropic = anthropic
    provider.client = MagicMock()
    return provider


def fake_stream(events, final):
    """Return a messages.stream() replacement yielding events then a final message."""

    @asynccontextmanager
    async def _stream(**kwargs):
        class _S:
            def __aiter__(self):
                async def gen():
                    for e in events:
                        yield e

                return gen()

            async def get_final_message(self):
                return final

        yield _S()

    return _stream


def text_delta(t):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))


def message(content, stop_reason="end_turn", model="claude-opus-5"):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
        stop_details=None,
    )


class TestAnthropicProvider:

    async def test_chat_text_response_streams_on_text(self):
        provider = make_provider()
        captured = []
        final = message([SimpleNamespace(type="text", text="Hello there!")])
        provider.client.messages.stream = fake_stream([text_delta("Hello "), text_delta("there!")], final)

        async def on_text(t):
            captured.append(t)

        result = await provider.chat(messages=[{"role": "user", "content": "Hi"}], system="Be helpful", on_text=on_text)
        assert result.text == "Hello there!"
        assert captured == ["Hello ", "there!"]
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"
        assert result.usage["input_tokens"] == 10

    async def test_chat_tool_call(self):
        provider = make_provider()
        final = message(
            [SimpleNamespace(type="tool_use", id="tool_123", name="speak", input={"text": "Hello!"})],
            stop_reason="tool_use",
        )
        provider.client.messages.stream = fake_stream([], final)
        result = await provider.chat(messages=[{"role": "user", "content": "Say hello"}], tools=[{"name": "speak"}])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "speak"
        assert result.tool_calls[0].input == {"text": "Hello!"}

    async def test_request_kwargs(self):
        provider = make_provider(effort="medium")
        kwargs = provider._request_kwargs([{"role": "user", "content": "x"}], [{"name": "t"}], "sys")
        assert kwargs["model"] == DEFAULT_ANTHROPIC_MODEL
        assert kwargs["output_config"] == {"effort": "medium"}
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["tools"] == [{"name": "t"}]
        assert "thinking" not in kwargs  # adaptive by default on Opus 5

    async def test_no_effort_for_sonnet_45(self):
        provider = make_provider(model="claude-sonnet-4-5-20250929", effort="low")
        kwargs = provider._request_kwargs([{"role": "user", "content": "x"}], None, None)
        assert "output_config" not in kwargs

    async def test_refusal(self):
        provider = make_provider()
        final = message([], stop_reason="refusal")
        provider.client.messages.stream = fake_stream([], final)
        result = await provider.chat(messages=[{"role": "user", "content": "..."}])
        assert result.text == REFUSAL_TEXT and result.stop_reason == "refusal"

    async def test_chat_error(self):
        import anthropic

        provider = make_provider()

        @asynccontextmanager
        async def boom(**kwargs):
            raise anthropic.APIConnectionError(request=MagicMock())
            yield  # pragma: no cover

        provider.client.messages.stream = boom
        result = await provider.chat(messages=[{"role": "user", "content": "Hi"}])
        assert result.is_error
        assert result.usage["error"] == "connection"


class TestOpenAIProvider:

    def make(self):
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.api_key = "test"
        provider.model = "gpt-4o"
        provider.max_tokens = 1024
        provider.logger = MagicMock()
        provider.client = MagicMock()
        return provider

    async def test_chat_text_response(self):
        provider = self.make()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello!"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.model = "gpt-4o"
        provider.client.chat.completions.create = AsyncMock(return_value=mock_resp)
        got = []

        async def on_text(t):
            got.append(t)

        result = await provider.chat(messages=[{"role": "user", "content": "Hi"}], system="Be helpful", on_text=on_text)
        assert result.text == "Hello!" and got == ["Hello!"]
        kwargs = provider.client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 1024
        assert kwargs["messages"][0] == {"role": "system", "content": "Be helpful"}

    def test_convert_tools(self):
        tools = [
            {
                "name": "speak",
                "description": "Speak text",
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ]
        converted = OpenAIProvider._convert_tools(tools)
        assert converted[0]["type"] == "function" and converted[0]["function"]["name"] == "speak"

    def test_convert_messages_keeps_tool_calls_and_results(self):
        provider = self.make()
        messages = [
            {"role": "user", "content": "wave"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Sure"},
                    {"type": "tool_use", "id": "t1", "name": "play_animation", "input": {"name": "x"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "image", "source": {}}, {"type": "text", "text": '{"success": true}'}],
                    },
                ],
            },
            {"role": "assistant", "content": "Done"},
        ]
        oai = provider._convert_messages(messages, [{"type": "text", "text": "sys"}])
        assert oai[0] == {"role": "system", "content": "sys"}
        assert oai[1] == {"role": "user", "content": "wave"}
        assert oai[2]["role"] == "assistant" and oai[2]["tool_calls"][0]["id"] == "t1"
        assert oai[2]["tool_calls"][0]["function"]["arguments"] == '{"name": "x"}'
        assert oai[3]["role"] == "tool" and oai[3]["tool_call_id"] == "t1" and "image omitted" in oai[3]["content"]
        assert oai[4] == {"role": "assistant", "content": "Done"}


class TestSystemPrompt:

    def test_system_prompt_content(self):
        assert "Pepper" in SYSTEM_PROMPT
        assert "TRiPL Lab" in SYSTEM_PROMPT
        assert "take_photo" in SYSTEM_PROMPT
        assert "spoken aloud" in SYSTEM_PROMPT
