"""
Tests for greeting newcomers: the world model decides what counts as an arrival,
the manager decides whether to speak.
"""

import asyncio
from unittest.mock import AsyncMock

from src.ai.manager import AIManager
from src.world import WorldModel


def person(distance=1.2, looking=True, pid=1, yaw=None, pitch=None):
    return {
        "id": pid,
        "distance": distance,
        "looking": looking,
        "zone": None,
        "present_for": 2,
        "yaw": yaw,
        "pitch": pitch,
    }


class Room:
    """A world model and a manager wired as in main.py, on one fake clock."""

    def __init__(self, robot, provider, snapshot=0, **kw):
        self.clock = [1000.0]
        self.world = WorldModel(clock=lambda: self.clock[0])
        kw.setdefault("speak_responses", False)
        kw.setdefault("tablet_subtitles", False)
        kw.setdefault("backchannel_after", 0)
        self.manager = AIManager(robot, provider, world=self.world, **kw)
        self.manager._clock = lambda: self.clock[0]
        self.manager.process_user_input = AsyncMock(return_value={})
        self.world.on_arrival(self.manager.handle_arrival)
        self.snapshot = snapshot

    async def connect(self):
        people = [person() for _ in range(self.snapshot)]
        await self.world.handle_event("sensors", {"people_count": self.snapshot, "people": people})

    async def people(self, *people):
        data = {"count": len(people), "people": list(people)}
        await self.world.handle_event("people", data)  # registered first, as in main.py
        await self.manager.handle_event("people", data)
        await asyncio.sleep(0)  # let a greeting task run

    def wait(self, seconds):
        self.clock[0] += seconds

    @property
    def greetings(self):
        return [c.args[0] for c in self.manager.process_user_input.call_args_list if c.kwargs.get("source") == "event"]


async def room(mock_robot, mock_ai_provider, **kw):
    r = Room(mock_robot, mock_ai_provider, **kw)
    await r.connect()
    return r


class TestArrivals:
    async def test_someone_walking_up_to_an_empty_room_is_greeted_once(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=1.2))
        assert len(r.greetings) == 1
        assert "[Sensor event] Someone just walked up to you, about 1.2 m away and looking at you." in r.greetings[0]
        r.wait(2)
        await r.people(person(distance=1.0))  # they step closer: same visit
        assert len(r.greetings) == 1

    async def test_the_prompt_says_how_long_the_room_was_empty(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        r.wait(10)
        await r.people()  # the person leaves
        r.wait(300)
        await r.people(person())
        assert "Nobody had been around for 5 minutes." in r.greetings[0]

    async def test_a_short_dropout_is_not_an_arrival(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        r.wait(10)
        await r.people()  # the detector loses them when they look away
        r.wait(5)
        await r.people(person())
        assert r.greetings == []
        assert "Someone left" not in r.world.summary() and "arrived" not in r.world.summary()

    async def test_someone_already_there_at_start_up_is_not_greeted(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        r.wait(5)
        await r.people(person(looking=False))
        await r.people(person(looking=True))  # a gaze change with the same count
        assert r.greetings == []

    async def test_a_second_person_joining_is_not_greeted(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        r.wait(30)
        await r.people(person(), person(pid=2))
        assert r.greetings == []
        assert "Someone arrived" in r.world.summary()


class TestWhoIsGreeted:
    async def test_far_away_or_not_looking_is_a_passer_by(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=3.5))
        await r.people(person(distance=1.5, looking=False))
        await r.people(person(distance=None))
        assert r.greetings == []

    async def test_looking_from_where_pepper_first_sees_people_is_greeted(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=2.35, looking=True))  # the open room on the robot: first seen at 2.35 m
        assert len(r.greetings) == 1

    async def test_unknown_gaze_is_greeted_only_up_close(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=2.3, looking=None))  # a passer-by, as on the robot
        assert r.greetings == []
        r.wait(2)
        await r.people(person(distance=1.6, looking=None))  # comes up close
        assert len(r.greetings) == 1
        assert "about 1.6 m away." in r.greetings[0] and "looking at you" not in r.greetings[0]

    async def test_greeted_when_they_look_over_within_the_window(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=2.8, looking=False))
        r.wait(4)
        await r.people(person(distance=1.4, looking=True))
        assert len(r.greetings) == 1 and "about 1.4 m away" in r.greetings[0]

    async def test_not_greeted_when_they_look_over_too_late(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(looking=False))
        r.wait(AIManager.GREET_WINDOW + 1)
        await r.people(person(looking=True))
        assert r.greetings == []


