"""
Tests for the command router (src/decide/router.py) and how the manager starts routed actions.
"""

import asyncio
import json

import httpx

from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall
from src.decide import DecisionClient
from src.decide.router import START_AT_ONCE, amount, plan


def router_client(choice, p):
    def handler(request):
        questions = json.loads(request.content)["questions"]
        if "to_pepper" in questions:
            return httpx.Response(200, json={"answers": {"to_pepper": {"type": "noul", "noul": 0.95}}})
        return httpx.Response(200, json={"answers": {"action": {"choice": choice, "probabilities": {choice: p}}}})

    return DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler))


class TestAmounts:
    def test_wednesdays_transcripts(self):
        assert amount("Turn left ninety degrees", 90) == 90
        assert amount("Turn a little bit farther", 90) == 30
        assert amount("Turn left thirty degrees, then move forward one meter", 90) == 30
        assert amount("turn right 45 degrees", 90) == 45
        assert amount("Turn left", 90) == 90  # default

    def test_plans(self):
        assert plan("turn_left", 0.9, "Turn left ninety degrees").args == {"angle": 90.0}
        assert plan("turn_right", 0.9, "turn right 45 degrees").args == {"angle": -45.0}
        assert plan("turn_around", 0.9, "face the other way").args == {"angle": 180.0}
        assert plan("look_right", 0.9, "glance right").args == {"yaw": -60.0, "pitch": 0}
        assert plan("wave", 0.9, "give us a wave").tool == "play_animation"
        assert plan("stop", 0.9, "hang on a sec").intent == "stop"
        assert plan("turn_left", 0.9, "turn left 400 degrees").args == {"angle": 180.0}  # capped

    def test_drives_and_talk_go_to_claude(self):
        for action in ("come_here", "move_forward", "move_back", "spin_circle", "talk"):
            assert action not in START_AT_ONCE and plan(action, 0.99, "x") is None


def turn_then_words(text="Turning left now."):
    """A model that also calls turn (the duplicate the router must absorb), then speaks."""
    calls = {"n": 0}

    async def chat(messages, tools=None, system=None, on_text=None):
        calls["n"] += 1
        chat.seen = messages[-1]["content"] if calls["n"] == 1 else chat.seen
        if calls["n"] == 1:
            return AIResponse(tool_calls=[ToolCall(id="t1", name="turn", input={"angle": 90})], stop_reason="tool_use")
        if on_text is not None:
            await on_text(text)
        return AIResponse(text=text, stop_reason="end_turn")

    return chat


def routed_manager(mock_robot, mock_ai_provider, choice, p):
    return AIManager(
        mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False, decider=router_client(choice, p)
    )


class TestRoutingInTheManager:
    async def test_a_routed_turn_starts_at_once_and_runs_only_once(self, mock_robot, mock_ai_provider):
        chat = turn_then_words()
        mock_ai_provider.chat.side_effect = chat
        manager = routed_manager(mock_robot, mock_ai_provider, "turn_left", 0.95)
        result = await manager.process_user_input("Turn left ninety degrees", source="voice", open_mic=True)
        mock_robot.connection.bridge.move_turn.assert_awaited_once_with(90.0)  # the model's own turn was absorbed
        assert chat.seen.startswith("[Already started: turning left 90 degrees.")
        assert result["tool_calls"][0]["result"].find("already_done") > 0
        assert mock_robot.direct_commands_running == 0

    async def test_below_the_threshold_nothing_starts(self, mock_robot, mock_ai_provider):
        manager = routed_manager(mock_robot, mock_ai_provider, "turn_left", 0.5)
        await manager.process_user_input("Turn a bit", source="voice", open_mic=True)
        await asyncio.sleep(0)
        mock_robot.connection.bridge.move_turn.assert_not_awaited()

    async def test_a_drive_is_left_to_claude(self, mock_robot, mock_ai_provider):
        manager = routed_manager(mock_robot, mock_ai_provider, "come_here", 0.97)
        await manager.process_user_input("Come over here please", source="voice", open_mic=True)
        await asyncio.sleep(0)
        mock_robot.connection.bridge.move_forward.assert_not_awaited()
        mock_robot.connection.bridge.move_turn.assert_not_awaited()

    async def test_a_paraphrased_stop_uses_the_intent_path(self, mock_robot, mock_ai_provider):
        manager = routed_manager(mock_robot, mock_ai_provider, "stop", 0.9)
        result = await manager.process_user_input("Whoa whoa, hold it right there", source="voice", open_mic=True)
        assert result["intent"] == "stop"
        mock_robot.connection.bridge.stop.assert_awaited()
        mock_ai_provider.chat.assert_not_called()

    async def test_nothing_starts_while_a_turn_runs(self, mock_robot, mock_ai_provider):
        manager = routed_manager(mock_robot, mock_ai_provider, "wave", 0.95)
        async with manager._lock:
            routed = await manager._choose_action("", "Give us a wave", {})
        assert routed is None

    async def test_events_are_never_routed(self, mock_robot, mock_ai_provider):
        manager = routed_manager(mock_robot, mock_ai_provider, "wave", 0.99)
        await manager.process_user_input("[Sensor event] Someone walked up.", source="event")
        await asyncio.sleep(0)
        mock_robot.connection.bridge.play_animation.assert_not_awaited()


