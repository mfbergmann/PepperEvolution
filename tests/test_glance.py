"""
Tests for glancing towards a voice (src/ai/glance.py, issue #33).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.glance import EVERY, HEAD_LIMIT, glance_target


def voice(bearing, sounds=5, spread=5.0):
    return {"bearing": bearing, "sounds": sounds, "spread": spread}


class TestGlanceTarget:
    def test_a_voice_from_where_nobody_is_seen(self):
        assert glance_target(voice(82), [], 0.0, 100.0, None) == 82

    def test_needs_several_sounds_that_agree(self):
        assert glance_target(voice(82, sounds=2), [], 0.0, 100.0, None) is None  # single sounds were off by 40-56°
        assert glance_target(voice(82, spread=30.0), [], 0.0, 100.0, None) is None
        assert glance_target(None, [], 0.0, 100.0, None) is None
        assert glance_target({"bearing": None}, [], 0.0, 100.0, None) is None

    def test_not_when_someone_in_view_is_in_that_direction(self):
        assert glance_target(voice(60), [45.0], 0.0, 100.0, None) is None
        assert glance_target(voice(60), [-30.0, None], 0.0, 100.0, None) == 60  # someone else elsewhere

    def test_not_when_the_head_already_points_there(self):
        assert glance_target(voice(82), [], 70.0, 100.0, None) is None
        assert glance_target(voice(5), [], None, 100.0, None) == 5  # head angle unknown: glance

    def test_behind_pepper_the_head_turns_as_far_as_it_can(self):
        assert glance_target(voice(170), [], 0.0, 100.0, None) == HEAD_LIMIT
        assert glance_target(voice(-150), [], 0.0, 100.0, None) == -HEAD_LIMIT

    def test_not_again_within_a_few_seconds(self):
        assert glance_target(voice(82), [], 0.0, 100.0, 100.0 - EVERY + 1) is None
        assert glance_target(voice(82), [], 0.0, 100.0, 100.0 - EVERY - 1) == 82


class TestManagerGlance:
    @pytest.fixture
    def manager(self, mock_robot, mock_ai_provider):
        from src.ai.manager import AIManager
        from src.world.model import WorldModel

        m = AIManager(mock_robot, mock_ai_provider, world=WorldModel())
        m.robot.move_head = AsyncMock()
        m.robot.direct_commands_running = False
        m.robot.halted = False
        m.voice_glances = True
        m.head_angles = MagicMock(return_value=[0.0, -10.0])
        return m

    async def test_glances_and_records_it(self, manager):
        rec = {"voice": voice(82)}
        manager._glance_at_voice(rec)
        for task in list(manager._tasks):
            await task
        manager.robot.move_head.assert_awaited_once()
        assert manager.robot.move_head.await_args.args[0] == 82 and rec["glance"] == 82

    async def test_off_by_default_and_never_while_busy(self, manager):
        manager.voice_glances = False
        manager._glance_at_voice({"voice": voice(82)})
        manager.voice_glances = True
        manager.robot.halted = True
        manager._glance_at_voice({"voice": voice(82)})
        assert manager.robot.move_head.await_count == 0


# --- looking around after a second voice (LOOK_AROUND) ------------------------------------------------------------------

from src.ai.glance import SWING, look_around_yaws, others_found  # noqa: E402


class TestLookAroundYaws:
    def test_either_side_of_where_the_head_points(self):
        assert look_around_yaws(0.0, []) == [SWING, -SWING]
        assert look_around_yaws(20.0, []) == [20.0 + SWING, 20.0 - SWING]

    def test_the_voice_first_when_its_direction_is_known(self):
        assert look_around_yaws(0.0, [], voice(-80)) == [-80.0, SWING]  # -50 is close to -80: left out
        assert look_around_yaws(0.0, [], voice(-80, sounds=1)) == [SWING, -SWING]  # not agreed: not used

    def test_never_past_the_head_limit(self):
        assert look_around_yaws(100.0, []) == [100.0 - SWING]  # the limit is only 10° further: not worth a stop
        assert look_around_yaws(70.0, []) == [HEAD_LIMIT, 70.0 - SWING]
        assert look_around_yaws(None, []) == [SWING, -SWING]  # head angle unknown: from straight ahead

    def test_not_where_someone_is_already_in_view(self):
        assert look_around_yaws(0.0, [48.0]) == [-SWING]


class TestOthersFound:
    def test_counts_people_away_from_the_one_known(self):
        assert others_found([2.0, 3.0, None], [0.0]) == 0  # the person Pepper was facing
        assert others_found([60.0, 62.0, -55.0], [0.0]) == 2
        assert others_found([], [0.0]) == 0


class TestManagerLookAround:
    @pytest.fixture
    def manager(self, mock_robot, mock_ai_provider, monkeypatch):
        from src.ai import manager as manager_module
        from src.ai.manager import AIManager
        from src.world.model import WorldModel

        monkeypatch.setattr(manager_module, "HOLD", 0.0)
        m = AIManager(mock_robot, mock_ai_provider, world=WorldModel())
        m.robot.move_head = AsyncMock()
        m.robot.set_awareness = AsyncMock()
        m.robot.direct_commands_running = False
        m.robot.halted = False
        m.voice_glances = True
        m.second_voice_gate = True
        m.look_around = True
        m.face_tracking = True
        m.head_angles = MagicMock(return_value=[0.0, -10.0])
        m.second_voice.heard_at = m._clock()  # a second voice just now
        m.faces_seen = MagicMock(return_value=[1.0, 58.0])
        return m

    async def _done(self, m):
        for task in list(m._tasks):
            await task

    async def test_sweeps_records_and_comes_back(self, manager):
        rec = {}
        assert manager._look_around_for_people(rec) is True
        await self._done(manager)
        yaws = [c.args[0] for c in manager.robot.move_head.await_args_list]
        assert yaws == [SWING, -SWING, 0.0] and rec["look_around"] == [SWING, -SWING]
        assert manager.robot.set_awareness.await_args_list[0].args == (False,)  # tracking paused
        assert manager.robot.set_awareness.await_args_list[-1].args == (True,)  # and resumed
        last = manager.world.timeline.last("looked_around")
        assert last is not None and last.data["found"] == 1  # the face at 58°, not the one Pepper faced
        assert not manager._looking_around

    async def test_once_per_episode(self, manager):
        assert manager._look_around_for_people({})
        await self._done(manager)
        assert manager._look_around_for_people({}) is False

    async def test_only_after_a_second_voice_and_when_off_by_default(self, manager):
        manager.second_voice.heard_at = None
        assert manager._look_around_for_people({}) is False
        manager.second_voice.heard_at = manager._clock()
        manager.look_around = False
        assert manager._look_around_for_people({}) is False
        assert manager.robot.move_head.await_count == 0

    async def test_not_while_busy_or_halted(self, manager):
        manager.robot.halted = True
        assert manager._look_around_for_people({}) is False
        manager.robot.halted = False
        manager.robot.direct_commands_running = True
        assert manager._look_around_for_people({}) is False

    async def test_a_glance_waits_for_the_sweep(self, manager):
        manager._looking_around = True
        manager._glance_at_voice({"voice": voice(82)})
        assert manager.robot.move_head.await_count == 0
