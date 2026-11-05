"""
Voice identification (src/identity): the store's identity table, comparing, reading a clip, the second-voice watch,
enrolment, and the live wiring in VoiceInput and the manager. Fake embeddings only: no model, no real audio, no names
of real people.
"""

import asyncio
import importlib.util
import json
import sqlite3
import sys
import wave
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import pytest

from src.ai.manager import AIManager
from src.audio import FakeTranscriber, VoiceInput
from src.audio.pcm import tone
from src.decide import DecisionClient
from src.identity import (
    Enrolled,
    SecondVoiceWatch,
    VoiceIdentifier,
    VoiceReading,
    best_match,
    from_blob,
    load_enrolled,
    merge_readings,
    read_voice,
    robust_centroid,
    to_blob,
    unit,
)
from src.identity.voice import PRESENT_SCORE, SAMPLE_RATE, window_starts
from src.memory import ConsentRequired, Memory, MemoryStore
from src.session import SessionRecorder
from src.world import WorldModel

ROOT = Path(__file__).resolve().parents[1]
A = unit([1.0, 0.0, 0.0, 0.0])
B = unit([0.0, 1.0, 0.0, 0.0])
MODEL = "fake-model"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def enrolled_store(tmp_path=None, now=datetime.now):
    store = MemoryStore(":memory:" if tmp_path is None else str(tmp_path / "pepper.sqlite"), now=now)
    person = store.add_person("Mira", '"yes" (voice)')
    store.add_identity(person.id, "voice", to_blob(A), MODEL, 12, "she asked for it")
    return store, person


