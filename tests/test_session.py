"""
Tests for session records: one folder per run, turns.jsonl and events.jsonl.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock

import main
from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall
from src.session import SessionRecorder


def lines(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def streaming(text, tool=None):
    calls = {"n": 0}

    async def chat(messages, tools=None, system=None, on_text=None):
        calls["n"] += 1
        if tool and calls["n"] == 1:
            return AIResponse(tool_calls=[ToolCall(id="t1", name=tool[0], input=tool[1])], stop_reason="tool_use")
        if on_text is not None:
            await on_text(text)
        return AIResponse(text=text, stop_reason="end_turn")

    return chat


class TestRecorder:
    def test_one_folder_per_run_with_json_lines(self, tmp_path):
        rec = SessionRecorder.start(str(tmp_path), now=datetime(2026, 10, 6, 14, 5, 9))
        assert rec.folder.endswith("2026-10-06_140509")
        rec.record_event("people", count=1)
        rec.record_turn({"text": "hello"})
        assert lines(rec.path("events.jsonl"))[0]["kind"] == "people"
        assert lines(rec.path("turns.jsonl"))[0]["text"] == "hello"

    def test_a_write_failure_does_not_raise(self, tmp_path):
        rec = SessionRecorder(str(tmp_path / "s"))
        rec.folder = str(tmp_path / "missing" / "deeper")  # cannot be written
        rec.record_turn({"text": "lost"})  # logged, not raised


class TestTurnRecords:
    async def test_a_turn_with_a_tool_and_speech(self, mock_robot, mock_ai_provider, tmp_path):
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("Turning left now.", ("turn", {"angle": 90})))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        manager.recorder = SessionRecorder(str(tmp_path))
        import time

        await manager.process_user_input("Turn left ninety degrees", source="voice", heard_at=time.monotonic())
        turn = lines(tmp_path / "turns.jsonl")[0]
        assert turn["source"] == "voice" and turn["text"] == "Turn left ninety degrees"
        assert turn["heard"] and turn["first_word_s"] is not None and turn["duration_s"] >= turn["first_word_s"]
        assert turn["tools"][0]["name"] == "turn" and turn["tools"][0]["ok"] is True
        assert turn["spoken"][-1] == "Turning left now."  # after a filler before the silent tool call

    async def test_intents_are_recorded_too(self, mock_robot, mock_ai_provider, tmp_path):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        manager.recorder = SessionRecorder(str(tmp_path))
        await manager.process_user_input("Pepper, stop", source="voice")
        turn = lines(tmp_path / "turns.jsonl")[0]
        assert turn["intent"] == "stop" and turn["stop_reason"] == "intent"


class TestSettings:
    def test_session_dir_puts_everything_in_one_folder(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SESSION_DIR", str(tmp_path))
        monkeypatch.setenv("PEPPER_FAKE_BRIDGE", "true")
        app = main.PepperEvolution()
        folder = app.recorder.folder
        assert app.settings.log_file == f"{folder}/host.log"
        assert (
            app.settings.voice_record_dir == f"{folder}/audio" and app.settings.photo_record_dir == f"{folder}/photos"
        )
