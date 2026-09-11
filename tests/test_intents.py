"""
Tests for the local intent table (control phrases answered without a model call).
"""

import pytest
from unittest.mock import AsyncMock

from src.ai.intents import INTENTS, IntentExecutor, match_intent, normalise
from src.pepper.bridge_client import BridgeError


class TestNormalise:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Hey Pepper, STOP!", "stop"),
            ("okay pepper be quiet.", "be quiet"),
            ("  Wake   up ", "wake up"),
            ("Pepper stop moving...", "stop moving"),
            ("hi", ""),
            ("", ""),
            ("Look at me?", "look at me"),
            ("please stop", "stop"),
            ("stop, please", "stop"),
            ("Pepper, stop now please!", "stop"),
            ("history", "history"),
            ("okstop", "okstop"),
        ],
    )
    def test_normalise(self, raw, expected):
        assert normalise(raw) == expected


class TestMatchIntent:
    @pytest.mark.parametrize(
        "phrase, name",
        [
            ("stop", "stop"),
            ("Stop!", "stop"),
            ("Pepper, stop moving", "stop"),
            ("hey pepper freeze", "stop"),
            ("wait", "stop"),
            ("emergency stop", "emergency_stop"),
            ("e-stop", "emergency_stop"),
            ("kill the motors", "emergency_stop"),
            ("halt", "stop"),
            ("stop it", "stop"),
            ("please stop", "stop"),
            ("stop now", "stop"),
            ("stop everything", "stop"),
            ("be quiet", "quiet"),
            ("quiet", "quiet"),
            ("shhh", "quiet"),
            ("stop talking", "quiet"),
            ("wake up", "wake_up"),
            ("stand up", "wake_up"),
            ("sit down", "rest"),
            ("go to sleep", "rest"),
            ("look at me", "look_at_me"),
            ("Pepper, look straight ahead.", "look_ahead"),
            ("look forward", "look_ahead"),
        ],
    )
    def test_matches(self, phrase, name):
        intent = match_intent(phrase)
        assert intent is not None and intent.name == name

    @pytest.mark.parametrize(
        "phrase",
        [
            "",
            "hello",
            "stop by the kitchen and tell me what you see",
            "wait, what is your name?",
            "can you stop?",
            "tell me a story about a robot that would not stop",
            "look at me and describe what I'm wearing",
            "please rest your hand on the table",
            "okstop",
            "pepperstop",
            "history",
            "x" * 60,
        ],
    )
    def test_no_match(self, phrase):
        assert match_intent(phrase) is None

    def test_every_intent_has_description_and_phrases(self):
        names = [i.name for i in INTENTS]
        assert len(names) == len(set(names))
        for intent in INTENTS:
            assert intent.description and intent.phrases
            for phrase in intent.phrases:
                assert phrase == phrase.strip()

    def test_stop_family_aborts_and_hushes(self):
        assert match_intent("stop").aborts_turn and match_intent("stop").hushes
        assert match_intent("emergency stop").aborts_turn and match_intent("emergency stop").hushes
        assert match_intent("quiet").hushes and not match_intent("quiet").aborts_turn
        assert not match_intent("wake up").aborts_turn and not match_intent("wake up").hushes


class TestIntentExecutor:
    async def test_emergency_stop(self, mock_robot):
        result = await IntentExecutor(mock_robot).execute(match_intent("emergency stop"))
        assert result == {"intent": "emergency_stop", "ok": True, "text": ""}
        assert mock_robot.halted is True
        mock_robot.connection.bridge.emergency_stop.assert_awaited_once()

    async def test_stop_silences_and_stops_motion(self, mock_robot):
        result = await IntentExecutor(mock_robot).execute(match_intent("stop"))
        assert result["ok"] and result["text"] == "Okay."
        mock_robot.connection.bridge.stop_speaking.assert_awaited_once()
        mock_robot.connection.bridge.stop.assert_awaited_once()
        mock_robot.connection.bridge.emergency_stop.assert_not_awaited()
        assert mock_robot.halted is False

    async def test_quiet_only_stops_speech(self, mock_robot):
        await IntentExecutor(mock_robot).execute(match_intent("be quiet"))
        mock_robot.connection.bridge.stop_speaking.assert_awaited_once()
        mock_robot.connection.bridge.stop.assert_not_awaited()

    async def test_wake_up_clears_halt(self, mock_robot):
        mock_robot.halted = True
        result = await IntentExecutor(mock_robot).execute(match_intent("wake up"))
        assert result["text"] == "I'm up."
        mock_robot.connection.bridge.wake_up.assert_awaited_once()
        assert mock_robot.halted is False

    async def test_rest(self, mock_robot):
        result = await IntentExecutor(mock_robot).execute(match_intent("sit down"))
        assert result["text"] == "Resting."
        mock_robot.connection.bridge.rest.assert_awaited_once()

    async def test_look_at_me_enables_awareness(self, mock_robot):
        await IntentExecutor(mock_robot).execute(match_intent("look at me"))
        assert mock_robot.connection.bridge.set_awareness.await_args.args == (True,)

    async def test_look_ahead_disables_awareness_and_centres_head(self, mock_robot):
        await IntentExecutor(mock_robot).execute(match_intent("look ahead"))
        assert mock_robot.connection.bridge.set_awareness.await_args.args == (False,)
        assert mock_robot.connection.bridge.move_head.call_args.args[:2] == (0, 0)

    async def test_failure_is_reported_not_raised(self, mock_robot):
        mock_robot.connection.bridge.rest = AsyncMock(side_effect=BridgeError("motors off"))
        result = await IntentExecutor(mock_robot).execute(match_intent("rest"))
        assert result["ok"] is False
        assert result["error"] == "motors off"
