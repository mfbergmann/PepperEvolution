"""
Face identification (src/identity/face.py): scoring, votes, what is kept and forgotten, the store, enrolment, working
memory and the review. Fake embeddings only: no model, no real images, no names of real people.
"""

import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.ai.manager import AIManager
from src.identity import (
    FaceIdentifier,
    FrameVotes,
    Gallery,
    gallery_blob,
    gallery_score,
    load_galleries,
    read_faces,
    unit,
)
from src.identity import face as face_module
from src.identity.face import KIND, Match, Seen, bearing_of
from src.memory import Memory, MemoryStore
from src.perception import FrameWatcher
from src.perception.geometry import offsets
from src.world import WorldModel
from src.world.observations import IDENTITY

ROOT = Path(__file__).resolve().parents[1]
MODEL = "fake-face-model"
A = unit([1.0, 0.0, 0.0, 0.0])
B = unit([0.0, 1.0, 0.0, 0.0])
C = unit([0.0, 0.0, 1.0, 0.0])
NEAR_A = unit([1.0, 0.1, 0.0, 0.0])


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gallery_rows(vector=A, n=4):
    return np.stack([vector] * n)


def enrolled_store(tmp_path=None, vector=A):
    store = MemoryStore(":memory:" if tmp_path is None else str(tmp_path / "pepper.sqlite"))
    person = store.add_person("Mira", '"yes" (voice)')
    store.add_identity(person.id, KIND, gallery_blob(gallery_rows(vector)), MODEL, 4, "she asked for it")
    return store, person


def seen(vector=A, x=0.5, width=40.0):
    return Seen(x, 0.5, width, vector)


class FakeModel:
    """Stands in for FaceModel: each ``faces`` call gives the next frame's faces, or raises."""

    name = MODEL

    def __init__(self, frames=None):
        self.frames = list(frames or [])
        self.calls = 0
        self.release = None  # an Event to wait on, to hold a read open

    def faces(self, jpeg):
        self.calls += 1
        if self.release is not None:
            self.release.wait(5)
        result = self.frames.pop(0) if self.frames else []
        if isinstance(result, Exception):
            raise result
        return len(result), result


class Recorder:
    def __init__(self):
        self.events = []

    def record_event(self, kind, **data):
        self.events.append({"kind": kind, **data})


def identifier(store, frames=None, clock=None, world=None, memory=None, recorder=None):
    world = world if world is not None else WorldModel()
    return FaceIdentifier(
        store,
        world=world,
        memory=memory,
        recorder=recorder,
        model=FakeModel(frames),
        clock=clock or (lambda: 0.0),
    )


async def feed(faces, frames, head_yaw=0.0):
    """Offer ``frames`` different frames, each read to the end."""
    for _ in range(frames):
        faces.offered_frame = getattr(faces, "offered_frame", 0) + 1
        faces.offer(b"frame %d" % faces.offered_frame, head_yaw)
        for _ in range(200):
            if not faces._busy:
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0)


