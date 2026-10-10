"""
Pointing and looking at a spot in a photo (#28, #6): the geometry, the tools, and the bridge endpoint.
"""

import math
import time
from unittest.mock import AsyncMock

import pytest

from src.ai.tool_executor import ToolExecutor
from src.perception.geometry import CHEST_BELOW_EYES, offsets, photo_direction
from src.pepper.robot import Photo
from src.world import WorldModel


def photo(head=None, commanded=None, camera=0, age=1.0):
    p = Photo("image/jpeg", "", 640, 480, camera=camera, head_yaw=commanded, head_measured=head)
    p.taken_at = time.monotonic() - age
    return p


class TestGeometry:
    def test_offsets_from_the_middle(self):
        assert offsets(0.5, 0.5) == pytest.approx((0.0, 0.0))
        yaw, pitch = offsets(1.0, 1.0)
        assert yaw == pytest.approx(-28.15, abs=0.01) and pitch == pytest.approx(21.85, abs=0.01)  # half the FOV
        assert offsets(0.0, 0.0)[0] == pytest.approx(28.15, abs=0.01)  # left edge: to the left (positive)
        assert offsets(0.75, 0.5)[0] == pytest.approx(-math.degrees(math.atan(0.5 * math.tan(math.radians(28.15)))))

    def test_the_head_direction_is_added(self):
        yaw, pitch, measured = photo_direction(photo(head=[-44.3, -1.8]), 0.5, 0.5)
        assert (yaw, pitch, measured) == (-44.3, -1.8, True)
        yaw, _, measured = photo_direction(photo(commanded=10.0), 0.0, 0.5)
        assert yaw == pytest.approx(38.2, abs=0.2) and measured is False
        assert photo_direction(photo(head=[0, 0], camera=1), 0.5, 0.5)[1] == 40.0  # the mouth camera looks down


class TestTools:
    async def test_look_at_a_spot(self, mock_robot):
        mock_robot.last_photo = photo(head=[20.0, -10.0])
        mock_robot.move_head = AsyncMock(return_value={"yaw": 0.0, "pitch": 0.0})
        outcome = await ToolExecutor(mock_robot).execute("look_at", {"x": 0.9, "y": 0.5})
        yaw, pitch = mock_robot.move_head.await_args.args
        assert outcome.ok and yaw < 0 and pitch == pytest.approx(-10.0, abs=0.1)  # right of the middle
        assert "note" not in outcome.data

    async def test_look_at_needs_a_photo_and_warns_when_it_is_old(self, mock_robot):
        mock_robot.last_photo = None
        assert not (await ToolExecutor(mock_robot).execute("look_at", {"x": 0.5, "y": 0.5})).ok
        mock_robot.last_photo = photo(commanded=0.0, age=60)
        mock_robot.move_head = AsyncMock(return_value={})
        outcome = await ToolExecutor(mock_robot).execute("look_at", {"x": 0.5, "y": 0.5})
        assert "not measured" in outcome.data["note"] and "60 s old" in outcome.data["note"]

    async def test_point_at_a_spot_the_person_and_a_direction(self, mock_robot):
        mock_robot.point = AsyncMock(return_value={"arm": "LArm", "held": 3.0})
        mock_robot.last_photo = photo(head=[0.0, 0.0])
        world = WorldModel()
        world.handle_people(1, [{"id": 1, "yaw": 30.0, "distance": 1.5, "pitch": -10.0}])
        executor = ToolExecutor(mock_robot, world=world)
        await executor.execute("point_at", {"x": 0.25, "y": 0.5})
        assert mock_robot.point.await_args.args[0] > 0  # left of the middle
        await executor.execute("point_at", {"target": "person"})
        yaw, pitch = mock_robot.point.await_args.args
        assert yaw == pytest.approx(30.0, abs=0.1) and pitch == pytest.approx(-10.0 + CHEST_BELOW_EYES)
        outcome = await executor.execute("point_at", {"yaw": -45, "pitch": 10, "hold": 5})
        assert mock_robot.point.await_args.args == (-45.0, 10.0) and mock_robot.point.await_args.kwargs["hold"] == 5
        assert outcome.data["arm"] == "LArm" and "down again" in outcome.data["note"]

    async def test_not_behind_and_not_without_a_target(self, mock_robot):
        mock_robot.point = AsyncMock()
        executor = ToolExecutor(mock_robot, world=WorldModel())
        behind = await executor.execute("point_at", {"yaw": 150})
        assert not behind.ok and "turn 150" in behind.data["error"]
        assert not (await executor.execute("point_at", {"target": "person"})).ok
        assert not (await executor.execute("point_at", {})).ok
        mock_robot.point.assert_not_awaited()

    async def test_the_robot_holds_still_speech_while_pointing(self, mock_robot):
        seen = []

        async def bridge_point(yaw, pitch, hold=3.0, look=True):
            seen.append(mock_robot.holding_pose)
            return {"yaw": yaw}

        mock_robot.connection.bridge.point = bridge_point
        await mock_robot.point(20.0, 5.0)
        assert seen == [1] and mock_robot.holding_pose == 0 and mock_robot.last_head_yaw == 20.0
