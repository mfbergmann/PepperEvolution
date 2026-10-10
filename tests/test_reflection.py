"""
Initiative (src/ai/reflection.py): the two rules, the guards, and the hand-over to the mind.
"""

from unittest.mock import AsyncMock, MagicMock

from src.ai.reflection import Reflection, consider
from src.world import WorldModel
from src.world.observations import HEARD, MIND, SAID


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def room(people=1, distance=1.5, looking=True):
    clock = Clock()
    world = WorldModel(clock=clock)
    world.handle_people(
        people, [{"id": i, "yaw": 0.0, "distance": distance, "looking": looking} for i in range(people)]
    )
    return world, clock


def said(world, text):
    world.note(SAID, "said", text=text)


def heard(world, text, **extra):
    world.note(HEARD, "heard", text=text, via="voice", addressed=True, **extra)


class TestLingering:
    def test_someone_looking_quietly_for_half_a_minute(self):
        world, clock = room()
        clock.t += 29
        assert consider(world, clock.t) is None
        clock.t += 2
        chosen = consider(world, clock.t)
        assert chosen.rule == "lingering" and "1.5 m away" in chosen.prompt and "stay quiet" in chosen.prompt

    def test_not_after_a_greeting_talk_or_far_away_or_not_looking(self):
        world, clock = room()
        world.note(MIND, "greeting")
        clock.t += 60
        assert consider(world, clock.t) is None  # greeted a minute ago
        clock.t += 40
        heard(world, "hi")
        clock.t += 30
        assert consider(world, clock.t) is None  # they spoke
        far, c2 = room(distance=3.5)
        c2.t += 40
        assert consider(far, c2.t) is None
        away, c3 = room(looking=False)
        c3.t += 40
        assert consider(away, c3.t) is None

    def test_once_per_visit_and_not_in_a_crowd(self):
        world, clock = room()
        clock.t += 31
        done = set()
        chosen = consider(world, clock.t, done)
        done.add(chosen.key)
        world.handle_people(1, [{"id": 9, "yaw": 5.0, "distance": 1.4, "looking": True}])  # the detector's new id
        clock.t += 300
        assert consider(world, clock.t, done) is None
        crowd, c2 = room(people=3)
        c2.t += 40
        assert consider(crowd, c2.t) is None


class TestUnanswered:
    def test_a_question_left_hanging(self):
        world, clock = room()
        heard(world, "what's that")
        said(world, "Is it a good brew?")
        clock.t += 19
        assert consider(world, clock.t) is None
        clock.t += 2
        chosen = consider(world, clock.t)
        assert chosen.rule == "unanswered" and '"Is it a good brew?"' in chosen.prompt
        assert consider(world, clock.t, {chosen.key}) is None  # once per question

    def test_any_answer_counts_even_side_talk(self):
        world, clock = room()
        said(world, "Shall I turn?")
        clock.t += 5
        world.note(HEARD, "heard", text="no idea", via="voice", addressed=False)
        clock.t += 20
        assert consider(world, clock.t) is None

    def test_not_after_be_quiet_or_too_soon_after_another(self):
        world, clock = room()
        heard(world, "be quiet", intent="quiet")
        said(world, "Okay?")
        clock.t += 25
        assert consider(world, clock.t) is None
        world2, c2 = room()
        world2.timeline.add("initiative", c2.t, rule="lingering")
        said(world2, "Anything else?")
        c2.t += 25
        assert consider(world2, c2.t) is None  # one every 3 minutes


class TestLoop:
    def manager(self, busy=False):
        manager = MagicMock()
        manager.busy = busy
        manager.robot.direct_commands_running = 0
        manager.robot.halted = False
        manager.initiate = AsyncMock()
        return manager

    async def test_hands_it_to_the_mind_once(self):
        world, clock = room()
        clock.t += 31
        manager = self.manager()
        loop = Reflection(world, manager)
        chosen = await loop.step()
        assert chosen.rule == "lingering" and manager.initiate.await_args.args[1] == "lingering"
        assert world.timeline.last("initiative") is not None
        clock.t += 400
        assert await loop.step() is None and loop.fired == 1

    async def test_waits_while_pepper_is_busy(self):
        world, clock = room()
        clock.t += 31
        manager = self.manager(busy=True)
        assert await Reflection(world, manager).step() is None
        manager.initiate.assert_not_awaited()

    async def test_the_mind_gets_an_event_turn(self, mock_robot, mock_ai_provider):
        from src.ai.manager import AIManager

        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        manager.process_user_input = AsyncMock(return_value={})
        await manager.initiate("[Initiative] Someone is waiting.", "lingering")
        assert manager.process_user_input.await_args.kwargs["source"] == "event"