class TestScoring:
    def test_a_gallery_blob_reads_back_as_unit_rows(self):
        store, person = enrolled_store()
        store.add_identity(person.id, KIND, gallery_blob(np.stack([A * 3, B * 2])), MODEL, 2, "again")
        loaded = load_galleries(store, MODEL)
        assert [(g.person_id, g.name, g.vectors.shape) for g in loaded] == [(person.id, "Mira", (2, 4))]
        assert np.allclose(np.linalg.norm(loaded[0].vectors, axis=1), 1.0)

    def test_a_blob_needs_rows(self):
        with pytest.raises(ValueError):
            gallery_blob(A)

    def test_the_score_is_the_mean_of_the_three_best(self):
        rows = np.stack([A, A, unit([1.0, 1.0, 0.0, 0.0]), B, C])
        assert gallery_score(A, rows) == pytest.approx((1.0 + 1.0 + 2**-0.5) / 3)

    def test_a_small_gallery_uses_what_it_has(self):
        assert gallery_score(A, np.stack([A])) == pytest.approx(1.0)

    def test_below_the_threshold_leaves_no_match(self):
        galleries = [Gallery(1, "Mira", gallery_rows())]
        assert read_faces([seen(B)], galleries) == ()
        assert [m.person_id for m in read_faces([seen(NEAR_A)], galleries)] == [1]

    def test_a_face_goes_to_the_closest_person_and_each_person_to_their_best_face(self):
        galleries = [Gallery(1, "Mira", gallery_rows(A)), Gallery(2, "Theo", gallery_rows(B))]
        matches = read_faces([seen(B, x=0.2), seen(NEAR_A, x=0.4), seen(A, x=0.8)], galleries)
        assert {(m.person_id, m.x) for m in matches} == {(2, 0.2), (1, 0.8)}

    def test_bearing_is_the_head_plus_the_place_in_the_frame(self):
        match = Match(1, "Mira", 0.7, 0.75, 0.5, 40.0)
        assert bearing_of(match, 10.0) == pytest.approx(10.0 + offsets(0.75, 0.5)[0])
        assert bearing_of(match, 10.0) < 10.0  # the right of the frame is to the right: negative
        assert bearing_of(match, None) is None


class TestVotes:
    def test_three_of_the_last_five(self):
        votes = FrameVotes()
        assert votes.update({1}) == set()
        assert votes.update({1}) == set()
        assert votes.update(set()) == set()
        assert votes.update({1}) == {1}

    def test_leaving_the_view_ends_it_within_five_frames(self):
        votes = FrameVotes()
        for _ in range(5):
            votes.update({1})
        held = [votes.update(set()) for _ in range(5)]
        assert held[1] == {1} and held[2] == set()

    def test_people_vote_apart(self):
        votes = FrameVotes()
        for people in ({1, 2}, {1}, {1, 2}):
            held = votes.update(people)
        assert held == {1}


