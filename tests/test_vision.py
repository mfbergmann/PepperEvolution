"""
Tests for camera judgements (src/perception/vision.py) and how they reach the world model and the manager.
"""

import asyncio
from unittest.mock import AsyncMock

from src.ai.manager import AIManager
from src.perception import FrameWatcher
from src.world import WorldModel


class FakeDecider:
    def __init__(self, answers, delay=0.0):
        self.answers = list(answers)
        self.delay = delay
        self.calls = []

    async def ask(self, model, state, questions, images=None, timeout=None):
        self.calls.append((model, images))
        await asyncio.sleep(self.delay)
        probs = self.answers.pop(0) if self.answers else {}
        return {k: {"type": "noul", "noul": v} for k, v in probs.items()}


def watcher(answers, clock, delay=0.0, world=None):
    events = []

    async def on_event(kind, data):
        events.append(data)

    world = world or WorldModel(clock=lambda: clock[0])
    w = FrameWatcher("ws://pepper:8888/ws/camera", FakeDecider(answers, delay), world, on_event, clock=lambda: clock[0])
    return w, events, world


async def feed(w, n=1):
    for _ in range(n):
        w.frame(b"\xff\xd8jpeg")
        while w._busy:
            await asyncio.sleep(0)


class TestJudgements:
    async def test_an_event_needs_two_frames_in_a_row(self):
        clock = [100.0]
        w, events, _ = watcher([{"waving": 0.9}, {"waving": 0.2}, {"waving": 0.9}, {"waving": 0.95}], clock)
        await feed(w, 3)
        assert events == []  # a single frame, then broken
        await feed(w)
        assert events == [{"what": "waving", "p": 0.95}]

    async def test_an_object_held_up_counts_from_0_6(self):
        clock = [100.0]
        w, events, _ = watcher([{"showing": 0.69}, {"showing": 0.68}], clock)  # the robot's frames, 2026-10-08
        await feed(w, 2)
        assert [e["what"] for e in events] == ["showing"]

    async def test_cooldown_between_events_of_a_kind(self):
        clock = [100.0]
        w, events, _ = watcher([{"showing": 0.9}] * 6, clock)
        await feed(w, 4)
        assert len(events) == 1
        clock[0] += 31
        await feed(w, 2)
        assert len(events) == 2

    async def test_frames_arriving_during_a_judgement_are_dropped(self):
        clock = [100.0]
        w, _, _ = watcher([{"facing": 0.9}], clock, delay=0.05)
        w.frame(b"a")
        w.frame(b"b")
        w.frame(b"c")
        await asyncio.sleep(0.1)
        assert w.dropped == 2 and len(w.decider.calls) == 1

    async def test_judgements_reach_the_world_model(self):
        clock = [100.0]
        world = WorldModel(clock=lambda: clock[0])
        await world.handle_event("people", {"count": 1, "people": [{"id": 1, "distance": 1.2, "looking": True}]})
        w, _, _ = watcher([{"waving": 0.9, "facing": 0.8}], clock, world=world)
        await feed(w)
        assert world.sees("facing") and "Your camera shows someone waving at you." in world.summary()
        clock[0] += 10  # stale after 5 s
        assert not world.sees("waving") and "camera" not in world.summary()

    async def test_no_answer_changes_nothing(self):
        clock = [100.0]

        class Down:
            calls = []

            async def ask(self, *a, **k):
                return None

        w, events, world = watcher([], clock)
        w.decider = Down()
        await feed(w, 3)
        assert events == [] and world.seen == {}


class TestManagerReactions:
    async def test_a_wave_wakes_the_mind_briefly(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        manager.process_user_input = AsyncMock(return_value={})
        await manager.handle_event("vision", {"what": "waving", "p": 0.9})
        await asyncio.sleep(0)
        prompt = manager.process_user_input.await_args.args[0]
        assert prompt.startswith("[Sensor event] Someone in front of you is waving at you.")
        await manager.handle_event("vision", {"what": "waving", "p": 0.9})  # within the cooldown
        await asyncio.sleep(0)
        assert manager.process_user_input.await_count == 1

    async def test_facing_the_camera_counts_as_looking_for_the_gate(self, mock_robot, mock_ai_provider):
        clock = [100.0]
        world = WorldModel(clock=lambda: clock[0])
        world.update_seen({"facing": 0.85}, 100.0)

        class Decider:
            async def ask(self, model, state, questions, images=None, timeout=None):
                return {"to_pepper": {"noul": 0.5}}

        manager = AIManager(
            mock_robot,
            mock_ai_provider,
            speak_responses=False,
            tablet_subtitles=False,
            world=world,
            decider=Decider(),
            router=False,
        )
        rec = {}
        assert await manager._meant_for_pepper([], "Emer, can you come to me", rec) is True
        assert rec["addressee"]["someone_looking"] is True


class TestCameraFallback:
    """#24: the detector has lost the person who is talking; the camera says where they are."""

    def finder(self, people="one", where="right", p=0.9, head=None):
        clock = [100.0]
        world = WorldModel(clock=lambda: clock[0])
        world.handle_people(0, [])

        class Decider:
            calls = 0

            async def ask(self, model, state, questions, images=None, timeout=None):
                if "people" not in questions:
                    return {"waving": {"noul": 0.0}}
                Decider.calls += 1
                return {
                    "people": {"choice": people, "probabilities": {people: p}},
                    "where": {"choice": where, "probabilities": {where: p}},
                }

        async def on_event(kind, data):
            pass

        w = FrameWatcher("ws://x", Decider(), world, on_event, clock=lambda: clock[0], wanted=lambda: True)
        if head is not None:
            w._text('{"type": "frame", "head": [%s, -5.0]}' % head)
        return w, world, clock, Decider

    async def settle(self, w):
        for _ in range(20):
            await asyncio.sleep(0)
        if w._judging:
            await asyncio.gather(*w._judging)

    async def test_a_person_on_the_right_with_the_head_turned_left(self):
        w, world, clock, _ = self.finder(where="right", head=40.0)
        w.frame(b"jpeg")
        await self.settle(w)
        assert w.found == 1 and w.head == [40.0, -5.0]
        line = world.summary()
        assert "you last saw someone just now" in line and "about 20° to your left" in line  # 40 - 19.6

    async def test_unsure_or_nobody_changes_nothing_and_it_asks_at_most_every_1_5_s(self):
        w, world, clock, decider = self.finder(people="one", p=0.55)
        w.frame(b"a")
        await self.settle(w)
        w.frame(b"b")  # too soon to ask again
        await self.settle(w)
        assert w.found == 0 and decider.calls == 1 and not world.tracker.tracks
        clock[0] += 2.0
        w.frame(b"c")
        await self.settle(w)
        assert decider.calls == 2

    async def test_a_remembered_person_keeps_their_distance(self):
        w, world, clock, _ = self.finder(where="left", head=0.0)
        world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 2.0}])
        world.handle_people(0, [])
        w.frame(b"jpeg")
        await self.settle(w)
        track = world.tracker.tracks[0]
        assert len(world.tracker.tracks) == 1 and "camera" in track.sources
        assert track.bearing(world.pose) == __import__("pytest").approx(19.6, abs=0.1)
        assert track.distance_from(world.pose) == __import__("pytest").approx(2.0)
