"""
Working memory (#27): Pepper's pose, frames, and people remembered after they leave the camera's view.

The fixture numbers come from what is already published in issue #27 and docs/HANDOFF.md about the robot session
of 2026-10-08 (no session record is copied here): a person at 67.5°, Pepper turns 70 and sees them at -0.9°; then
turns +90 (they drop out of view), +180, -160 and -60, and sees them again at -50.4°.
"""

import math

import pytest

from src.world import WorldModel
from src.world.frames import MEASURED, Pose, pose_from, wrap, wrap_deg
from src.world.observations import MOTION, POSE, SOUND
from src.world.tracks import REMEMBER_FOR


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def world_at():
    clock = Clock()
    return WorldModel(clock=clock), clock


def see(world, *people):
    world.handle_people(len(people), [dict(p) for p in people])


def person(yaw, distance=1.6, looking=True, **extra):
    return {"id": 1, "yaw": yaw, "distance": distance, "looking": looking, **extra}


def turn(world, clock, angle, seconds=3.0, completed=True, **extra):
    world.note(MOTION, "turn_started", angle=angle)
    clock.t += seconds
    world.note(MOTION, "turn", angle=angle, completed=completed, **extra)


class TestFrames:
    def test_wrap(self):
        assert wrap_deg(270) == pytest.approx(-90)
        assert wrap_deg(-180) == pytest.approx(180)
        assert wrap(3 * math.pi) == pytest.approx(math.pi)

    def test_a_point_keeps_its_place_as_pepper_turns(self):
        pose = Pose(theta=math.radians(30))
        x, y = pose.to_odom(40, 2.0)
        assert pose.bearing_to(x, y) == pytest.approx(40)
        assert pose.turned(70, at=1).bearing_to(x, y) == pytest.approx(-30)
        assert pose.distance_to(x, y) == pytest.approx(2.0)

    def test_driving_towards_someone(self):
        pose = Pose(theta=math.radians(90))
        x, y = pose.to_odom(0, 2.0)
        closer = pose.driven(1.0, at=1)
        assert closer.bearing_to(x, y) == pytest.approx(0, abs=1e-9)
        assert closer.distance_to(x, y) == pytest.approx(1.0)

    def test_bridge_pose(self):
        pose = pose_from([0.5, -0.2, 7.0], at=3)
        assert pose.source == MEASURED and pose.theta == pytest.approx(wrap(7.0))
        assert pose_from(None, at=1) is None and pose_from([1, "x", 0], at=1) is None


class TestTurningAway:
    def test_the_2026_10_08_sequence(self):
        world, clock = world_at()
        see(world, person(67.5, 1.63))
        turn(world, clock, 70)  # "turn your body towards me"
        see(world, person(-0.9, 1.69))
        assert len(world.tracker.tracks) == 1  # the same person, seen from the new heading
        turn(world, clock, 90)  # "turn ninety degrees": they are behind Pepper's right shoulder now
        see(world)
        clock.t += 8
        line = world.summary()
        assert line.startswith("Around you: nobody in view; you last saw someone")
        assert "about 90° to your right of where your body points now (turn -90 to face where they were)" in line
        turn(world, clock, 180)  # routed "turn back the way you [came]"
        turn(world, clock, -160)
        turn(world, clock, -60)
        bearing = world.tracker.remembered(clock.t)[0].bearing(world.pose)
        assert bearing == pytest.approx(-50.9, abs=0.2)  # where they were seen again: -50.4
        see(world, person(-50.4, 1.57))
        assert len(world.tracker.tracks) == 1  # matched as the same person
        assert any(e.kind == "track" and "back_after" in e.data for e in world.timeline.recent())

    def test_only_for_two_minutes(self):
        world, clock = world_at()
        see(world, person(10))
        turn(world, clock, 90)
        see(world)
        clock.t += REMEMBER_FOR + 1
        assert "you last saw someone" in world.summary()  # the old wording, without a direction
        assert "turn " not in world.summary()

    def test_a_report_without_a_direction_keeps_the_position(self):
        # on the robot a people event came without a position (yaw None); it must not erase where they were
        world, clock = world_at()
        see(world, person(-76.6, 1.41))
        see(world, {"id": 1, "distance": 1.41, "looking": True})
        track = world.tracker.present()[0]
        assert track.bearing(world.pose) == pytest.approx(-76.6, abs=0.1)

    def test_directions_during_a_turn_are_not_trusted(self):
        world, clock = world_at()
        see(world, person(40))
        world.note(MOTION, "turn_started", angle=40)
        see(world, person(10))  # mid-turn: the dead-reckoned pose has not caught up yet
        clock.t += 2
        world.note(MOTION, "turn", angle=40, completed=True)
        assert world.tracker.present()[0].bearing(world.pose) == pytest.approx(0, abs=0.1)

    def test_a_refused_move_changes_nothing(self):
        world, clock = world_at()
        see(world, person(30))
        world.note(MOTION, "drive_started", distance=1.0)
        world.note(MOTION, "drive", distance=1.0, completed=False, refused=True, error="obstacle")
        assert world.pose == Pose()
        assert world.timeline.last("drive").data["description"] == "refused"

    def test_a_move_stopped_early_makes_directions_unknown(self):
        world, clock = world_at()
        see(world, person(30))
        turn(world, clock, 90, completed=False)
        see(world)
        assert "turn" not in world.summary()  # where Pepper faces is not known any more
        assert world.pose.uncertain