class TestIdentifier:
    async def test_two_frames_are_not_a_recognition_three_are(self):
        store, person = enrolled_store()
        world = WorldModel()
        faces = identifier(store, [[seen()]] * 3, world=world)
        await feed(faces, 2)
        assert not world.timeline.recent(kinds=["recognised"])
        await feed(faces, 1)
        event = world.timeline.recent(kinds=["recognised"])[0]
        assert (event.data["via"], event.data["person_id"], event.data["name"]) == ("face", person.id, "Mira")
        assert event.data["score"] == pytest.approx(1.0)

    async def test_a_repeated_frame_is_no_new_vote(self):
        store, _ = enrolled_store()
        world = WorldModel()
        faces = identifier(store, [[seen()]] * 5, world=world)
        for _ in range(5):
            faces.offer(b"the same frame", 0.0)
            await asyncio.sleep(0.01)
        assert faces.model.calls == 1
        assert faces.counts.repeats == 4
        assert not world.timeline.recent(kinds=["recognised"])

    async def test_frames_are_dropped_while_one_is_being_read(self):
        store, _ = enrolled_store()
        faces = identifier(store)
        faces.model.release = threading.Event()
        faces.offer(b"one")
        faces.offer(b"two")
        faces.offer(b"three")
        assert faces.counts.dropped == 2
        faces.model.release.set()
        await asyncio.sleep(0.05)
        assert faces.model.calls == 1 and not faces._busy

    async def test_offering_returns_while_the_model_is_slow(self):
        store, _ = enrolled_store()
        faces = identifier(store)
        faces.model.release = threading.Event()
        faces.offer(b"one")  # would hang here if it read on this thread
        assert faces._busy
        faces.model.release.set()
        await asyncio.sleep(0.02)

    async def test_with_nobody_enrolled_no_face_is_even_looked_for(self):
        store = MemoryStore(":memory:")
        faces = identifier(store)
        await feed(faces, 3)
        assert faces.model.calls == 0

    async def test_a_failing_model_never_reaches_the_stream(self):
        store, _ = enrolled_store()
        faces = identifier(store, [RuntimeError("no"), [seen()], [seen()], [seen()]])
        await feed(faces, 4)
        assert faces.counts.failed == 1 and faces.last_error == "RuntimeError"
        assert not faces._busy

    async def test_a_failing_store_never_reaches_the_stream(self):
        store, _ = enrolled_store()
        faces = identifier(store)
        store.close()
        faces.offer(b"one")
        assert faces.counts.failed == 1 and not faces._busy

    async def test_working_memory_hears_on_winning_the_vote_then_only_now_and_then(self):
        store, person = enrolled_store()
        now = [0.0]
        recorder = Recorder()
        notes = []
        world = WorldModel(clock=lambda: now[0])
        world.add_sink(lambda obs: notes.append(obs) if obs.source == IDENTITY else None)
        memory = Memory(store)
        faces = identifier(store, [[seen()]] * 40, clock=lambda: now[0], world=world, memory=memory, recorder=recorder)
        for _ in range(40):
            await feed(faces, 1)
            now[0] += 1.0
        assert len(notes) == 3  # at frame 3, then each 15 s
        assert memory.met == {person.id: "Mira"}
        assert len(world.timeline.recent(kinds=["recognised"])) == 1  # one line for recall

    async def test_only_someone_enrolled_is_ever_recorded(self):
        """Faces that match nobody: only how many, never a score, a size or a place."""
        store, _ = enrolled_store()
        recorder = Recorder()
        faces = identifier(store, [[seen(B, width=33.0)], [seen(C, width=33.0)]], recorder=recorder)
        await feed(faces, 2)
        faces.close()
        assert [e["kind"] for e in recorder.events] == ["face_stats"]
        stats = recorder.events[0]
        assert stats["with_face"] == 2 and stats["matched"] == 0
        assert not {"score", "width", "name", "person_id"} & set(stats)

    async def test_nothing_recorded_holds_a_vector_or_a_name(self):
        store, _ = enrolled_store()
        recorder = Recorder()
        faces = identifier(store, [[seen()]] * 3, recorder=recorder)
        await feed(faces, 3)
        faces.close()
        text = json.dumps(recorder.events)
        assert "Mira" not in text and "vector" not in text and "embedding" not in text
        last = next(e for e in reversed(recorder.events) if e["kind"] == "face")
        assert last["held"] is True and last["votes"] == 3 and last["width"] == 40.0

    async def test_losing_them_is_recorded(self):
        store, person = enrolled_store()
        recorder = Recorder()
        faces = identifier(store, [[seen()]] * 3, recorder=recorder)
        await feed(faces, 6)
        assert {"kind": "face_lost", "person_id": person.id} in recorder.events