class TestTurningTowardsNewcomers:
    async def test_head_turns_to_a_newcomer_at_once(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.people(person(distance=2.5, looking=False, yaw=20.0, pitch=-5.0))  # not greeted, still noticed
        await asyncio.sleep(0)
        mock_robot.connection.bridge.move_head.assert_awaited_once_with(20.0, -5.0, 0.3, wait=False)
        assert r.greetings == []

    async def test_no_turn_without_a_direction_far_away_or_mid_turn(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        for p in (person(yaw=None), person(distance=3.5, yaw=10.0)):
            r.wait(30)
            await r.people(p)
            r.wait(1)
            await r.people()
        r.wait(30)
        async with r.manager._lock:
            await r.people(person(yaw=10.0))
            await asyncio.sleep(0)
        mock_robot.connection.bridge.move_head.assert_not_awaited()


class TestLookingBackAtTheRoom:
    async def test_head_recentres_when_nobody_is_left(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "RECENTRE_AFTER", 0.01)
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        await r.people()
        await asyncio.sleep(0.05)
        mock_robot.connection.bridge.move_head.assert_awaited_once_with(0.0, -18.0, 0.15, wait=False)

    async def test_face_tracking_off_for_an_empty_room_and_back_on_for_a_person(
        self, mock_robot, mock_ai_provider, monkeypatch
    ):
        monkeypatch.setattr(AIManager, "RECENTRE_AFTER", 0.01)
        r = await room(mock_robot, mock_ai_provider, snapshot=1, face_tracking=True)
        bridge = mock_robot.connection.bridge
        await r.people()
        await asyncio.sleep(0.05)
        bridge.set_awareness.assert_awaited_once_with(False, tracking_mode=None, engagement_mode=None, stimuli=None)
        r.wait(5)  # back before it counts as an arrival: still turned to and tracked
        await r.people(person(yaw=15.0, pitch=-10.0))
        await asyncio.sleep(0)
        bridge.move_head.assert_awaited_with(15.0, -10.0, 0.3, wait=False)
        bridge.set_awareness.assert_awaited_with(
            True, tracking_mode="Head", engagement_mode="SemiEngaged", stimuli=["People", "Touch"]
        )

    async def test_no_recentre_when_someone_came_back(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "RECENTRE_AFTER", 0.02)
        r = await room(mock_robot, mock_ai_provider, snapshot=1)
        await r.people()
        await r.people(person())  # back within the wait (no yaw, so no turn either)
        await asyncio.sleep(0.05)
        mock_robot.connection.bridge.move_head.assert_not_awaited()


class TestWhenToStayQuiet:
    async def test_cooldown_between_greetings(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider, greet_cooldown=90)
        for gap in (30, 30, 70):  # second visit 30 s after the first greeting, third one 100 s after
            r.wait(gap)
            await r.people(person())
            r.wait(1)
            await r.people()
        assert len(r.greetings) == 2

    async def test_not_during_a_conversation(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        r.manager._last_talk_at = r.clock[0] - 10  # someone spoke to Pepper 10 s ago
        await r.people(person())
        assert r.greetings == []

    async def test_a_user_turn_counts_as_conversation(self, mock_robot, mock_ai_provider):
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        manager._clock = lambda: 42.0
        await manager.process_user_input("hello")
        assert manager._last_talk_at == 42.0
        await manager.process_user_input("[Sensor event] x", source="event")
        manager._clock = lambda: 50.0
        assert manager._last_talk_at == 42.0

    async def test_not_while_busy_halted_or_switched_off(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        mock_robot.halted = True
        r.wait(30)
        await r.people(person())
        mock_robot.halted = False
        r.wait(1)
        await r.people()
        r.manager.greet_newcomers = False
        r.wait(30)
        await r.people(person())
        assert r.greetings == []

    async def test_a_dropped_greeting_still_uses_the_cooldown(self, mock_robot, mock_ai_provider):
        r = await room(mock_robot, mock_ai_provider)
        r.wait(30)
        await r.world.handle_event("people", {"count": 1, "people": [person()]})  # greeting scheduled
        await r.manager._lock.acquire()  # a turn starts before the greeting task runs
        await asyncio.sleep(0)
        r.manager._lock.release()
        assert r.greetings == [] and r.manager._last_greeting is not None
        r.wait(1)
        await r.people()
        r.wait(30)
        await r.people(person())
        assert r.greetings == []  # within the cooldown: no late second try