class TestMeasuredPose:
    def test_the_first_measured_pose_keeps_remembered_directions(self):
        world, clock = world_at()
        see(world, person(-30, 2.0))
        turn(world, clock, 45)
        see(world)
        before = world.tracker.remembered(clock.t)[0].bearing(world.pose)
        world.note(POSE, "pose", pose=[3.0, -1.0, 1.2])  # odometry has its own origin
        assert world.pose.source == MEASURED
        assert world.tracker.remembered(clock.t)[0].bearing(world.pose) == pytest.approx(before)

    def test_people_reports_with_the_pose_of_their_moment(self):
        world, clock = world_at()
        world.note(POSE, "pose", pose=[0, 0, 0])
        world.handle_people(1, [person(20)], pose=[0, 0, math.radians(30)])
        assert world.pose.theta == pytest.approx(math.radians(30))
        assert world.tracker.present()[0].bearing(world.pose) == pytest.approx(20)

    def test_moved_unexpectedly(self):
        world, clock = world_at()
        world.note(POSE, "pose", pose=[0, 0, 0])
        see(world, person(20))
        see(world)
        world.note(POSE, "pose", pose=[0.0, 0.0, math.radians(40)])  # nobody asked Pepper to turn
        assert world.timeline.last("moved_unexpectedly") is not None
        assert "turn" not in world.summary()

    def test_a_commanded_turn_is_not_moved_unexpectedly(self):
        world, clock = world_at()
        world.note(POSE, "pose", pose=[0, 0, 0])
        turn(world, clock, 90)
        world.note(POSE, "pose", pose=[0.0, 0.0, math.radians(91)])
        assert world.timeline.last("moved_unexpectedly") is None

    def test_the_bridge_pose_after_a_move(self):
        world, clock = world_at()
        world.note(POSE, "pose", pose=[0, 0, 0])
        turn(world, clock, 90, pose=[0.01, 0.0, math.radians(88)])  # measured: a little short
        assert world.pose.theta == pytest.approx(math.radians(88))


class TestRobotReportsMoves:
    async def test_turns_and_drives_reach_the_world(self, mock_robot):
        world, clock = world_at()
        mock_robot.motion_observer = lambda kind, data: world.note(MOTION, kind, **data)
        await mock_robot.turn(90)
        await mock_robot.move_forward(0.5)
        assert world.pose.theta == pytest.approx(math.pi / 2)
        assert world.pose.y == pytest.approx(0.5)
        assert [e.kind for e in world.timeline.recent()] == ["turn", "drive"]
        recall = world.recall()["recently"]
        assert recall[0].endswith("you turned +90 degrees") and recall[1].endswith("you drove +0.50 m")