class TestForgetting:
    async def test_a_forgotten_face_stops_at_the_next_frame(self):
        store, person = enrolled_store()
        world = WorldModel()
        faces = identifier(store, [[seen()]] * 10, world=world)
        await feed(faces, 3)
        calls = faces.model.calls
        store.forget(person.id)
        await feed(faces, 3)
        assert faces.model.calls == calls  # with nobody enrolled, no more reading
        assert world.known == {}

    async def test_a_frame_in_flight_when_they_are_forgotten_does_not_count(self):
        store, person = enrolled_store()
        world = WorldModel()
        faces = identifier(store, [[seen()]] * 4, world=world)
        await feed(faces, 2)
        faces.model.release = threading.Event()
        faces.offer(b"in flight", 0.0)
        store.forget(person.id)
        faces.model.release.set()
        await asyncio.sleep(0.05)
        assert not world.timeline.recent(kinds=["recognised"])
        assert world.known == {} and faces._held == set()

    async def test_the_tool_clears_working_memory_at_once(self):
        store, person = enrolled_store()
        world = WorldModel()
        memory = Memory(store, world=world)
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.0}])
        faces = identifier(store, [[seen()]] * 3, world=world, memory=memory)
        await feed(faces, 3)
        assert "Mira is here" in world.summary()
        assert world.tracker.tracks[0].person_id == person.id
        assert memory.forget_person("Mira")["forgotten"]
        assert "Mira" not in world.summary()
        assert world.tracker.tracks[0].person_id is None
        assert "Mira" not in json.dumps(world.recall())
        assert store.identities(kind=KIND) == []

    def test_the_gallery_goes_with_the_person_and_leaves_the_file(self, tmp_path):
        store, person = enrolled_store(tmp_path)
        store.add_identity(person.id, "voice", A.tobytes(), "fake-voice", 1, "she asked for it")
        blob = store.identities(kind=KIND)[0].embedding
        assert blob in (tmp_path / "pepper.sqlite").read_bytes() + (tmp_path / "pepper.sqlite-wal").read_bytes()
        store.forget(person.id)
        assert store.identities(person_id=person.id) == []
        for path in tmp_path.iterdir():
            assert blob not in path.read_bytes()

    def test_enrolling_again_replaces_the_gallery(self):
        store, person = enrolled_store()
        store.add_identity(person.id, KIND, gallery_blob(gallery_rows(B, 2)), MODEL, 2, "again")
        loaded = load_galleries(store, MODEL)
        assert len(loaded) == 1 and loaded[0].vectors.shape == (2, 4)


class TestWorld:
    def recognise(self, world, bearing=0.0, person_id=7, name="Mira"):
        world.note(IDENTITY, "recognised", via="face", person_id=person_id, name=name, score=0.7, bearing=bearing)

    def test_a_face_goes_on_the_person_in_its_direction(self):
        world = WorldModel()
        world.handle_people(2, [{"id": 1, "yaw": 30.0, "distance": 1.5}, {"id": 2, "yaw": -30.0, "distance": 2.0}])
        self.recognise(world, bearing=-27.0)
        by_yaw = {round(t.bearing(world.pose)): t for t in world.tracker.tracks}
        assert by_yaw[30].person_id is None
        assert [t.person_id for t in world.tracker.tracks if t.person_id] == [7]
        assert world.timeline.recent(kinds=["recognised"])[0].data["track"] is not None

    def test_a_face_nowhere_near_a_track_is_a_new_one_from_the_camera(self):
        world = WorldModel()
        world.handle_people(1, [{"id": 1, "yaw": 40.0, "distance": 1.5}])
        self.recognise(world, bearing=-20.0)
        new = world.tracker.tracks[-1]
        assert len(world.tracker.tracks) == 2 and new.person_id == 7 and new.sources == ["camera"]

    def test_one_person_is_on_one_track(self):
        world = WorldModel()
        world.handle_people(2, [{"id": 1, "yaw": 30.0, "distance": 1.5}, {"id": 2, "yaw": -30.0, "distance": 2.0}])
        self.recognise(world, bearing=30.0)
        self.recognise(world, bearing=-30.0)
        assert sum(t.person_id == 7 for t in world.tracker.tracks) == 1

    def test_no_direction_or_a_turning_base_attaches_nothing_but_they_are_still_here(self):
        world = WorldModel()
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.5}])
        self.recognise(world, bearing=None)
        assert world.tracker.tracks[0].person_id is None
        assert "Mira is here" in world.summary()
        world._moving = 1
        self.recognise(world, bearing=0.0)
        assert world.tracker.tracks[0].person_id is None

    def test_the_line_says_who_and_carries_no_facts_and_no_pronoun(self):
        world = WorldModel()
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.5}])
        self.recognise(world)
        assert world.summary().endswith("Mira is here (you know them: say recall to look them up).")
        for word in (" he ", " she ", " her ", " his ", " him "):
            assert word not in world.known_line()

    def test_it_fades_when_nothing_refreshes_it(self):
        now = [0.0]
        world = WorldModel(clock=lambda: now[0])
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.5}])
        self.recognise(world)
        now[0] += 29.0
        assert "Mira" in world.summary()
        now[0] += 2.0
        assert "Mira" not in world.summary()

    def test_two_people_in_one_mention(self):
        world = WorldModel()
        self.recognise(world, person_id=7, name="Mira")
        self.recognise(world, person_id=8, name="Theo")
        assert world.known_line() == "Mira and Theo are here (you know them: say recall to look them up)."

    def test_face_and_voice_are_one_mention(self):
        world = WorldModel()
        self.recognise(world)
        world.note(IDENTITY, "recognised", via="voice", person_id=7, name="Mira", score=0.6)
        assert world.known_line().count("Mira") == 1
        assert world.known_line(speaking=7) == (
            "Mira is here and the voice you are answering sounds like them "
            "(you know them: say recall to look them up)."
        )

    def test_a_voice_alone_is_left_to_its_own_line_while_it_speaks_and_is_here_after(self):
        world = WorldModel()
        world.note(IDENTITY, "recognised", via="voice", person_id=7, name="Mira", score=0.6)
        assert world.known_line(speaking=7) == ""
        assert "Mira is here" in world.known_line()

    def test_different_people_by_face_and_voice_both_stand(self):
        world = WorldModel()
        self.recognise(world, person_id=7, name="Mira")
        world.note(IDENTITY, "recognised", via="voice", person_id=8, name="Theo", score=0.6)
        assert world.known_line(speaking=8) == "Mira is here (you know them: say recall to look them up)."

    def test_the_timeline_hears_each_way_once_in_a_while(self):
        now = [0.0]
        world = WorldModel(clock=lambda: now[0])
        for _ in range(5):
            self.recognise(world)
            now[0] += 15.0
        world.note(IDENTITY, "recognised", via="voice", person_id=7, name="Mira", score=0.6)
        assert [e.data["via"] for e in world.timeline.recent(kinds=["recognised"])] == ["face", "voice"]
        now[0] += 120.0
        self.recognise(world)
        assert len(world.timeline.recent(kinds=["recognised"])) == 3


