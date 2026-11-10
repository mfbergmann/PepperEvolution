"""
Tests for the orchestrator's first part (docs/ORCHESTRATOR.md): the engine registry and its agreement with
docs/DATA_FLOWS.md, session manifests, the ledger's turn ids and mind records, and outcomes.
"""

import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall
from src.orchestra import manifest
from src.orchestra.engines import (
    IMAGE,
    MIND_SENDS,
    OTHERS,
    REGISTRY,
    SOLO,
    TEXT,
    UNKNOWN,
    PolicyError,
    allowed,
    for_model,
)
from src.orchestra.ledger import OutcomeWatch, TurnIds, digest, similar
from src.session import SessionRecorder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lines(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []


class TestRegistry:
    def test_every_engine_has_its_row_in_data_flows(self):
        table = open(os.path.join(ROOT, "docs", "DATA_FLOWS.md"), encoding="utf-8").read()
        rows = [line for line in table.splitlines() if line.startswith("| **")]
        for e in REGISTRY.values():
            assert any(line.startswith("| " + e.destination) for line in rows), f"{e.name}: no row {e.destination!r}"

    def test_default_deny(self):
        assert not allowed(None, [TEXT]) and not allowed("openai", [TEXT], SOLO)
        assert not allowed("brine", [TEXT], UNKNOWN) and not allowed("brine", [TEXT], OTHERS)
        assert allowed("brine", [TEXT], SOLO) and not allowed("brine", [TEXT, IMAGE], SOLO)
        assert allowed("anthropic", MIND_SENDS, UNKNOWN)  # the tester's decision, 2026-09

    def test_which_engine_a_configured_mind_sends_to(self):
        assert for_model("claude-sonnet-5-5") == "anthropic"
        assert for_model("DeepSeek-V4-Flash-0731", "https://brine.sharcnet.ca") == "brine"
        assert for_model("qwen", "http://alien3:11434/") == "alien3"
        assert for_model("qwen", "http://alien3.tail1234.ts.net:11434") == "alien3"
        assert for_model("qwen", "http://localhost:11434") == "local-ollama"
        assert for_model("qwen", "http://localhost.example.com") is None
        assert for_model("gpt-4o") is None and for_model("gpt-4o", "http://localhost:11434") is None
        assert for_model("o3", "http://alien3:11434") is None


class TestManifest:
    def test_unknown_until_marked(self, tmp_path):
        folder = str(tmp_path)
        assert manifest.presence(folder) == UNKNOWN  # no file
        manifest.start(folder, version="0.x")
        assert manifest.presence(folder) == UNKNOWN
        with pytest.raises(PolicyError):
            manifest.require(folder, "brine")
        manifest.mark(folder, SOLO, note="alone in the office")
        manifest.require(folder, "brine")  # no raise
        data = manifest.read(folder)
        assert data["note"] == "alone in the office" and data["history"][0]["presence"] == UNKNOWN
        assert oct(os.stat(tmp_path / "session.json").st_mode)[-3:] == "600"
        manifest.start(folder)  # a restart in the same folder keeps the mark
        assert manifest.presence(folder) == SOLO
        with pytest.raises(PolicyError):
            manifest.require(folder, "brine", (TEXT, IMAGE))

    def test_others_and_damaged_files(self, tmp_path):
        manifest.mark(str(tmp_path), OTHERS)
        with pytest.raises(PolicyError):
            manifest.require(str(tmp_path), "brine")
        (tmp_path / "session.json").write_text("{not json")
        assert manifest.presence(str(tmp_path)) == UNKNOWN
        (tmp_path / "session.json").write_text('{"presence": "probably alone"}')
        assert manifest.presence(str(tmp_path)) == UNKNOWN
        with pytest.raises(ValueError):
            manifest.mark(str(tmp_path), "alone")


class TestLedgerBits:
    def test_ids_and_digests(self):
        ids = TurnIds("2026-11-10_120000")
        assert [ids.next(), ids.next()] == ["2026-11-10_120000#1", "2026-11-10_120000#2"]
        assert TurnIds().next() == "nosession#1"
        assert digest("a") == digest("a") != digest("b") and len(digest([{"x": 1}])) == 12

    def test_similar(self):
        assert similar("Can you turn left?", "can you turn left")
        assert similar("can you turn to the left", "can you turn left")
        assert not similar("turn left", "what time is it") and not similar("", "")


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TestOutcomeWatch:
    def watch(self):
        got, clock = [], FakeClock()
        return OutcomeWatch(lambda tid, kind, **d: got.append((tid, kind, d)), clock), got, clock

    def test_stop_during_and_after(self):
        w, got, clock = self.watch()
        w.started("s#1")
        w.heard("stop", "stop")
        assert got[-1][:2] == ("s#1", "stopped_during")
        w.finished("s#1", "tell me a story", "mind", {"spoken": ["Once"]}, [])
        clock.t += 3
        w.heard("be quiet", "quiet")
        assert got[-1][:2] == ("s#1", "stopped_after") and got[-1][2]["after_s"] == 3
        clock.t += 30
        n = len(got)
        w.heard("stop", "stop")
        assert len(got) == n  # too late to be about that answer

    def test_repeats(self):
        w, got, clock = self.watch()
        w.finished("s#1", "what is your name", "not_addressed", {}, [])
        clock.t += 4
        w.heard("What is your name?")
        assert got[-1][:2] == ("s#1", "repeated_after_ignored")
        w.finished("s#2", "what is your name", "mind", {"spoken": ["I'm Pepper."]}, [])
        clock.t += 5
        w.heard("what is your name")
        assert got[-1][:2] == ("s#2", "repeated")
        clock.t += 60
        n = len(got)
        w.heard("what is your name")
        assert len(got) == n

    def test_errors_tools_and_what_is_not_an_answer(self):
        w, got, clock = self.watch()
        w.finished("s#1", "hi", "mind", {"stop_reason": "error", "usage": {"error": "rate_limit"}}, [])
        w.finished("s#2", "turn", "mind", {"spoken": ["ok"]}, [{"name": "turn", "ok": False}])
        assert ("s#1", "error", {"error": "rate_limit"}) in got and got[-1][:2] == ("s#2", "tool_failed")
        w.finished("s#3", "", "mind", {"spoken": ["ouch"]}, [], person=False)  # a sensor event
        w.finished("s#4", "stop", "intent", {"spoken": ["Okay."]}, [])
        clock.t += 2
        w.heard("turn")
        assert got[-1][:2] == ("s#2", "repeated")  # still about the last answer to a person


def streaming(text, tool=None):
    calls = {"n": 0}

    async def chat(messages, tools=None, system=None, on_text=None):
        calls["n"] += 1
        if tool and calls["n"] == 1:
            return AIResponse(
                tool_calls=[ToolCall(id="t1", name=tool[0], input=tool[1])], stop_reason="tool_use", usage={"x": 1}
            )
        if on_text is not None:
            await on_text(text)
        return AIResponse(text=text, stop_reason="end_turn", model="claude-opus-5", usage={"output_tokens": 7})

    return chat


class TestTurnLedger:
    def manager(self, robot, provider, folder):
        manager = AIManager(robot, provider, speak_responses=False, tablet_subtitles=False)
        manager.recorder = SessionRecorder(str(folder))
        return manager

    async def test_ids_mind_record_and_history(self, mock_robot, mock_ai_provider, tmp_path):
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("Turning.", ("turn", {"angle": 90})))
        manager = self.manager(mock_robot, mock_ai_provider, tmp_path)
        await manager.process_user_input("Turn left", source="voice", heard_at=time.monotonic())
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("Hello."))
        await manager.process_user_input("Hello there", source="voice")
        first, second = lines(tmp_path / "turns.jsonl")
        session = os.path.basename(str(tmp_path))
        assert first["turn_id"] == f"{session}#1" and second["turn_id"] == f"{session}#2"
        mind = first["mind"]
        assert mind["engine"] == "anthropic" and mind["model"] == "claude-opus-5" and first["path"] == "mind"
        assert mind["state"].startswith("Current state:") and mind["prompt"] == digest(manager.system_prompt)
        assert [c["round"] for c in mind["calls"]] == [1, 2] and mind["calls"][0]["tools"] == ["turn"]
        assert mind["calls"][1]["first_text_s"] is not None and mind["calls"][1]["usage"] == {"output_tokens": 7}
        assert mind["history"] == [] and second["mind"]["history"] == [first["turn_id"]]
        assert first["rounds"] == 2 and first["usage"] == {"output_tokens": 7}

    async def test_paths_and_outcomes_reach_the_record(self, mock_robot, mock_ai_provider, tmp_path):
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("Here is a long story."))
        manager = self.manager(mock_robot, mock_ai_provider, tmp_path)
        await manager.process_user_input("Tell me a story", source="voice")
        await manager.process_user_input("Pepper, stop", source="voice")
        turns = lines(tmp_path / "turns.jsonl")
        assert [t["path"] for t in turns] == ["mind", "intent"]
        outcomes = lines(tmp_path / "outcomes.jsonl")
        assert outcomes[0]["turn_id"] == turns[0]["turn_id"] and outcomes[0]["kind"] == "stopped_after"

    async def test_sent_text_is_kept_when_it_differs(self, mock_robot, mock_ai_provider, tmp_path):
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("Hi."))
        manager = self.manager(mock_robot, mock_ai_provider, tmp_path)
        manager._rec = manager._new_record("look left", "voice", None)
        await manager._run_turn("[Already started: looking left.] look left", False, "voice", None)
        assert manager._rec["mind"]["sent"].startswith("[Already started")

    async def test_history_ids_follow_trimming(self, mock_robot, mock_ai_provider, tmp_path):
        mock_ai_provider.chat = AsyncMock(side_effect=streaming("ok"))
        manager = self.manager(mock_robot, mock_ai_provider, tmp_path)
        manager.history_turns = 2
        for i in range(4):
            await manager.process_user_input(f"question {i}", source="voice")
        last = lines(tmp_path / "turns.jsonl")[-1]
        assert last["mind"]["history"] == [last["turn_id"].replace("#4", "#3")]
        assert len(manager._turn_of) == 2


