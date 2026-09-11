"""
Tests for the reactive layer inside the AI loop: local intents that bypass the
model, eye LED state signals, and backchannel fillers while the model is slow.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.ai.manager import ABORTED_TEXT, FILLERS, AIManager
from src.ai.models import AIResponse, ToolCall
from src.ai.tool_executor import ToolOutcome
from src.pepper.bridge_client import BridgeError


def text(t):
    return AIResponse(text=t, tool_calls=[], stop_reason="end_turn", model="claude-opus-5")


def tools(*calls):
    return AIResponse(
        text="",
        tool_calls=[ToolCall(id=f"t{i}", name=name, input=inp) for i, (name, inp) in enumerate(calls)],
        stop_reason="tool_use",
        model="claude-opus-5",
    )


def eye_colors(robot):
    return [c.kwargs.get("color") for c in robot.connection.bridge.set_eye_leds.call_args_list]


def manager_for(robot, provider, **kw):
    kw.setdefault("speak_responses", True)
    kw.setdefault("tablet_subtitles", False)
    kw.setdefault("backchannel_after", 0)
    return AIManager(robot, provider, **kw)


async def wait_until(predicate, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not met in time"
        await asyncio.sleep(0)


class TestIntentsInTheLoop:
    async def test_intent_bypasses_model_and_history(self, mock_ai_manager, mock_ai_provider):
        result = await mock_ai_manager.process_user_input("Pepper, stop")
        assert result["stop_reason"] == "intent" and result["intent"] == "stop"
        assert result["tool_calls"] == [] and result["rounds"] == 0
        mock_ai_provider.chat.assert_not_called()
        assert mock_ai_manager.conversation_history == []
        mock_ai_manager.robot.connection.bridge.stop.assert_awaited_once()
        mock_ai_manager.robot.connection.bridge.stop_speaking.assert_awaited_once()

    async def test_ack_is_spoken_without_animation(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)
        result = await manager.process_user_input("wake up")
        assert result["text"] == "I'm up." and result["spoken"] == ["I'm up."]
        call = mock_robot.connection.bridge.speak.call_args
        assert call.args[0] == "I'm up." and call.kwargs["animated"] is False

    async def test_no_ack_after_emergency_stop(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)
        result = await manager.process_user_input("emergency stop")
        assert result["intent"] == "emergency_stop" and mock_robot.halted is True
        mock_robot.connection.bridge.emergency_stop.assert_awaited_once()
        mock_robot.connection.bridge.speak.assert_not_called()

    async def test_failed_intent_reports_error(self, mock_ai_manager):
        mock_ai_manager.robot.connection.bridge.rest = AsyncMock(side_effect=BridgeError("nope"))
        result = await mock_ai_manager.process_user_input("rest")
        assert result["error"] == "nope" and result["text"] == "Sorry, that didn't work."

    async def test_intents_only_come_from_people(self, mock_ai_manager, mock_ai_provider):
        result = await mock_ai_manager.process_user_input("stop", source="event")
        assert result["stop_reason"] == "end_turn"
        mock_ai_provider.chat.assert_called_once()

    async def test_voice_source_matches_intents(self, mock_ai_manager, mock_ai_provider):
        result = await mock_ai_manager.process_user_input("okay pepper, be quiet", source="voice")
        assert result["intent"] == "quiet" and result["source"] == "voice"
        mock_ai_provider.chat.assert_not_called()

    async def test_intent_result_reaches_response_callbacks(self, mock_ai_manager):
        seen = []

        async def cb(result):
            seen.append(result["intent"])

        mock_ai_manager._response_callbacks.append(cb)
        await mock_ai_manager.process_user_input("look at me")
        assert seen == ["look_at_me"]

    async def test_stop_during_a_turn_skips_the_remaining_tools(self, mock_ai_manager, mock_ai_provider):
        mock_ai_provider.chat = AsyncMock(
            side_effect=[tools(("move_forward", {"distance": 0.5}), ("play_animation", {"name": "Hey_1"})), text("no")]
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_execute(name, inp):
            entered.set()
            await release.wait()
            return ToolOutcome(ok=True, data={"moved": True})

        mock_ai_manager.executor.execute = slow_execute
        turn = asyncio.create_task(mock_ai_manager.process_user_input("walk over there and wave"))
        await entered.wait()
        assert mock_ai_manager.busy
        stop = await mock_ai_manager.process_user_input("stop")  # not blocked by the running turn
        assert stop["stop_reason"] == "intent"
        release.set()
        result = await turn
        assert [tc["ok"] for tc in result["tool_calls"]] == [True, False]
        assert "said stop" in result["tool_calls"][1]["result"]
        assert mock_ai_provider.chat.call_count == 1  # the model is not asked to continue
        assert result["text"] == ABORTED_TEXT
        assert mock_ai_manager.conversation_history[-1] == {"role": "assistant", "content": ABORTED_TEXT}
        assert not mock_ai_manager.busy

    async def test_stop_cancels_the_model_call_in_flight(self, mock_ai_manager, mock_ai_provider):
        release, started = asyncio.Event(), asyncio.Event()
        cancelled = []

        async def chat(messages, tools=None, system=None, on_text=None):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return text("never")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        turn = asyncio.create_task(mock_ai_manager.process_user_input("tell me a long story"))
        await started.wait()  # the model call is really in flight
        stop = await mock_ai_manager.process_user_input("stop")
        assert stop["stop_reason"] == "intent"
        result = await turn
        assert cancelled == [True]
        assert result["text"] == ABORTED_TEXT and result["stop_reason"] == ""
        assert mock_ai_manager.conversation_history == []  # the unanswered question is gone
        assert mock_ai_manager._chat_task is None and not mock_ai_manager.busy

    async def test_shutdown_cancellation_is_not_mistaken_for_a_stop(self, mock_ai_manager, mock_ai_provider):
        release = asyncio.Event()

        async def chat(messages, tools=None, system=None, on_text=None):
            await release.wait()
            return text("never")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        turn = asyncio.create_task(mock_ai_manager.process_user_input("hi"))
        await wait_until(lambda: mock_ai_manager._chat_task is not None)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert not mock_ai_manager.busy

    async def test_turns_queued_before_a_stop_are_dropped(self, mock_ai_manager, mock_ai_provider):
        clock = [100.0]
        mock_ai_manager._clock = lambda: clock[0]
        release = asyncio.Event()

        async def chat(messages, tools=None, system=None, on_text=None):
            await release.wait()
            return text("done")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        first = asyncio.create_task(mock_ai_manager.process_user_input("walk to the door"))
        await wait_until(lambda: mock_ai_manager.busy)
        clock[0] = 101.0
        queued = asyncio.create_task(mock_ai_manager.process_user_input("and then wave"))
        await asyncio.sleep(0)
        clock[0] = 102.0
        await mock_ai_manager.process_user_input("stop")
        release.set()
        assert (await first)["text"] == ABORTED_TEXT
        assert (await queued)["stop_reason"] == "cancelled"
        clock[0] = 103.0
        mock_ai_provider.chat = AsyncMock(return_value=text("fresh"))
        assert (await mock_ai_manager.process_user_input("hello again"))["text"] == "fresh"

    async def test_intent_restores_idle_eyes_when_no_turn_runs(self, mock_ai_manager):
        mock_ai_manager.robot.last_eye_color = "green"
        await mock_ai_manager.signal_state("listening")
        await mock_ai_manager.process_user_input("look at me")
        assert eye_colors(mock_ai_manager.robot) == ["blue", "green"]

    async def test_quiet_hushes_the_rest_of_the_turn(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)
        release = asyncio.Event()

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("First sentence. ")
            await release.wait()
            await on_text("Second sentence.")
            return text("First sentence. Second sentence.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        turn = asyncio.create_task(manager.process_user_input("tell me two things"))
        await wait_until(lambda: mock_robot.connection.bridge.speak.await_count == 1)
        hush = await manager.process_user_input("be quiet")
        assert hush["stop_reason"] == "intent" and hush["spoken"] == []
        release.set()
        result = await turn
        assert result["spoken"] == ["First sentence."]
        assert "Second sentence." in result["text"]  # the reply is kept in full, only speech is suppressed
        mock_robot.connection.bridge.stop_speaking.assert_awaited_once()

    async def test_hush_does_not_leak_into_the_next_turn(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("Hello.")
            return text("Hello.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await manager.process_user_input("quiet")
        result = await manager.process_user_input("hi")
        assert result["spoken"] == ["Hello."]


class TestLedSignals:
    async def test_thinking_then_speaking_then_idle(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("Hello there. Nice to see you.")
            return text("Hello there. Nice to see you.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await manager.process_user_input("hi")
        assert eye_colors(mock_robot) == ["purple", "white", "white"]  # speaking is signalled once per turn

    async def test_idle_restores_the_colour_someone_chose(self, mock_robot, mock_ai_provider):
        mock_robot.last_eye_color = "green"
        manager = manager_for(mock_robot, mock_ai_provider, speak_responses=False)
        await manager.process_user_input("hi")
        assert eye_colors(mock_robot) == ["purple", "green"]

    async def test_colour_set_by_the_model_survives_the_turn(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider)
        first = tools(("set_eye_color", {"color": "blue"}))

        async def chat(messages, tools=None, system=None, on_text=None):
            if len(messages) == 1:
                return first
            await on_text("Done.")
            return text("Done.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        await manager.process_user_input("blue eyes please")
        assert eye_colors(mock_robot) == ["purple", "blue", "blue"]  # no white "speaking" flash over blue

    async def test_disabled_after_the_first_failure(self, mock_robot, mock_ai_provider):
        mock_robot.connection.bridge.set_eye_leds = AsyncMock(side_effect=BridgeError("no leds"))
        manager = manager_for(mock_robot, mock_ai_provider, speak_responses=False)
        await manager.process_user_input("hi")
        await manager.process_user_input("hi again")
        assert mock_robot.connection.bridge.set_eye_leds.await_count == 1
        assert manager._led_ok is False

    async def test_off_when_disabled(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, led_signals=False)
        await manager.process_user_input("hi")
        mock_robot.connection.bridge.set_eye_leds.assert_not_called()

    async def test_no_signals_while_halted(self, mock_ai_manager):
        mock_ai_manager.robot.halted = True
        await mock_ai_manager.process_user_input("hi")
        mock_ai_manager.robot.connection.bridge.set_eye_leds.assert_not_called()


class TestBackchannel:
    async def test_filler_when_the_model_is_slow(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, backchannel_after=0.02)

        async def chat(messages, tools=None, system=None, on_text=None):
            await asyncio.sleep(0.1)
            await on_text("Here you go.")
            return text("Here you go.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("think about it")
        assert len(result["spoken"]) == 2
        assert result["spoken"][0] in FILLERS and result["spoken"][1] == "Here you go."
        assert result["text"] == "Here you go."  # fillers are not part of the reply

    async def test_no_filler_when_the_model_is_fast(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, backchannel_after=0.05)

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("Hi.")
            await asyncio.sleep(0.1)  # still streaming after the filler deadline
            return text("Hi.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("hi")
        assert result["spoken"] == ["Hi."]

    async def test_no_filler_once_the_model_has_answered_with_a_tool_call(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, backchannel_after=0.02)
        order = []
        first = tools(("move_forward", {"distance": 0.3}))

        async def chat(messages, tools=None, system=None, on_text=None):
            if len(messages) == 1:
                return first
            await on_text("Arrived.")
            return text("Arrived.")

        async def slow_move(*args, **kwargs):
            order.append("move-start")
            await asyncio.sleep(0.08)
            order.append("move-end")
            return {"ok": True}

        mock_robot.connection.bridge.move_forward = AsyncMock(side_effect=slow_move)
        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("come here")
        assert result["spoken"] == ["Arrived."]  # no "One moment." while the robot is already moving
        assert order == ["move-start", "move-end"]

    async def test_no_filler_for_event_turns(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, backchannel_after=0.02)

        async def chat(messages, tools=None, system=None, on_text=None):
            await asyncio.sleep(0.06)
            await on_text("Ouch.")
            return text("Ouch.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        result = await manager.process_user_input("[touch] head", source="event")
        assert result["spoken"] == ["Ouch."]

    async def test_no_filler_after_a_hush(self, mock_robot, mock_ai_provider):
        manager = manager_for(mock_robot, mock_ai_provider, backchannel_after=0.02)
        release = asyncio.Event()

        async def chat(messages, tools=None, system=None, on_text=None):
            await release.wait()
            await on_text("Late answer.")
            return text("Late answer.")

        mock_ai_provider.chat = AsyncMock(side_effect=chat)
        turn = asyncio.create_task(manager.process_user_input("hi"))
        await wait_until(lambda: manager.busy)
        await manager.process_user_input("shh")
        await asyncio.sleep(0.05)
        release.set()
        result = await turn
        assert result["spoken"] == []
        mock_robot.connection.bridge.speak.assert_not_called()