class TestManager:
    def manager(self, robot, provider, memory=None):
        world = WorldModel()
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.5}])
        ai = AIManager(robot, provider, world=world, memory=memory, greet_newcomers=False)
        ai.use_voice_identity(True, False)
        ai.prompted = []
        real = provider.chat

        async def chat(*args, **kwargs):
            ai.prompted.append(str(kwargs))
            return await real(*args, **kwargs)

        provider.chat = chat
        return ai, world

    @staticmethod
    def reading(person_id=7, name="Mira"):
        from src.identity import VoiceReading

        future = asyncio.get_running_loop().create_future()
        future.set_result(VoiceReading(3.0, windows=4, score=0.6, low=0.4, high=0.7, person_id=person_id, name=name))
        return future

    async def test_seen_and_heard_is_one_sentence(self, mock_robot, mock_ai_provider):
        ai, world = self.manager(mock_robot, mock_ai_provider)
        TestWorld().recognise(world)
        await ai.process_user_input("hello", source="voice", voice_reading=self.reading())
        prompt = ai.prompted[0]
        assert "Mira is here and the voice you are answering sounds like them" in prompt
        assert "The voice sounds like" not in prompt
        assert prompt.count("Mira") == 1

    async def test_heard_alone_keeps_the_voice_line(self, mock_robot, mock_ai_provider):
        ai, _ = self.manager(mock_robot, mock_ai_provider)
        await ai.process_user_input("hello", source="voice", voice_reading=self.reading())
        assert "The voice sounds like Mira." in ai.prompted[0]
        assert "Mira is here" not in ai.prompted[0]

    async def test_seen_alone_is_the_around_line(self, mock_robot, mock_ai_provider):
        ai, world = self.manager(mock_robot, mock_ai_provider)
        TestWorld().recognise(world)
        await ai.process_user_input("hello", source="voice")
        assert "Mira is here (you know them: say recall to look them up)." in ai.prompted[0]
        assert "The voice" not in ai.prompted[0]

    async def test_a_voice_read_as_someone_just_forgotten_is_dropped(self, mock_robot, mock_ai_provider):
        store = MemoryStore(":memory:")
        person = store.add_person("Mira", '"yes" (voice)')
        memory = Memory(store)
        ai, world = self.manager(mock_robot, mock_ai_provider, memory=memory)
        store.forget(person.id)
        await ai.process_user_input("hello", source="voice", voice_reading=self.reading(person.id))
        assert "Mira" not in ai.prompted[0]
        assert world.known == {} and memory.met == {}

    def test_a_recognition_gives_no_standing(self):
        """A social cue: it never links someone to the conversation the way telling Pepper their name does."""
        store = MemoryStore(":memory:")
        person = store.add_person("Mira", '"yes" (voice)')
        memory = Memory(store)
        memory.recognised(person.id, "Mira")
        assert memory.here_line() == ""