class TestReplayPolicy:
    def test_the_recorded_state_goes_only_where_identity_is_cleared(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "compare_minds", os.path.join(ROOT, "scripts", "compare_minds.py")
        )
        cm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cm)
        rec = {
            "at": "2026-11-10T12:00:00",
            "around": "One person in view.",
            "mind": {"state": "Current state: ...\nThe voice sounds like Tester."},
        }
        assert "sounds like" in cm.state_block(rec, identity_ok=True)
        rebuilt = cm.state_block(rec, identity_ok=False)
        assert "sounds like" not in rebuilt and "One person in view." in rebuilt


class TestLedgerSafety:
    async def test_a_failing_ledger_never_breaks_a_stop(self, mock_robot, mock_ai_provider, tmp_path):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        manager.recorder = SessionRecorder(str(tmp_path))

        def broken(*a, **k):
            raise RuntimeError("ledger down")

        manager.outcomes.heard = broken
        manager.outcomes.finished = broken
        result = await manager.process_user_input("Pepper, stop", source="voice")
        assert result["stop_reason"] == "intent"
        result = await manager.process_user_input("hello", source="voice")
        assert result["text"]

    async def test_names_in_context_are_flagged(self, mock_robot, mock_ai_provider, tmp_path):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        manager.recorder = SessionRecorder(str(tmp_path))
        await manager.process_user_input("hello", source="voice")
        manager._who_line = "The voice sounds like Tester."
        manager._rec = manager._new_record("hello again", "voice", None)
        await manager._run_turn("hello again", False, "voice", None)
        assert manager._rec["around_names"] and manager._rec["mind"]["names"]
        assert "around_names" not in lines(tmp_path / "turns.jsonl")[0]
        assert lines(tmp_path / "turns.jsonl")[0]["mind"]["names"] is False
