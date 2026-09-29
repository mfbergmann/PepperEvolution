"""
Tests for the local-model provider: streaming, tool calls, where the state goes, and photos.
"""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from src.ai.models import ERROR_TEXT, OllamaProvider

PHOTO = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
SYSTEM = [{"type": "text", "text": "You are Pepper."}, {"type": "text", "text": "Around you: nobody in view."}]


def provider():
    p = OllamaProvider("http://alien3:11434/", "qwen3-vl:30b-a3b-instruct")
    p.client = MagicMock()
    return p


def chunk(content=None, tool_calls=None, finish=None, usage=None):
    choices = [NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)] if not usage else []
    return NS(choices=choices, usage=usage)


def call(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


async def stream_of(*chunks):
    for c in chunks:
        yield c


class TestStreaming:
    async def test_text_streams_to_the_callback(self):
        p = provider()
        p.client.chat.completions.create = AsyncMock(
            return_value=stream_of(
                chunk("Hi "),
                chunk("there!"),
                chunk(finish="stop"),
                chunk(usage=NS(prompt_tokens=10, completion_tokens=3)),
            )
        )
        heard = []

        async def on_text(t):
            heard.append(t)

        r = await p.chat([{"role": "user", "content": "Hello"}], system=SYSTEM, on_text=on_text)
        assert heard == ["Hi ", "there!"] and r.text == "Hi there!" and r.stop_reason == "end_turn"
        assert r.usage == {"input_tokens": 10, "output_tokens": 3}
        kwargs = p.client.chat.completions.create.call_args.kwargs
        assert kwargs["stream"] is True and kwargs["model"] == "qwen3-vl:30b-a3b-instruct"

    async def test_tool_call_fragments_are_joined(self):
        p = provider()
        p.client.chat.completions.create = AsyncMock(
            return_value=stream_of(
                chunk("Let me look."),
                chunk(tool_calls=[call(0, id="c1", name="move_head", args='{"ya')]),
                chunk(tool_calls=[call(0, args='w": 30}')]),
                chunk(tool_calls=[call(1, name="take_photo", args="{}")]),
                chunk(finish="tool_calls"),
            )
        )
        r = await p.chat([{"role": "user", "content": "Look left"}], tools=[{"name": "move_head", "input_schema": {}}])
        assert [(t.id, t.name, t.input) for t in r.tool_calls] == [
            ("c1", "move_head", {"yaw": 30}),
            ("call_1", "take_photo", {}),
        ]
        assert r.stop_reason == "tool_use"

    async def test_separate_calls_sharing_index_zero_stay_separate(self):
        p = provider()
        p.client.chat.completions.create = AsyncMock(
            return_value=stream_of(
                chunk(tool_calls=[call(0, id="a", name="set_eye_color", args='{"color": "blue"}')]),
                chunk(tool_calls=[call(0, id="b", name="move_head", args='{"yaw": -60}')]),
            )
        )
        r = await p.chat([{"role": "user", "content": "Blue eyes, look right"}])
        assert [(t.name, t.input) for t in r.tool_calls] == [
            ("set_eye_color", {"color": "blue"}),
            ("move_head", {"yaw": -60}),
        ]

    async def test_server_down_is_an_error_reply(self):
        p = provider()
        p.client.chat.completions.create = AsyncMock(side_effect=ConnectionError("refused"))
        r = await p.chat([{"role": "user", "content": "Hi"}])
        assert r.is_error and r.text == ERROR_TEXT


class TestMessages:
    def test_state_goes_after_the_newest_user_text_not_in_the_system_message(self):
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hello!"}]},
            {"role": "user", "content": "What's up?"},
        ]
        out = provider()._convert_messages_local(history, SYSTEM)
        assert out[0] == {"role": "system", "content": "You are Pepper."}
        assert out[1]["content"] == "Hi"  # older turns unchanged, so the server's cache still matches
        assert out[-1]["content"] == "What's up?\n\n[Around you: nobody in view.]"

    def test_photo_from_a_tool_result_follows_the_tool_message(self):
        history = [
            {"role": "user", "content": "What do you see?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "take_photo", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": [{"type": "text", "text": "ok"}, PHOTO]}
                ],
            },
        ]
        out = provider()._convert_messages_local(history, "You are Pepper.")
        roles = [m["role"] for m in out]
        assert roles == ["system", "user", "assistant", "tool", "user"]
        assert out[2]["tool_calls"][0]["function"]["name"] == "take_photo"
        assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok\n[photo follows]"}
        assert out[4]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}

    def test_removed_photos_are_not_sent(self):
        elided = {"type": "image", "elided": True}
        history = [{"role": "user", "content": [{"type": "text", "text": "hi"}, elided]}]
        assert provider()._convert_messages_local(history, None) == [{"role": "user", "content": "hi"}]