class FakeDecider:
    async def ask(self, *args, **kwargs):
        return {}


class TestFrameWatcher:
    def watcher(self, faces, watch=False, count=0):
        world = SimpleNamespace(count=count, note=lambda *a, **k: None, update_seen=lambda *a, **k: None, pose=None)
        return FrameWatcher("ws://x", FakeDecider(), world, AsyncMock(), watch=watch, faces=faces)

    def offers(self):
        got = []
        return got, SimpleNamespace(
            offer=lambda jpeg, head_yaw=None: got.append((jpeg, head_yaw)), status=lambda: {"read": len(got)}
        )

    async def test_every_frame_is_offered_with_the_heads_angle(self):
        got, faces = self.offers()
        watcher = self.watcher(faces, count=1)
        watcher._text(json.dumps({"type": "frame", "head": [12.0, 3.0]}))
        watcher.frame(b"not really a jpeg")
        await asyncio.sleep(0)
        assert got == [(b"not really a jpeg", 12.0)]
        assert watcher.status()["faces"] == {"read": 1}

    async def test_also_while_only_watching_a_room_the_detector_calls_empty(self):
        got, faces = self.offers()
        watcher = self.watcher(faces, watch=True, count=None)
        watcher.frame(b"not really a jpeg")
        await asyncio.sleep(0)
        assert len(got) == 1

    async def test_also_while_the_judgement_is_busy(self):
        got, faces = self.offers()
        watcher = self.watcher(faces, count=1)
        watcher._busy = True
        watcher.frame(b"not really a jpeg")
        assert len(got) == 1 and watcher.dropped == 1

    async def test_off_by_default(self):
        watcher = self.watcher(None, count=1)
        watcher.frame(b"not really a jpeg")
        await asyncio.sleep(0)
        assert "faces" not in watcher.status()