def words_only(text="On my left there's a cabinet."):
    async def chat(messages, tools=None, system=None, on_text=None):
        if on_text is not None:
            await on_text(text)
        return AIResponse(text=text, stop_reason="end_turn")

    return chat


class TestRouterFollowUps:
    async def test_look_back_after_a_routed_head_move(self, mock_robot, mock_ai_provider, monkeypatch):
        from src.world import WorldModel

        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.0)
        world = WorldModel()
        await world.handle_event(
            "sensors",
            {"people_count": 1, "people": [{"id": 1, "distance": 1.4, "looking": True, "yaw": 10.0, "pitch": -15.0}]},
        )
        mock_ai_provider.chat.side_effect = words_only()
        manager = routed_manager(mock_robot, mock_ai_provider, "look_left", 0.97)
        manager.world = world
        await manager.process_user_input("Look to your left and tell me what's there", source="voice", open_mic=True)
        for _ in range(5):
            await asyncio.sleep(0.01)
        calls = mock_robot.connection.bridge.move_head.await_args_list
        assert calls[0].args[:2] == (60.0, 0)  # the routed look
        assert calls[-1].args[:2] == (10.0, -15.0)  # and back to the person, though Claude made no head call

    async def test_a_failed_routed_action_is_not_reported_as_done(self, mock_robot, mock_ai_provider):
        mock_robot.connection.bridge.move_turn.side_effect = [RuntimeError("robot is resting"), {"ok": True}]
        mock_ai_provider.chat.side_effect = turn_then_words()
        manager = routed_manager(mock_robot, mock_ai_provider, "turn_left", 0.95)
        result = await manager.process_user_input("Turn left ninety degrees", source="voice", open_mic=True)
        assert mock_robot.connection.bridge.move_turn.await_count == 2  # the model's own call really ran
        assert "already_done" not in result["tool_calls"][0]["result"]

    async def test_nothing_is_routed_while_resting(self, mock_robot, mock_ai_provider):
        mock_robot.state.awake = False
        manager = routed_manager(mock_robot, mock_ai_provider, "wave", 0.99)
        assert await manager._choose_action("", "Give us a wave", {}) is None


class TestOnTheRobotFixes:
    """From the 2026-10-08 session: handshake, look at me, misheard follow-ups."""

    def test_shake_hand_is_a_real_handshake(self):
        routed = plan("shake_hand", 0.98, "Put your hand out let's shake on it")
        assert routed.tool == "offer_hand"

    async def test_routed_handshake_holds_the_hand_out(self, mock_robot, mock_ai_provider):
        mock_robot.connection.bridge.offer_hand = __import__("unittest.mock").mock.AsyncMock(
            return_value={"taken": True, "waited": 1.2}
        )
        mock_ai_provider.chat.side_effect = words_only("Here's my hand, nice to meet you!")
        manager = routed_manager(mock_robot, mock_ai_provider, "shake_hand", 0.98)
        await manager.process_user_input("Put your hand out let's shake on it", source="voice", open_mic=True)
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.offer_hand.assert_awaited_once()

    async def test_look_at_me_faces_forward_and_answers_when_nobody_is_detected(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        result = await manager.process_user_input("Look at me", source="voice")
        assert result["intent"] == "look_at_me" and result["spoken"] == ["Looking at you."]
        mock_robot.connection.bridge.move_head.assert_awaited_with(0.0, -18.0, 0.3, wait=False)

    async def test_a_misheard_follow_up_is_answered_in_a_conversation(self, mock_robot, mock_ai_provider):
        def handler(request):
            questions = json.loads(request.content)["questions"]
            if "to_pepper" in questions:
                return httpx.Response(200, json={"answers": {"to_pepper": {"type": "noul", "noul": 0.45}}})
            return httpx.Response(200, json={"answers": {"action": {"choice": "talk", "probabilities": {"talk": 0.8}}}})

        mock_ai_provider.chat.side_effect = words_only("Yes, I can hear you!")
        manager = AIManager(
            mock_robot,
            mock_ai_provider,
            speak_responses=True,
            tablet_subtitles=False,
            decider=DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler)),
        )
        first = await manager.process_user_input("Not if you can hear me", source="voice", open_mic=True)
        assert first["stop_reason"] == "not_addressed"  # no conversation yet, nobody seen looking
        manager._last_answered_at = manager._clock()  # Pepper just answered someone
        second = await manager.process_user_input("God, if you could hear me", source="voice", open_mic=True)
        assert second["stop_reason"] != "not_addressed"