class TestReplayScript:
    """scripts/replay_world.py on a made-up session folder (real session records never go into tests)."""

    def test_replays_people_and_logged_turns(self, tmp_path):
        import importlib.util
        import json
        from pathlib import Path

        script = Path(__file__).resolve().parents[1] / "scripts" / "replay_world.py"
        spec = importlib.util.spec_from_file_location("replay_world", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def people(at, *yaws):
            return {
                "at": f"2026-01-01T10:00:{at:06.3f}",
                "kind": "people",
                "count": len(yaws),
                "people": [{"id": 1, "yaw": y, "distance": 1.5} for y in yaws],
            }

        events = [
            people(1, -2.0),
            people(
                10,
            ),
            people(30, -50.0),
        ]
        (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        log = [
            '2026-01-01 10:00:05.000 | INFO     | src.ai.tool_executor:execute:103 - Tool call: turn({"angle": 90})',
            '2026-01-01 10:00:08.000 | INFO     | src.ai.tool_executor:execute:123 - Tool turn -> {"success": true}',
            "2026-01-01 10:00:20.000 | INFO     | src.ai.manager:_start_routed:347 - Routed at once: turning around",
        ]
        (tmp_path / "host.log").write_text("\n".join(log) + "\n")
        turns = [
            {
                "at": "2026-01-01T10:00:15.000",
                "heard": "2026-01-01T10:00:12.000",
                "source": "voice",
                "text": "Turn towards me",
                "around": "Around you: nobody in view.",
            }
        ]
        (tmp_path / "turns.jsonl").write_text(json.dumps(turns[0]) + "\n")
        text = module.replay(str(tmp_path))
        assert "about 90° to your right of where your body points now (turn -90 to face where they were)" in text
        assert "back in view: memory said +88.0°, the detector saw -50.0°" in text  # they moved: shown as such


class TestVoiceDirection:
    """Sound direction (#12, #24): where a voice came from, kept true while Pepper turns."""

    def sounds(self, world, clock, azimuth, n=4, confidence=0.6):
        for i in range(n):
            world.note(SOUND, "sound", azimuth=azimuth + (i % 2) * 4 - 2, confidence=confidence)
            clock.t += 0.2

    def test_mean_direction_of_the_utterance(self):
        world, clock = world_at()
        start = clock.t
        self.sounds(world, clock, 60.0)
        world.note(SOUND, "sound", azimuth=-120.0, confidence=0.1)  # a door, too unsure: ignored
        voice = world.voice_direction(start, clock.t)
        assert voice["bearing"] == pytest.approx(60.0, abs=0.5) and voice["sounds"] == 4
        assert world.voice_direction(clock.t + 1, clock.t + 2) is None

    def test_a_voice_out_of_view_becomes_someone_heard(self):
        world, clock = world_at()
        see(world)  # nobody in view
        start = clock.t
        self.sounds(world, clock, -70.0)
        voice = world.voice_direction(start, clock.t)
        track = world.attach_voice(voice["heading"])
        assert track.heard_only
        turn(world, clock, -30)
        line = world.summary()
        assert "you heard someone" in line and "about 40° to your right" in line and "turn -40" in line

    def test_a_voice_from_someone_in_view_marks_them(self):
        world, clock = world_at()
        see(world, person(-35.0, 1.4))
        start = clock.t
        self.sounds(world, clock, -40.0)
        track = world.attach_voice(world.voice_direction(start, clock.t)["heading"])
        assert not track.heard_only and track.spoke_at is not None and len(world.tracker.tracks) == 1

    async def test_the_mind_hears_where_the_voice_came_from(self, mock_robot, mock_ai_provider):
        from src.ai.manager import AIManager
        from src.ai.models import AIResponse

        world, clock = world_at()
        seen = {}

        async def chat(messages, tools=None, system=None, on_text=None):
            seen["state"] = system[1]["text"]
            return AIResponse(text="Coming.", stop_reason="end_turn")

        mock_ai_provider.chat.side_effect = chat
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False, world=world)
        self.sounds(world, clock, 50.0, n=6)
        clock.t += 1.0  # the recogniser's end-of-speech silence
        result = await manager.process_user_input(
            "Come to me", source="voice", heard_at=clock.t, spoke_for=1.2, open_mic=False
        )
        assert "The voice you are answering came from about 50° to your left" in seen["state"]
        assert "(turn 50 to face it)" in seen["state"]
        assert manager._voice_line == ""  # only for that turn
        assert result["stop_reason"] == "end_turn"
