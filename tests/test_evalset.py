"""
Tests for the evaluation set (src/orchestra/evalset.py, docs/ORCHESTRATOR.md phase 2): which turns enter
and why, how the split is chosen and stays fixed, and that the file on disk holds keys and flags only.

All data is synthetic, written into pytest's ``tmp_path`` — never anything under ``results/``.
"""

import json
import os
import shutil
import stat
from dataclasses import asdict

from src.orchestra.evalset import (
    DEV,
    HELD_OUT,
    EvalTurn,
    build,
    load_marked_keys,
    load_outcomes,
    load_turns,
    read,
    split_for,
    summary,
    turn_key,
    write,
)


def _session(root, name, presence="solo", turns=(), outcomes=()):
    """A synthetic session folder: ``session.json`` plus optional ``turns.jsonl`` / ``outcomes.jsonl``."""
    folder = os.path.join(str(root), name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as f:
        json.dump({"presence": presence}, f)
    for fname, rows in (("turns.jsonl", turns), ("outcomes.jsonl", outcomes)):
        if rows:
            with open(os.path.join(folder, fname), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)


def _marks(path, rows=()):
    with open(str(path), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)


class TestTurnKey:
    def test_with_turn_id(self):
        assert turn_key("s1", {"turn_id": "s1#3", "at": "2026-11-05T15:47:00"}) == "s1#3"

    def test_old_form(self):
        assert turn_key("s1", {"at": "2026-11-05T15:47:00"}) == "s1@2026-11-05T15:47:00"


class TestLoaders:
    def test_missing_files_are_empty(self, tmp_path):
        assert load_turns(str(tmp_path / "nowhere")) == []
        assert load_outcomes(str(tmp_path / "nowhere")) == {}
        assert load_marked_keys(str(tmp_path / "marks.jsonl")) == set()

    def test_torn_lines_are_skipped(self, tmp_path):
        (tmp_path / "turns.jsonl").write_text("{bad json\n" + json.dumps({"source": "user"}) + "\n", encoding="utf-8")
        assert load_turns(str(tmp_path)) == [{"source": "user"}]
        (tmp_path / "outcomes.jsonl").write_text(
            '{"no": "key"}\n{"turn_id": "a#1", "kind": "error"}\n', encoding="utf-8"
        )
        assert load_outcomes(str(tmp_path)) == {"a#1": ["error"]}


class TestSplit:
    def test_deterministic_for_every_key(self):
        for key in ("a#1", "b@2026-11-05T15:47:00", "2026-11-05_154622#7"):
            for fraction in (0.2, 0.3, 0.5):
                assert split_for(key, fraction) == split_for(key, fraction)

    def test_both_sides_are_reached(self):
        assert {split_for(f"key{i}") for i in range(50)} == {HELD_OUT, DEV}

    def test_held_out_share_is_about_the_fraction(self):
        n = 1000
        held = sum(split_for(f"key{i}", fraction=0.3) == HELD_OUT for i in range(n))
        assert 0.25 <= held / n <= 0.35


class TestBuild:
    def test_missing_root_is_empty(self, tmp_path):
        assert build(str(tmp_path / "nowhere"), str(tmp_path / "marks.jsonl")) == []

    def test_only_solo_sessions_enter(self, tmp_path):
        root = tmp_path / "sessions"
        action = [{"name": "move_head", "ok": True}]
        for name, presence, key in (
            ("solo-one", "solo", "s#1"),
            ("others-one", "others", "s#2"),
            ("unknown-one", "unknown", "s#3"),
        ):
            _session(root, name, presence, turns=[{"turn_id": key, "source": "user", "text": "hi", "tools": action}])
        turns = build(str(root), str(tmp_path / "marks.jsonl"))
        assert [t.key for t in turns] == ["s#1"]

    def test_only_person_sources_enter(self, tmp_path):
        root = tmp_path / "sessions"
        action = [{"name": "move_head", "ok": True}]
        _session(
            root,
            "a",
            "solo",
            turns=[
                {"turn_id": "a#1", "source": "initiative", "tools": action},
                {"turn_id": "a#2", "source": "touch", "tools": action},
                {"turn_id": "a#3", "source": "user", "tools": []},  # no outcome, no mark, no action
            ],
        )
        assert build(str(root), str(tmp_path / "marks.jsonl")) == []

    def test_outcome_is_a_reason_to_enter(self, tmp_path):
        root = tmp_path / "sessions"
        _session(
            root,
            "a",
            "solo",
            turns=[{"turn_id": "a#1", "source": "user", "text": "x", "tools": []}],
            outcomes=[{"turn_id": "a#1", "kind": "stopped_during"}],
        )
        turns = build(str(root), str(tmp_path / "marks.jsonl"))
        assert len(turns) == 1
        t = turns[0]
        assert t.has_outcomes and not t.marked and not t.action
        assert t.weight == 1.0

    def test_a_reader_mark_is_a_reason_to_enter(self, tmp_path):
        root = tmp_path / "sessions"
        marks = str(tmp_path / "marks.jsonl")
        _session(root, "a", "solo", turns=[{"turn_id": "a#1", "source": "voice", "text": "x", "tools": []}])
        _marks(marks, [{"turn_id": "a#1", "better": "equal", "pair": ["m1", "m2"]}])
        turns = build(str(root), marks)
        assert len(turns) == 1
        t = turns[0]
        assert t.marked and not t.has_outcomes and not t.action
        assert t.weight == 1.0

    def test_an_action_is_a_reason_to_enter_and_carries_the_weight(self, tmp_path):
        root = tmp_path / "sessions"
        _session(
            root,
            "a",
            "solo",
            turns=[{"turn_id": "a#1", "source": "voice", "text": "x", "tools": [{"name": "speak", "ok": True}]}],
        )
        turns = build(str(root), str(tmp_path / "marks.jsonl"), action_weight=2.0)
        assert len(turns) == 1
        assert turns[0].action and turns[0].weight == 2.0

    def test_old_key_form_enters(self, tmp_path):
        root = tmp_path / "sessions"
        _session(
            root,
            "a",
            "solo",
            turns=[{"at": "2026-11-05T15:47:00", "source": "user", "text": "x", "tools": [{"name": "speak"}]}],
        )
        turns = build(str(root), str(tmp_path / "marks.jsonl"))
        assert [t.key for t in turns] == ["a@2026-11-05T15:47:00"]

    def test_sorted_by_key(self, tmp_path):
        root = tmp_path / "sessions"
        action = [{"name": "speak"}]
        _session(root, "bb", "solo", turns=[{"turn_id": "bb#2", "source": "user", "tools": action}])
        _session(root, "aa", "solo", turns=[{"turn_id": "aa#1", "source": "user", "tools": action}])
        assert [t.key for t in build(str(root), str(tmp_path / "marks.jsonl"))] == ["aa#1", "bb#2"]

    def test_previous_keeps_splits_when_the_fraction_changes(self, tmp_path):
        root = tmp_path / "sessions"
        keys = [f"a#{i}" for i in range(30)]
        _session(
            root, "a", "solo", turns=[{"turn_id": k, "source": "user", "tools": [{"name": "speak"}]} for k in keys]
        )
        marks = str(tmp_path / "marks.jsonl")
        first = build(str(root), marks, fraction=0.3)
        raw_09 = build(str(root), marks, fraction=0.9)  # without `previous` the split would move
        assert any(r.split != f.split for r, f in zip(raw_09, first))
        again = build(str(root), marks, previous=first, fraction=0.9)
        assert [t.split for t in again] == [t.split for t in first]

    def test_keys_are_never_dropped(self, tmp_path):
        root = tmp_path / "sessions"
        _session(root, "a", "solo", turns=[{"turn_id": "a#1", "source": "user", "tools": [{"name": "speak"}]}])
        marks = str(tmp_path / "marks.jsonl")
        first = build(str(root), marks)
        # the session leaves solo, then the folder leaves the tree: the key stays on the same side
        with open(os.path.join(root, "a", "session.json"), "w", encoding="utf-8") as f:
            json.dump({"presence": "others"}, f)
        assert [t.key for t in build(str(root), marks, previous=first)] == ["a#1"]
        shutil.rmtree(os.path.join(str(root), "a"))
        again = build(str(root), marks, previous=first)
        assert [t.key for t in again] == ["a#1"] and again[0].split == first[0].split

    def test_new_keys_are_added_to_a_previous_set(self, tmp_path):
        root = tmp_path / "sessions"
        marks = str(tmp_path / "marks.jsonl")
        _session(root, "a", "solo", turns=[{"turn_id": "a#1", "source": "user", "tools": [{"name": "speak"}]}])
        first = build(str(root), marks)
        _session(root, "b", "solo", turns=[{"turn_id": "b#1", "source": "user", "tools": [{"name": "speak"}]}])
        again = build(str(root), marks, previous=first)
        assert {t.key for t in again} == {"a#1", "b#1"}


class TestWriteRead:
    def test_round_trip(self, tmp_path):
        turns = [
            EvalTurn("a#1", "a", "voice", True, False, True, 2.0, HELD_OUT),
            EvalTurn("b@2026-11-05T15:47:00", "b", "user", False, True, False, 1.0, DEV),
        ]
        path = str(tmp_path / "nested" / "folder" / "evalset.jsonl")
        write(turns, path)
        assert read(path) == turns

    def test_mode_is_0600(self, tmp_path):
        path = str(tmp_path / "evalset.jsonl")
        write([], path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_the_file_holds_only_the_fields(self, tmp_path):
        path = str(tmp_path / "evalset.jsonl")
        write([EvalTurn("a#1", "a", "user", True, True, True, 2.0, DEV)], path)
        row = json.loads(open(path, encoding="utf-8").readline())
        assert set(row) == {"key", "session", "source", "has_outcomes", "marked", "action", "weight", "split"}

    def test_read_tolerates_missing_and_bad_lines(self, tmp_path):
        assert read(str(tmp_path / "missing.jsonl")) == []
        good = json.dumps(asdict(EvalTurn("a#1", "a", "user", True, True, True, 2.0, DEV)))
        path = str(tmp_path / "evalset.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(good + "\nnot json\n")
        assert read(path) == [EvalTurn("a#1", "a", "user", True, True, True, 2.0, DEV)]


class TestSummary:
    def test_counts_and_weight(self):
        turns = [
            EvalTurn("a", "s", "user", True, True, False, 1.0, HELD_OUT),
            EvalTurn("b", "s", "voice", False, False, True, 2.0, DEV),
        ]
        s = summary(turns)
        assert s["total"] == 2
        assert s["by_split"] == {HELD_OUT: 1, DEV: 1}
        assert s["weight"] == {HELD_OUT: 1.0, DEV: 2.0}
        assert s["by_reason"] == {"outcomes": 1, "marked": 1, "action": 1}

    def test_empty(self):
        s = summary([])
        assert s["total"] == 0
        assert s["by_split"] == {HELD_OUT: 0, DEV: 0}
        assert s["by_reason"] == {"outcomes": 0, "marked": 0, "action": 0}
