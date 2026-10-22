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
