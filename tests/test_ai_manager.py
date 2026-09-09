"""
Tests for AIManager - multi-turn tool-calling conversation loop.
"""

import asyncio
from unittest.mock import AsyncMock


from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall


def text(t, model="claude-opus-5"):
    return AIResponse(text=t, tool_calls=[], stop_reason="end_turn", model=model)


def tool(name, inp, tid="t1", preface=""):
    return AIResponse(
        text=preface, tool_calls=[ToolCall(id=tid, name=name, input=inp)], stop_reason="tool_use", model="claude-opus-5"
    )


class TestAIManager:

    async def test_simple_text_response(self, mock_ai_manager):
        result = await mock_ai_manager.process_user_input("Hello")
        assert result["text"] == "Hello! I'm Pepper."
        assert result["tool_calls"] == []
        assert result["photo"] is None
        assert len(mock_ai_manager.conversation_history) == 2  # user + assistant

    async def test_system_prompt_has_cached_static_and_dynamic_state(self, mock_ai_manager, mock_ai_provider):
        await mock_ai_manager.process_user_input("Hello")
        system = mock_ai_provider.chat.call_args.kwargs["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "Pepper" in system[0]["text"]
        assert "battery 80%" in system[1]["text"]
        assert "posture Stand" in system[1]["text"]

    async def test_tool_call_then_text(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(side_effect=[tool("set_eye_color", {"color": "blue"}), text("Done!")])
        result = await mock_ai_manager.process_user_input("Blue eyes please")
        assert result["text"] == "Done!"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "set_eye_color"
        assert result["tool_calls"][0]["ok"] is True
        # history: user, assistant(tool_use), user(tool_result), assistant(text)
        roles = [m["role"] for m in mock_ai_manager.conversation_history]
        assert roles == ["user", "assistant", "user", "assistant"]
        tool_result = mock_ai_manager.conversation_history[2]["content"][0]
        assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"

    async def test_failed_tool_is_marked_error_for_model(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(side_effect=[tool("play_animation", {"name": "nope"}), text("Sorry")])
        result = await mock_ai_manager.process_user_input("Do a thing")
        assert result["tool_calls"][0]["ok"] is False
        assert mock_ai_manager.conversation_history[2]["content"][0]["is_error"] is True

    async def test_photo_flows_to_model_and_result(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(side_effect=[tool("take_photo", {}), text("I see a desk.")])
        result = await mock_ai_manager.process_user_input("What do you see?")
        assert result["photo"]["media_type"] == "image/jpeg"
        assert result["photo"]["base64"] == "base64data"
        content = mock_ai_manager.conversation_history[2]["content"][0]["content"]
        assert content[0]["type"] == "image"
        assert mock_ai_manager.last_photo is not None

    async def test_multiple_tool_rounds(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(
            side_effect=[
                tool("get_sensors", {}, "t1", preface="Let me check."),
                tool("set_eye_color", {"color": "green"}, "t2"),
                text("All good."),
            ]
        )
        result = await mock_ai_manager.process_user_input("Check and glow")
        assert len(result["tool_calls"]) == 2
        assert result["rounds"] == 3
        assert result["text"] == "Let me check.\nAll good."

    async def test_parallel_tool_calls_in_one_round(self, mock_ai_manager, mock_ai_provider):
        both = AIResponse(
            text="",
            stop_reason="tool_use",
            model="m",
            tool_calls=[
                ToolCall(id="a", name="set_eye_color", input={"color": "red"}),
                ToolCall(id="b", name="play_animation", input={"name": "animations/Stand/Gestures/Hey_1"}),
            ],
        )
        mock_ai_provider.chat = AsyncMock(side_effect=[both, text("Ta-da")])
        result = await mock_ai_manager.process_user_input("Red eyes and wave")
        assert [tc["name"] for tc in result["tool_calls"]] == ["set_eye_color", "play_animation"]
        results = mock_ai_manager.conversation_history[2]["content"]
        assert [r["tool_use_id"] for r in results] == ["a", "b"]  # all results in one user message

    async def test_max_tool_rounds(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.MAX_TOOL_ROUNDS = 3
        mock_ai_provider.chat = AsyncMock(return_value=tool("get_sensors", {}))
        result = await mock_ai_manager.process_user_input("Loop forever")
        assert "carried away" in result["text"]
        assert len(result["tool_calls"]) == 3
        assert mock_ai_manager.conversation_history[-1]["role"] == "assistant"

    async def test_provider_error_does_not_pollute_history(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(return_value=AIResponse(text="Sorry", stop_reason="error", model="m"))
        result = await mock_ai_manager.process_user_input("Hi")
        assert result["stop_reason"] == "error"
        assert mock_ai_manager.conversation_history == []

    async def test_history_trims_at_user_boundaries(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.history_turns = 2
        mock_ai_provider.chat = AsyncMock(
            side_effect=[
                tool("get_sensors", {}),
                text("one"),
                tool("get_sensors", {}),
                text("two"),
                tool("get_sensors", {}),
                text("three"),
            ]
        )
        for msg in ("a", "b", "c"):
            await mock_ai_manager.process_user_input(msg)
        history = mock_ai_manager.conversation_history
        # Only the last two user turns survive, each with its tool_use/tool_result pair intact.
        assert isinstance(history[0]["content"], str) and history[0]["content"] == "b"
        for i, m in enumerate(history):
            if m["role"] == "assistant" and isinstance(m["content"], list):
                assert history[i + 1]["content"][0]["type"] == "tool_result"

    async def test_old_images_are_pruned(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.image_history = 1
        mock_ai_provider.chat = AsyncMock(
            side_effect=[
                tool("take_photo", {}),
                text("one"),
                tool("take_photo", {}),
                text("two"),
            ]
        )
        await mock_ai_manager.process_user_input("look")
        await mock_ai_manager.process_user_input("look again")
        image_blocks = []
        for m in mock_ai_manager.conversation_history:
            if m["role"] == "user" and isinstance(m["content"], list):
                for tr in m["content"]:
                    image_blocks.append([b["type"] for b in tr["content"]])
        assert image_blocks == [["text", "text"], ["image", "text"]]

    async def test_speech_streams_sentences(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)

        async def chat(messages, tools=None, system=None, on_text=None):
            for chunk in ["Hello there! I am ", "Pepper. How are", " you today?"]:
                await on_text(chunk)
            return text("Hello there! I am Pepper. How are you today?")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("Hi")
        assert result["spoken"] == ["Hello there!", "I am Pepper.", "How are you today?"]
        calls = [c.args[0] for c in mock_robot.connection.bridge.speak.call_args_list]
        assert calls == ["Hello there!", "I am Pepper.", "How are you today?"]

    async def test_animation_tags_are_spoken_but_not_displayed(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        reply = "^start(animations/Stand/Gestures/Hey_1) Hi there!"

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text(reply)
            return text(reply)

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("Hi")
        assert result["text"] == "Hi there!"
        assert mock_robot.connection.bridge.speak.call_args.args[0] == reply

    async def test_tablet_subtitles_disable_after_failure(self, mock_robot, mock_ai_provider):
        from src.pepper.bridge_client import BridgeError

        mock_robot.connection.bridge.tablet_text = AsyncMock(side_effect=BridgeError("no tablet"))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=True)

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("One. Two.")
            return text("One. Two.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await manager.process_user_input("Hi")
        assert mock_robot.connection.bridge.tablet_text.call_count == 1
        assert manager._tablet_ok is False

    async def test_concurrent_requests_are_serialized(self, mock_ai_manager, mock_ai_provider):
        order = []

        async def chat(messages, tools=None, system=None, on_text=None):
            order.append(("start", messages[-1]["content"]))
            await asyncio.sleep(0.01)
            order.append(("end", messages[-1]["content"]))
            return text("ok")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await asyncio.gather(mock_ai_manager.process_user_input("A"), mock_ai_manager.process_user_input("B"))
        assert order == [("start", "A"), ("end", "A"), ("start", "B"), ("end", "B")]

    async def test_touch_event_triggers_reaction_with_cooldown(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.react_to_touch = True
        mock_ai_manager.touch_cooldown = 60
        await mock_ai_manager.handle_event("touch", {"sensor": "head_front", "touched": True})
        await mock_ai_manager.handle_event("touch", {"sensor": "head_front", "touched": False})
        await mock_ai_manager.handle_event("touch", {"sensor": "hand_left", "touched": True})  # within cooldown
        await asyncio.sleep(0.05)
        assert mock_ai_provider.chat.call_count == 1
        messages = mock_ai_provider.chat.call_args.kwargs["messages"]
        prompt = [m for m in messages if m["role"] == "user"][-1]["content"]
        assert prompt.startswith("[Sensor event]") and "front of your head" in prompt

    async def test_events_ignored_when_disabled(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.react_to_touch = False
        await mock_ai_manager.handle_event("touch", {"sensor": "head_front", "touched": True})
        await asyncio.sleep(0.01)
        mock_ai_provider.chat.assert_not_called()

    async def test_clear_and_get_history(self, mock_ai_manager):
        await mock_ai_manager.process_user_input("Hello")
        history = mock_ai_manager.get_conversation_history()
        assert len(history) == 2
        mock_ai_manager.clear_conversation_history()
        assert mock_ai_manager.conversation_history == []
        assert len(history) == 2  # copy

    async def test_history_api_elides_images(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(side_effect=[tool("take_photo", {}), text("ok")])
        await mock_ai_manager.process_user_input("look")
        shown = mock_ai_manager.get_conversation_history()[2]["content"][0]["content"][0]
        assert shown == {"type": "image", "elided": True}

    async def test_response_callback(self, mock_ai_manager):
        callback = AsyncMock()
        mock_ai_manager.on_response(callback)
        await mock_ai_manager.process_user_input("Hello")
        callback.assert_called_once()
        assert callback.call_args.args[0]["source"] == "user"

    async def test_round_boundary_flushes_partial_sentence(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        calls = iter(
            [
                ("Hi there!", tool("set_eye_color", {"color": "blue"})),
                ("Blue it is. How do I look?", text("Blue it is. How do I look?")),
            ]
        )

        async def chat(messages, tools=None, system=None, on_text=None):
            chunk, response = next(calls)
            await on_text(chunk)
            response.text = chunk
            return response

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("Blue eyes")
        assert result["spoken"] == ["Hi there!", "Blue it is.", "How do I look?"]

    async def test_phantom_tool_call_is_retried_and_never_spoken(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        xml = '<invoke name="play_animation">\n<parameter name="name">x</parameter>\n</invoke>'
        phantom = AIResponse(text=xml, tool_calls=[], stop_reason="tool_use", model="m")

        async def chat(messages, tools=None, system=None, on_text=None):
            response = phantom if mock_ai_provider.chat.call_count == 1 else text("Done!")
            await on_text(response.text)
            return response

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("Wave")
        assert mock_ai_provider.chat.call_count == 2
        assert result["text"] == "Done!" and result["spoken"] == ["Done!"]
        assert all("invoke" not in c.args[0] for c in mock_robot.connection.bridge.speak.call_args_list)
        assert [m["role"] for m in manager.conversation_history] == ["user", "assistant"]

    async def test_phantom_twice_gives_up_gracefully(self, mock_ai_manager, mock_ai_provider):
        phantom = AIResponse(text='<invoke name="x"></invoke>', tool_calls=[], stop_reason="tool_use", model="m")
        mock_ai_provider.chat = AsyncMock(return_value=phantom)
        result = await mock_ai_manager.process_user_input("Wave")
        assert mock_ai_provider.chat.call_count == 2
        assert "invoke" not in result["text"] and result["text"]
