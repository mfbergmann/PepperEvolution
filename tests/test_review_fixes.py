"""
Regression tests for the issues found in the adversarial review of v2.1.

Covers: thinking-block replay, truncated/empty/refused responses, speech draining
before tool execution, emergency-halt handling mid-turn, event-reaction re-checks,
interrupted moves, prepare warnings, WebSocket chat not blocking the socket,
and the sentence splitter's abbreviation guard.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.ai.manager import HALTED_TEXT, TRUNCATED_TOOL_TEXT, AIManager
from src.ai.models import ERROR_TEXT, REFUSAL_TEXT, AIResponse, ToolCall, _raw_blocks
from src.ai.speech import SentenceSplitter, SpeechStreamer
from src.ai.tool_executor import ToolExecutor
from src.communication.api import APIServer, execute_command
from src.pepper import ConnectionConfig, FakeBridgeClient, PepperRobot, PrepareOptions
from src.pepper.event_stream import EventStream


def text(t):
    return AIResponse(text=t, tool_calls=[], stop_reason="end_turn", model="m")


def tool(name, inp, tid="t1", preface="", content=None, stop_reason="tool_use"):
    return AIResponse(
        text=preface,
        tool_calls=[ToolCall(id=tid, name=name, input=inp)],
        stop_reason=stop_reason,
        model="m",
        content=content or [],
    )


THINKING = {"type": "thinking", "thinking": "", "signature": "sig123"}


class TestThinkingReplay:

    def test_raw_blocks_serialises_sdk_and_doubles(self):
        class Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

            def model_dump(self, exclude_none=True, mode="json"):
                return {k: v for k, v in self.__dict__.items() if v is not None}

        msg = SimpleNamespace(
            content=[Block(type="thinking", thinking="", signature="s"), Block(type="text", text="hi", citations=None)]
        )
        assert _raw_blocks(msg) == [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "hi"},
        ]
        plain = SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="1", name="x", input={})])
        assert _raw_blocks(plain) == [{"type": "tool_use", "id": "1", "name": "x", "input": {}}]

    async def test_provider_content_is_replayed_verbatim(self, mock_ai_manager, mock_ai_provider):
        blocks = [
            THINKING,
            {"type": "text", "text": "Sure."},
            {"type": "tool_use", "id": "t1", "name": "set_eye_color", "input": {"color": "blue"}},
        ]
        mock_ai_provider.chat = AsyncMock(
            side_effect=[
                tool("set_eye_color", {"color": "blue"}, preface="Sure.", content=blocks),
                text("Done"),
            ]
        )
        await mock_ai_manager.process_user_input("blue")
        assistant = mock_ai_manager.conversation_history[1]
        assert assistant["content"] == blocks  # thinking block kept, order untouched
        # the second request saw the replayed turn
        sent = mock_ai_provider.chat.call_args_list[1].kwargs["messages"]
        assert sent[1]["content"][0]["type"] == "thinking"

    async def test_hand_built_turn_when_provider_gives_no_blocks(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(side_effect=[tool("set_eye_color", {"color": "blue"}), text("Done")])
        await mock_ai_manager.process_user_input("blue")
        assert mock_ai_manager.conversation_history[1]["content"] == [
            {"type": "tool_use", "id": "t1", "name": "set_eye_color", "input": {"color": "blue"}}
        ]


class TestTruncationAndRefusal:

    async def test_truncated_tool_call_is_not_executed(self, mock_ai_manager, mock_ai_provider, mock_robot):
        mock_ai_provider.chat = AsyncMock(
            side_effect=[
                tool("move_forward", {"distance": 1}, stop_reason="max_tokens"),
                text("Sorry, shorter now."),
            ]
        )
        result = await mock_ai_manager.process_user_input("go")
        mock_robot.connection.bridge.move_forward.assert_not_called()
        assert result["tool_calls"] == []
        results = mock_ai_manager.conversation_history[2]["content"]
        assert results[0]["is_error"] is True and results[0]["content"] == TRUNCATED_TOOL_TEXT
        assert result["text"] == "Sorry, shorter now."

    async def test_empty_max_tokens_response_speaks_error(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        mock_ai_provider.chat = AsyncMock(return_value=AIResponse(text="", stop_reason="max_tokens", model="m"))
        result = await manager.process_user_input("hi")
        assert " ".join(result["spoken"]) == ERROR_TEXT
        assert manager.conversation_history == []  # no dangling user message

    async def test_refusal_is_spoken(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        mock_ai_provider.chat = AsyncMock(return_value=AIResponse(text=REFUSAL_TEXT, stop_reason="refusal", model="m"))
        result = await manager.process_user_input("do something bad")
        assert result["spoken"] == [REFUSAL_TEXT]
        assert manager.conversation_history[-1] == {"role": "assistant", "content": REFUSAL_TEXT}


class TestSpeechAndTools:

    async def test_preamble_is_spoken_before_tools_run(self, mock_robot, mock_ai_provider):
        order = []

        async def speak(text, **kwargs):
            await asyncio.sleep(0.02)
            order.append(("speak", text))
            return {"ok": True}

        async def animation(name):
            order.append(("animation", name))
            return {"ok": True}

        mock_robot.connection.bridge.speak = speak
        mock_robot.connection.bridge.play_animation = animation
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)

        async def chat(messages, tools=None, system=None, on_text=None):
            if mock_ai_provider.chat.call_count == 1:
                await on_text("Let me wave. ")
                return tool("play_animation", {"name": "animations/Stand/Gestures/Hey_1"}, preface="Let me wave.")
            await on_text("Done.")
            return text("Done.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await manager.process_user_input("wave")
        assert order == [
            ("speak", "Let me wave."),
            ("animation", "animations/Stand/Gestures/Hey_1"),
            ("speak", "Done."),
        ]

    async def test_phantom_retry_does_not_repeat_preamble(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        xml = 'Sure, let me look. <invoke name="take_photo"></invoke>'

        async def chat(messages, tools=None, system=None, on_text=None):
            if mock_ai_provider.chat.call_count == 1:
                await on_text(xml)
                return AIResponse(text=xml, stop_reason="tool_use", model="m")
            await on_text("Sure, let me look. Nice room!")
            return text("Sure, let me look. Nice room!")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("look")
        assert result["spoken"] == ["Sure, let me look.", "Nice room!"]

    async def test_halt_mid_turn_ends_the_turn(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        both = AIResponse(
            text="",
            stop_reason="tool_use",
            model="m",
            tool_calls=[
                ToolCall(id="a", name="turn", input={"angle": 90}),
                ToolCall(id="b", name="move_forward", input={"distance": 1.0}),
            ],
        )

        async def turn(angle):
            mock_robot.halted = True  # operator pressed emergency stop during the turn
            return {"ok": True}

        mock_robot.connection.bridge.move_turn = turn
        mock_ai_provider.chat = AsyncMock(side_effect=[both, text("never reached")])
        result = await manager.process_user_input("go")
        mock_robot.connection.bridge.move_forward.assert_not_called()
        assert result["tool_calls"][1]["ok"] is False and "halted" in result["tool_calls"][1]["result"]
        assert result["text"].endswith(HALTED_TEXT)
        assert mock_ai_provider.chat.call_count == 1
        assert result["spoken"] == []  # nothing is spoken while halted

    async def test_splitter_keeps_abbreviations(self):
        s = SentenceSplitter()
        assert s.feed("Dr. Smith is here. Ask Mr. Jones. ") == ["Dr. Smith is here.", "Ask Mr. Jones."]

    async def test_drain_waits_for_queued_speech(self):
        spoken = []

        async def speak(t):
            await asyncio.sleep(0.02)
            spoken.append(t)

        streamer = SpeechStreamer(speak)
        await streamer.on_text("One. Two. ")
        await streamer.drain()
        assert spoken == ["One.", "Two."]
        await streamer.finish()


class TestEventReactions:

    async def test_reaction_dropped_when_turn_started_meanwhile(self, mock_ai_manager, mock_ai_provider):
        mock_ai_manager.react_to_touch = True
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_chat(messages, tools=None, system=None, on_text=None):
            started.set()
            await release.wait()
            return text("ok")

        mock_ai_provider.chat = AsyncMock(side_effect=slow_chat)
        user_turn = asyncio.create_task(mock_ai_manager.process_user_input("hello"))  # scheduled, lock not yet held
        await mock_ai_manager.handle_event("touch", {"sensor": "head_front", "touched": True})  # busy check passes
        await started.wait()  # the user turn won the lock; the reaction must notice and drop itself
        await asyncio.sleep(0.01)
        release.set()
        await user_turn
        await asyncio.sleep(0.02)
        assert mock_ai_provider.chat.call_count == 1  # the reaction saw the lock and dropped itself
        assert mock_ai_manager._last_event_reaction == 0.0  # cooldown reset so the next touch works

    async def test_reaction_skipped_during_direct_command(self, mock_ai_manager, mock_ai_provider, mock_robot):
        mock_robot.direct_commands_running = 1
        await mock_ai_manager.handle_event("touch", {"sensor": "head_front", "touched": True})
        await asyncio.sleep(0.01)
        mock_ai_provider.chat.assert_not_called()


class TestExecutorResults:

    @pytest.fixture
    def executor(self, mock_robot):
        return ToolExecutor(mock_robot)

    async def test_interrupted_move_is_a_failure(self, executor, mock_robot):
        mock_robot.connection.bridge.move_forward = AsyncMock(return_value={"ok": True, "completed": False})
        outcome = await executor.execute("move_forward", {"distance": 1.0})
        assert not outcome.ok and "interrupted" in outcome.data["error"]
        mock_robot.connection.bridge.move_turn = AsyncMock(return_value={"ok": True, "completed": False})
        assert not (await executor.execute("turn", {"angle": 30})).ok

    async def test_no_sonar_data_limits_move(self, executor, mock_robot):
        mock_robot.sensors.get_all = AsyncMock(return_value={"sonar": {}})
        assert not (await executor.execute("move_forward", {"distance": 1.0})).ok
        assert (await executor.execute("move_forward", {"distance": 0.4})).ok

    async def test_halted_outcome(self):
        assert not ToolExecutor.halted_outcome().ok


class TestRobotState:

    async def test_prepare_warnings_and_halt_flags(self):
        bridge = FakeBridgeClient()
        bridge.speech_delay = 0
        robot = PepperRobot(ConnectionConfig(ip="fake"), bridge=bridge)
        bridge.prepare = AsyncMock(return_value={"ok": True, "awake": True, "errors": ["autonomous_life: wizard"]})
        assert await robot.initialize(prepare=PrepareOptions())
        assert robot.last_prepare["errors"] == ["autonomous_life: wizard"]
        await robot.emergency_stop()
        assert robot.halted is True
        await robot.wake_up()
        assert robot.halted is False
        await robot.emergency_stop()
        await robot.prepare(PrepareOptions())
        assert robot.halted is False
        await robot.shutdown()

    async def test_photo_resolution_default(self, fake_robot, fake_bridge):
        fake_robot.photo_resolution = 1
        await fake_robot.take_picture()
        assert fake_bridge.calls[-1]["action"] == "take_picture"


class TestAPIExtras:

    @pytest.fixture
    def api_server(self, mock_robot, mock_ai_manager, tmp_path):
        return APIServer(host="127.0.0.1", port=8000, ai_manager=mock_ai_manager, robot=mock_robot, web_dir=tmp_path)

    async def test_prepare_warnings_surface(self, mock_robot):
        mock_robot.connection.bridge.prepare = AsyncMock(return_value={"ok": True, "errors": ["awake: nope"]})
        result = await execute_command(mock_robot, "prepare", {})
        assert result["success"] is True and result["warnings"] == ["awake: nope"]

    async def test_direct_command_latch(self, mock_robot):
        seen = []

        async def speak(*a, **k):
            seen.append(mock_robot.direct_commands_running)
            return {"ok": True}

        mock_robot.connection.bridge.speak = speak
        await execute_command(mock_robot, "speak", {"text": "hi"})
        assert seen == [1] and mock_robot.direct_commands_running == 0

    async def test_chat_passes_client_id(self, api_server, mock_ai_manager):
        mock_ai_manager.process_user_input = AsyncMock(return_value={"text": "ok"})
        async with AsyncClient(transport=ASGITransport(app=api_server.app), base_url="http://test") as c:
            await c.post("/chat", json={"message": "hi", "client_id": "abc"})
        mock_ai_manager.process_user_input.assert_called_once_with("hi", speak=None, client_id="abc")

    async def test_ws_chat_does_not_block_the_socket(self, api_server, mock_ai_manager):
        """A command sent while a chat turn is running must be handled immediately."""
        from starlette.testclient import TestClient

        gate = asyncio.Event()

        async def slow_turn(message, speak=None, client_id=None):
            await gate.wait()
            return {"text": "done", "source": "user"}

        mock_ai_manager.process_user_input = AsyncMock(side_effect=slow_turn)
        with TestClient(api_server.app) as client:
            with client.websocket_connect("/ws") as ws:
                assert json.loads(ws.receive_text())["type"] == "welcome"
                ws.send_text(json.dumps({"type": "chat", "message": "walk"}))
                assert json.loads(ws.receive_text())["type"] == "chat_user"
                ws.send_text(json.dumps({"type": "ping"}))
                assert json.loads(ws.receive_text())["type"] == "pong"  # answered while the turn is blocked
                gate.set()


class TestEventStreamAuth:

    async def test_unauthorized_error_backs_off(self):
        es = EventStream("ws://x/ws/events", api_key="bad")
        await es._handle_raw(json.dumps({"type": "error", "data": {"error": "unauthorized"}}))
        assert es._backoff == 30.0