class TestStore:
    def test_identity_is_stored_and_read_back(self):
        store, person = enrolled_store()
        identity = store.identities(kind="voice", model=MODEL)[0]
        assert (identity.person_id, identity.name, identity.samples) == (person.id, "Mira", 12)
        assert np.allclose(from_blob(identity.embedding), A)
        assert store.counts()["identity"] == 1

    def test_kinds_and_models_are_kept_apart(self):
        store, person = enrolled_store()
        store.add_identity(person.id, "face", to_blob(B), "face-model", 3, "she asked for it")
        assert [i.kind for i in store.identities(person_id=person.id)] == ["face", "voice"]
        assert store.identities(kind="voice", model="another-model") == []
        assert [e.name for e in load_enrolled(store, "face", "face-model")] == ["Mira"]
        with pytest.raises(ValueError):
            store.add_identity(person.id, "gait", to_blob(A), MODEL, 1, "note")

    def test_enrolling_again_replaces(self):
        store, person = enrolled_store()
        store.add_identity(person.id, "voice", to_blob(B), MODEL, 20, "again")
        assert len(store.identities()) == 1
        assert np.allclose(from_blob(store.identities()[0].embedding), B)

    def test_nobody_is_enrolled_without_consent_or_a_record(self):
        store, person = enrolled_store()
        with pytest.raises(ConsentRequired):
            store.add_identity(person.id, "voice", to_blob(A), MODEL, 1, "  ")
        with pytest.raises(ConsentRequired):
            store.add_identity(999, "voice", to_blob(A), MODEL, 1, "a note")

    def test_forget_deletes_the_identity_with_the_person(self, tmp_path):
        store, person = enrolled_store(tmp_path)
        assert store.forget(person.id)
        assert store.identities() == [] and store.counts()["identity"] == 0
        store.close()
        # gone from the file and the write-ahead log too, not just from the table
        for path in tmp_path.iterdir():
            assert to_blob(A) not in path.read_bytes()

    def test_forgetting_someone_else_leaves_the_identity(self):
        store, person = enrolled_store()
        other = store.add_person("Noor", '"yes"')
        store.forget(other.id)
        assert len(store.identities()) == 1

    def test_ordinary_people_expire_with_their_identity_after_90_days(self):
        clock = [datetime(2026, 1, 1)]
        store, person = enrolled_store(now=lambda: clock[0])
        clock[0] += timedelta(days=89)
        store.sweep()
        assert len(store.identities()) == 1
        clock[0] += timedelta(days=2)
        assert store.sweep()["people"] == 1
        assert store.person(person.id) is None and store.identities() == []

    def test_written_consent_keeps_them_past_expiry_until_forgotten(self):
        clock = [datetime(2026, 1, 1)]
        store, person = enrolled_store(now=lambda: clock[0])
        store.keep_written(person.id, "asked in writing")
        store.add_fact("likes tea", person_id=person.id)
        clock[0] += timedelta(days=1000)
        swept = store.sweep()
        assert swept["people"] == 0 and swept["facts"] == 1  # facts still follow the 90-day rule
        assert store.person(person.id).written_consent == "asked in writing" and len(store.identities()) == 1
        # a spoken yes later does not undo it, and a new fact still gets its own 90 days
        store.renew(person.id, '"yes"')
        assert store.person(person.id).written_consent
        store.add_fact("likes coffee", person_id=person.id)
        clock[0] += timedelta(days=80)
        assert store.sweep()["facts"] == 0
        assert store.forget(person.id) and store.identities() == []

    def test_written_consent_needs_a_note(self):
        store, person = enrolled_store()
        with pytest.raises(ConsentRequired):
            store.keep_written(person.id, " ")

    def test_a_version_2_file_gains_the_identity_table(self, tmp_path):
        path = tmp_path / "pepper.sqlite"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                consent_at TEXT NOT NULL, consent_note TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT,
                expires_at TEXT NOT NULL);
            INSERT INTO people VALUES (1, 'Mira', 'mira', '2026-01-01', 'yes', '2026-01-01', NULL, '2026-04-01');
            PRAGMA user_version = 2;
            """)
        old.close()
        for _ in range(2):  # opening twice must not try to migrate twice
            store = MemoryStore(str(path), now=lambda: datetime(2026, 2, 1))
            assert store.person(1).name == "Mira" and store.person(1).written_consent is None
            assert store._db.execute("PRAGMA user_version").fetchone()[0] == 3
            store.close()
        store = MemoryStore(str(path), now=lambda: datetime(2026, 2, 1))
        store.add_identity(1, "voice", to_blob(A), MODEL, 1, "note")
        assert store.forget(1) and store.identities() == []


class TestCompare:
    def test_blob_round_trip(self):
        assert np.allclose(from_blob(to_blob([3.0, 4.0])), [0.6, 0.8])

    def test_no_direction_is_refused(self):
        with pytest.raises(ValueError):
            unit([0.0, 0.0])

    def test_best_match(self):
        people = [Enrolled(1, "Mira", A), Enrolled(2, "Noor", B)]
        match = best_match(unit([0.9, 0.1, 0.0, 0.0]), people)
        assert match.person_id == 1 and match.score > 0.9
        assert best_match(A, []) is None

    def test_the_centroid_drops_a_clip_that_does_not_fit(self):
        rng = np.random.default_rng(0)
        samples = [unit(A + 0.1 * rng.normal(size=4)) for _ in range(10)]
        centroid, kept = robust_centroid(samples + [B])
        assert 10 not in kept and len(kept) >= 8
        assert float(centroid @ A) > 0.98

    def test_a_few_samples_are_all_kept(self):
        _, kept = robust_centroid([A, B])
        assert kept == [0, 1]
        with pytest.raises(ValueError):
            robust_centroid([])


def embed_by_loudness(samples):
    """A fake model: a loud stretch is voice A, a quiet one voice B, whatever else."""
    return A if float(np.abs(samples).mean()) > 0.25 else B


def clip(*parts):
    """Seconds of "voice A" (positive) or "voice B" (negative)."""
    return np.concatenate([np.full(int(abs(s) * SAMPLE_RATE), 0.5 if s > 0 else 0.1, dtype=np.float32) for s in parts])


class TestReading:
    people = [Enrolled(7, "Mira", A)]

    def test_the_enrolled_voice_is_named(self):
        reading = read_voice(embed_by_loudness, clip(4.0), self.people)
        assert (reading.name, reading.person_id, reading.second_voice) == ("Mira", 7, False)
        assert reading.score == pytest.approx(1.0) and reading.low == pytest.approx(1.0)

    def test_an_unknown_voice_gets_no_name(self):
        reading = read_voice(embed_by_loudness, clip(-4.0), self.people)
        assert reading.name is None and reading.person_id is None and reading.second_voice

    def test_a_long_clip_is_sampled_evenly(self):
        starts = window_starts(60 * SAMPLE_RATE)
        assert len(starts) == 8 and starts[0] == 0 and starts[-1] > 50 * SAMPLE_RATE
        assert starts == sorted(set(starts))

    def test_a_part_in_another_voice_withholds_the_name(self):
        reading = read_voice(embed_by_loudness, clip(5.0, -2.0), self.people)
        assert reading.score == pytest.approx(1.0)  # the fake's whole clip is mostly loud: the windows must catch it
        assert reading.low < 0.15 and reading.high == pytest.approx(1.0)
        assert reading.second_voice and reading.name is None

    def test_too_short_or_nobody_enrolled_says_nothing(self):
        calls = []

        def embed(samples):
            calls.append(len(samples))
            return A

        assert read_voice(embed, clip(1.0), self.people).score is None
        assert read_voice(embed, clip(4.0), []).score is None
        assert calls == []  # no work at all

    def test_the_record_holds_numbers_and_no_vector(self):
        record = read_voice(embed_by_loudness, clip(4.0), self.people).record()
        assert record["name"] == "Mira" and record["windows"] == len(window_starts(4 * SAMPLE_RATE))
        json.dumps(record)
        assert "second_voice" not in record

    def test_a_joined_utterance_is_as_mixed_as_its_worst_part(self):
        known = read_voice(embed_by_loudness, clip(4.0), self.people)
        mixed = read_voice(embed_by_loudness, clip(5.0, -2.0), self.people)
        merged = merge_readings([known, mixed])
        assert merged.second_voice and merged.name is None and merged.low == mixed.low
        assert merge_readings([known, known]).name == "Mira"
        assert merge_readings([known]) is known


def reading(low=0.5, high=0.6, second_voice=False):
    return VoiceReading(3.0, windows=4, score=0.5, low=low, high=high, second_voice=second_voice)


class TestSecondVoiceWatch:
    def test_two_voices_in_one_clip(self):
        watch = SecondVoiceWatch()
        assert watch.update(reading(low=0.05, second_voice=True), 100.0)
        assert watch.active(100.0) and watch.active(159.0) and not watch.active(160.0)

    def test_after_the_enrolled_voice(self):
        watch = SecondVoiceWatch()
        assert not watch.update(reading(), 100.0)
        assert watch.update(reading(low=0.05, high=0.2, second_voice=True), 110.0)

    def test_a_visitor_alone_is_not_a_second_voice(self):
        """Everyone who is not enrolled scores low, and the enrolled voice was not around."""
        watch = SecondVoiceWatch()
        for t in range(10):
            assert not watch.update(reading(low=0.05, high=PRESENT_SCORE - 0.1, second_voice=True), float(t))
        assert not watch.active(10.0)

    def test_the_enrolled_voice_long_gone_does_not_count(self):
        watch = SecondVoiceWatch()
        watch.update(reading(), 0.0)
        assert not watch.update(reading(low=0.05, high=0.2, second_voice=True), 1000.0)

    def test_a_reading_with_nothing_to_compare_changes_nothing(self):
        watch = SecondVoiceWatch()
        assert not watch.update(VoiceReading(3.0), 0.0)
        assert watch.enrolled_at is None


class FakeModel:
    name = MODEL
    embed = staticmethod(embed_by_loudness)


class TestIdentifier:
    async def test_reads_off_the_loop_and_forget_holds_at_once(self):
        store, person = enrolled_store()
        identifier = VoiceIdentifier(store, model=FakeModel())
        await identifier.start()
        pcm = (clip(4.0) * 32767).astype(np.int16).tobytes()
        first = await identifier.read(pcm)
        assert first.name == "Mira" and first.spent > 0.0
        store.forget(person.id)
        assert (await identifier.read(pcm)).score is None
        identifier.close()

    async def test_only_the_same_model_is_compared(self):
        store, person = enrolled_store()
        model = SimpleNamespace(name="another-model", embed=embed_by_loudness)
        identifier = VoiceIdentifier(store, model=model)
        pcm = (clip(4.0) * 32767).astype(np.int16).tobytes()
        assert (await identifier.read(pcm)).score is None
        identifier.close()


class TestEnrolScript:
    def write_clip(self, folder, name, seconds):
        folder.mkdir(parents=True, exist_ok=True)
        with wave.open(str(folder / name), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            fh.writeframes(b"\x10\x20" * int(seconds * SAMPLE_RATE))

    @pytest.fixture
    def enrol(self, tmp_path, monkeypatch):
        script = load_script("enroll_identity")
        monkeypatch.setattr(
            script, "SpeakerModel", lambda path, threads: SimpleNamespace(name=MODEL, embed=lambda s: A)
        )
        session = tmp_path / "session" / "audio"
        self.write_clip(session, "utterance_20260101_100000_000_1.wav", 3.0)
        self.write_clip(session, "utterance_20260101_110000_000_2.wav", 3.0)
        self.write_clip(session, "utterance_20260101_120000_000_3.wav", 1.0)  # too short
        store = MemoryStore.open(str(tmp_path / "memory"))
        store.add_person("Mira", '"yes"')
        store.close()
        base = ["--dir", str(tmp_path / "memory"), "--person", "Mira", "--consent", "she asked"]
        return lambda *more: script.main(base + list(more)), tmp_path / "session", tmp_path / "memory"

    def test_enrols_with_consent_and_skips_short_clips(self, enrol, capsys):
        run, session, memory = enrol
        run(str(session))
        store = MemoryStore.open(str(memory))
        identity = store.identities()[0]
        assert identity.samples == 2 and identity.consent_note == "she asked" and identity.model == MODEL
        assert store.people()[0].written_consent is None  # the 90-day rule, started again
        assert "1 too short" in capsys.readouterr().out

    def test_written_consent_keeps_them(self, enrol):
        run, session, memory = enrol
        run(str(session), "--written-consent")
        assert MemoryStore.open(str(memory)).people()[0].written_consent == "she asked"

    def test_a_time_limits_the_clips(self, enrol):
        run, session, memory = enrol
        run(f"{session}@110000")
        assert MemoryStore.open(str(memory)).identities()[0].samples == 1

    def test_a_dry_run_stores_nothing(self, enrol):
        run, session, memory = enrol
        run(str(session), "--dry-run")
        assert MemoryStore.open(str(memory)).identities() == []

    def test_a_name_not_stored_creates_nobody(self, enrol, tmp_path):
        script = sys.modules["enroll_identity"]
        with pytest.raises(SystemExit):
            script.main(["--dir", str(tmp_path / "memory"), "--person", "Nobody", "--consent", "x", str(tmp_path)])
        assert len(MemoryStore.open(str(tmp_path / "memory")).people()) == 1

    def test_consent_is_required(self, enrol, tmp_path):
        script = sys.modules["enroll_identity"]
        with pytest.raises(SystemExit):
            script.main(["--dir", str(tmp_path / "memory"), "--person", "Mira", str(tmp_path)])
        with pytest.raises(SystemExit):
            script.main(["--dir", str(tmp_path / "memory"), "--person", "Mira", "--consent", " ", str(tmp_path)])


def decider(p):
    def handler(request):
        questions = json.loads(request.content)["questions"]
        if "to_pepper" in questions:
            return httpx.Response(200, json={"answers": {"to_pepper": {"noul": p}}})
        return httpx.Response(200, json={"answers": {"action": {"choice": "none", "probabilities": {"none": 0.9}}}})

    return DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler))


def finished(value):
    future = asyncio.get_running_loop().create_future()
    if isinstance(value, Exception):
        future.set_exception(value)
    else:
        future.set_result(value)
    return future


class TestManager:
    def manager(self, mock_robot, mock_ai_provider, p=0.3, voice_id=True, second_voice=True, memory=None):
        world = WorldModel()
        ai = AIManager(
            mock_robot, mock_ai_provider, decider=decider(p), world=world, memory=memory, greet_newcomers=False
        )
        ai.use_voice_identity(voice_id, second_voice)
        ai.prompted = []
        real = mock_ai_provider.chat

        async def chat(*args, **kwargs):
            ai.prompted.append(str(kwargs))
            return await real(*args, **kwargs)

        mock_ai_provider.chat = chat
        return ai, world

    named = VoiceReading(3.0, windows=4, score=0.6, low=0.4, high=0.7, person_id=7, name="Mira")
    mixed = VoiceReading(3.0, windows=4, score=0.3, low=0.05, high=0.6, second_voice=True)

    async def answer_once(self, ai):
        """Pepper answered just now, so the conversation bypass applies to what comes next."""
        ai._last_answered_at = ai._clock()

    async def test_the_mind_is_told_whose_voice_it_was(self, mock_robot, mock_ai_provider):
        ai, world = self.manager(mock_robot, mock_ai_provider, p=0.9)
        result = await ai.process_user_input("hello", source="voice", open_mic=True, voice_reading=finished(self.named))
        assert result["stop_reason"] != "not_addressed"
        assert "The voice sounds like Mira." in ai.prompted[0]
        assert ai._who_line == ""  # for one turn only
        assert world.timeline.recent(kinds=["recognised"])[0].data["person_id"] == 7

    async def test_no_hint_with_the_flag_off(self, mock_robot, mock_ai_provider):
        ai, world = self.manager(mock_robot, mock_ai_provider, p=0.9, voice_id=False)
        await ai.process_user_input("hello", source="voice", open_mic=True, voice_reading=finished(self.named))
        assert "sounds like" not in ai.prompted[0]
        assert not world.timeline.recent(kinds=["recognised"])

    async def test_a_recognised_voice_goes_on_the_speaker_and_in_memory(self, mock_robot, mock_ai_provider):
        store, person = enrolled_store()
        memory = Memory(store)
        ai, world = self.manager(mock_robot, mock_ai_provider, p=0.9, memory=memory)
        track = world.attach_voice(0.2)
        await ai.process_user_input(
            "hello", source="voice", open_mic=True, voice_reading=finished(replace(self.named, person_id=person.id))
        )
        assert track.person_id == person.id
        assert memory.met == {person.id: "Mira"}
        assert memory.here_line() == ""  # a voice match is not the same as having told Pepper who they are

    async def test_a_second_voice_turns_the_bypass_off(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.3)
        await self.answer_once(ai)
        result = await ai.process_user_input(
            "and then we left", source="voice", open_mic=True, voice_reading=finished(self.mixed)
        )
        assert result["stop_reason"] == "not_addressed"

    async def test_without_the_flag_the_bypass_stays(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.3, second_voice=False)
        await self.answer_once(ai)
        result = await ai.process_user_input(
            "and then we left", source="voice", open_mic=True, voice_reading=finished(self.mixed)
        )
        assert result["stop_reason"] != "not_addressed"

    async def test_the_effect_ends_after_a_minute(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.3)
        now = [1000.0]
        ai._clock = lambda: now[0]
        ai.second_voice.update(self.mixed, now[0])
        now[0] += 61.0
        await self.answer_once(ai)
        result = await ai.process_user_input("go on", source="voice", open_mic=True)
        assert result["stop_reason"] != "not_addressed"

    async def test_a_clear_request_is_still_answered_with_a_second_voice(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.9)
        await self.answer_once(ai)
        result = await ai.process_user_input(
            "Pepper, wave", source="voice", open_mic=True, voice_reading=finished(self.mixed)
        )
        assert result["stop_reason"] != "not_addressed"

    async def test_stop_is_never_gated_and_does_not_wait(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.0)
        ai.second_voice.update(self.mixed, ai._clock())
        never = asyncio.get_running_loop().create_future()
        started = ai._clock()
        for words in ("stop", "be quiet"):
            result = await ai.process_user_input(words, source="voice", open_mic=True, voice_reading=never)
            assert result["stop_reason"] != "not_addressed"
        assert ai._clock() - started < 0.2
        never.cancel()

    async def test_a_routed_stop_gets_past_a_second_voice(self, mock_robot, mock_ai_provider):
        def handler(request):
            questions = json.loads(request.content)["questions"]
            if "to_pepper" in questions:
                return httpx.Response(200, json={"answers": {"to_pepper": {"noul": 0.1}}})
            return httpx.Response(
                200, json={"answers": {"action": {"choice": "stop", "probabilities": {"stop": 0.99}}}}
            )

        ai, _ = self.manager(mock_robot, mock_ai_provider)
        ai.decider = DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler))
        await self.answer_once(ai)
        result = await ai.process_user_input(
            "that is enough now", source="voice", open_mic=True, voice_reading=finished(self.mixed)
        )
        assert result["stop_reason"] != "not_addressed"

    async def test_a_failed_reading_never_breaks_the_turn(self, mock_robot, mock_ai_provider, tmp_path):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.9)
        ai.recorder = SessionRecorder(str(tmp_path))
        result = await ai.process_user_input(
            "hello", source="voice", open_mic=True, voice_reading=finished(RuntimeError("model gone"))
        )
        assert result["stop_reason"] != "not_addressed" and "sounds like" not in ai.prompted[0]
        turn = json.loads((Path(ai.recorder.folder) / "turns.jsonl").read_text().splitlines()[0])
        assert "model gone" in turn["identity"]["error"]

    async def test_a_late_reading_holds_nobody_up_and_counts_for_the_next_turn(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.3)
        await self.answer_once(ai)
        slow = asyncio.get_running_loop().create_future()
        started = ai._clock()
        task = asyncio.ensure_future(ai.process_user_input("first", source="voice", open_mic=True, voice_reading=slow))
        result = await asyncio.wait_for(task, 3.0)
        assert result["stop_reason"] != "not_addressed"  # no reading in time: as before
        assert ai._clock() - started < 2.0
        slow.set_result(self.mixed)
        await asyncio.sleep(0)
        assert ai.second_voice.active(ai._clock())
        assert "sounds like" not in ai.prompted[0]

    async def test_a_reading_is_not_waited_for_when_it_cannot_change_the_answer(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.9, voice_id=False)
        await self.answer_once(ai)
        never = asyncio.get_running_loop().create_future()
        started = ai._clock()
        await ai.process_user_input("Pepper, wave", source="voice", open_mic=True, voice_reading=never)
        assert ai._clock() - started < 0.4
        never.cancel()

    async def test_the_turn_record_shows_the_decision(self, mock_robot, mock_ai_provider, tmp_path):
        ai, _ = self.manager(mock_robot, mock_ai_provider, p=0.3)
        ai.recorder = SessionRecorder(str(tmp_path))
        await self.answer_once(ai)
        await ai.process_user_input("side talk", source="voice", open_mic=True, voice_reading=finished(self.mixed))
        folder = Path(ai.recorder.folder)
        turn = json.loads((folder / "turns.jsonl").read_text().splitlines()[0])
        assert turn["identity"]["second_voice_signal"] is True and turn["identity"]["low"] == 0.05
        assert turn["addressee"]["second_voice"] is True and turn["addressee"]["second_voice_decided"] is True
        text = load_script("review_session").review(str(folder))
        assert "SECOND VOICE signalled" in text and "because of a second voice" in text
        assert "voices read: 1" in text


class FakeIdentifier:
    def __init__(self, result=None, fails=False, start_fails=False):
        self.result = result
        self.fails = fails
        self.start_fails = start_fails
        self.heard = []

    async def start(self):
        if self.start_fails:
            raise FileNotFoundError("no model")

    def enrolled(self):
        return []

    def read(self, pcm):
        self.heard.append(len(pcm))
        if self.fails:
            raise RuntimeError("store locked")
        return finished(self.result)

    def close(self):
        pass


class FakeSource:
    def __init__(self):
        self.callback = None
        self.connected = True
        self.name = "robot"

    def on_audio(self, callback):
        self.callback = callback

    async def start(self):
        pass

    async def stop(self):
        pass

    async def push(self, pcm):
        for i in range(0, len(pcm), 3200):
            await self.callback(pcm[i : i + 3200])


class TestVoiceInput:
    async def run(self, identifier, refiner=None):
        manager = MagicMock()
        manager.process_user_input = AsyncMock(return_value={"text": ""})
        manager.signal_state = AsyncMock()
        source = FakeSource()
        voice = VoiceInput(
            manager,
            FakeTranscriber(["look at me"], streaming=True, endpoint_after=1.0),
            source=source,
            identifier=identifier,
            refiner=refiner,
        )
        await voice.start()
        await source.push(tone(1.2))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if manager.process_user_input.await_count:
                break
        return manager.process_user_input, voice

    async def test_each_final_is_read_and_the_reading_handed_on(self):
        identifier = FakeIdentifier(TestManager.named)
        call, voice = await self.run(identifier)
        reading = call.await_args.kwargs["voice_reading"]
        assert reading.result().name == "Mira" and identifier.heard[0] > 0
        assert voice.status()["voice_id"] is True

    async def test_without_an_identifier_nothing_changes(self):
        call, voice = await self.run(None)
        assert "voice_reading" not in call.await_args.kwargs
        assert voice.status()["voice_id"] is False

    async def test_a_failure_to_start_reading_loses_only_the_reading(self):
        call, _ = await self.run(FakeIdentifier(fails=True))
        assert call.await_count == 1 and "voice_reading" not in call.await_args.kwargs

    async def test_a_model_that_will_not_load_turns_identification_off(self):
        call, voice = await self.run(FakeIdentifier(start_fails=True))
        assert voice.identifier is None and call.await_count == 1

    async def test_a_joined_utterance_merges_its_readings(self):
        manager = MagicMock()
        voice = VoiceInput(manager, FakeTranscriber([], streaming=True))
        from src.audio import Transcript

        one = Transcript("and then", final=True, readings=(finished(TestManager.named),))
        two = Transcript("the rest", final=True, readings=(finished(TestManager.mixed),))
        joined = voice._join(one, two)
        merged = await voice._reading_of(joined)
        assert merged.second_voice and merged.name is None


class TestSettings:
    def test_off_by_default(self, monkeypatch):
        import main

        for key in ("VOICE_ID", "SECOND_VOICE", "VOICE_ID_THRESHOLD", "SECOND_VOICE_THRESHOLD"):
            monkeypatch.setenv(key, "")
        settings = main.Settings.from_env()
        assert settings.voice_id is False and settings.second_voice is False
        assert settings.voice_id_threshold == 0.40 and settings.second_voice_threshold == 0.15

    def test_they_need_the_long_term_store(self):
        import main

        app = SimpleNamespace(
            settings=SimpleNamespace(voice_id=True, second_voice=False), memory=None, logger=MagicMock()
        )
        assert main.PepperEvolution.build_identifier(app) is None
        app.logger.warning.assert_called_once()
        app.settings = SimpleNamespace(voice_id=False, second_voice=False)
        assert main.PepperEvolution.build_identifier(app) is None