class TestEnrolment:
    def run(self, tmp_path, *extra, person=("--person", "Mira"), vectors=None, recogniser="w600k_r50.onnx"):
        script = load_script("enroll_identity")
        path = tmp_path / "gallery.npz"
        vectors = np.stack([unit(np.eye(512)[i] + np.eye(512)[0]) for i in range(1, 6)]) if vectors is None else vectors
        np.savez(path, embeddings=vectors, recogniser=recogniser)
        args = [*person, "--consent", "their words", "--dir", str(tmp_path), "--face-gallery", str(path)]
        script.main(args + ["--face-model", "/models/face_w600k_r50.onnx", *extra])
        return vectors

    def store(self, tmp_path):
        store = MemoryStore.open(str(tmp_path))
        return store, store.add_person("Mira", '"yes" (voice)')

    def test_the_gallery_is_stored_whole(self, tmp_path, capsys):
        store, person = self.store(tmp_path)
        vectors = self.run(tmp_path)
        loaded = load_galleries(store, "face_w600k_r50")
        assert loaded[0].vectors.shape == (5, 512) and loaded[0].person_id == person.id
        assert np.allclose(loaded[0].vectors, vectors)
        identity = store.identities(kind="face")[0]
        assert identity.samples == 5 and identity.consent_note == "their words"
        assert "Mira" not in capsys.readouterr().out
        assert (tmp_path / "pepper.sqlite").stat().st_mode & 0o077 == 0  # this user's alone

    def test_by_number(self, tmp_path):
        store, person = self.store(tmp_path)
        self.run(tmp_path, person=("--person-id", str(person.id)))
        assert store.identities(kind="face")

    def test_written_consent_keeps_them(self, tmp_path):
        store, person = self.store(tmp_path)
        self.run(tmp_path, "--written-consent")
        assert store.person(person.id).written_consent == "their words"

    def test_a_dry_run_stores_nothing(self, tmp_path):
        store, _ = self.store(tmp_path)
        self.run(tmp_path, "--dry-run")
        assert store.identities(kind="face") == []

    @pytest.mark.parametrize(
        "bad",
        [
            {"recogniser": "w600k_mbf.onnx"},
            {"vectors": np.ones((3, 128), dtype=np.float32)},
            {"vectors": np.full((3, 512), np.nan, dtype=np.float32)},
        ],
    )
    def test_a_gallery_from_something_else_is_refused(self, tmp_path, bad):
        store, _ = self.store(tmp_path)
        with pytest.raises(SystemExit):
            self.run(tmp_path, **bad)
        assert store.identities(kind="face") == []

    def test_nobody_new_is_created(self, tmp_path):
        store, _ = self.store(tmp_path)
        with pytest.raises(SystemExit):
            self.run(tmp_path, person=("--person", "Nobody"))
        assert store.identities(kind="face") == []

    def test_clips_or_a_gallery_not_both(self, tmp_path):
        self.store(tmp_path)
        script = load_script("enroll_identity")
        with pytest.raises(SystemExit):
            script.main(["--person", "Mira", "--consent", "x", "--dir", str(tmp_path)])


class TestReview:
    def test_the_counts_are_in_the_summary(self, tmp_path):
        review = load_script("review_session")
        (tmp_path / "turns.jsonl").write_text("")
        lines = [
            {"kind": "observation", "source": "identity", "obs": "recognised", "via": "face", "person_id": 1},
            {
                "kind": "face_stats",
                "offered": 10,
                "repeats": 1,
                "dropped": 2,
                "read": 7,
                "with_face": 5,
                "matched": 4,
                "failed": 0,
                "median_ms": 12.0,
                "max_ms": 99.0,
            },
        ]
        (tmp_path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
        text = review.review(str(tmp_path))
        assert "faces: 7 of 10 frames read" in text and "working memory told 1 times" in text


def test_faces_are_off_by_default():
    assert face_module.THRESHOLD == 0.45 and face_module.VOTES == 3 and face_module.VOTE_FRAMES == 5
    import main

    assert main.Settings.__dataclass_fields__["face_id"].default is False


class TestFacePlaces:
    async def test_every_face_is_kept_as_a_direction_only(self):
        store, _ = enrolled_store()
        now = [10.0]
        faces = identifier(store, frames=[[seen(A, x=0.5), seen(B, x=0.9)]], clock=lambda: now[0])
        await feed(faces, 1, head_yaw=20.0)
        bearings = faces.faces_since(0.0)
        assert len(bearings) == 2 and bearings[0] == pytest.approx(20.0)  # the enrolled face and a stranger's
        assert bearings[1] < 20.0  # right of the middle of the frame: right of the head
        assert faces.faces_since(11.0) == []
        assert all(isinstance(b, float) for b in bearings)  # nothing but a number per face

    async def test_without_the_head_angle_a_face_has_no_direction(self):
        store, _ = enrolled_store()
        faces = identifier(store, frames=[[seen(B)]])
        await feed(faces, 1, head_yaw=None)
        assert faces.faces_since(0.0) == [None]
