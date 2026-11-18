"""Tests for expect-then-check (issue #30): the pure planners, the watch, and the world model end to end."""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

from src.world import WorldModel
from src.world.expectations import (
    CLOSER_TOLERANCE,
    FACE_DUE,
    MIN_DRIVE,
    Target,
    ExpectationWatch,
    check,
    expire,
    plan_closer,
    plan_face,
)
from src.world.observations import EXPECT, MIND, MOTION, POSE
from src.world.views import describe_event


def started_at(clock):
    return WorldModel(clock=lambda: clock[0], expectations=True), clock


def test_plan_face_picks_the_nearest_target_within_aimed():
    targets = [Target(75.0, 2.0, 1), Target(110.0, 3.0, 2)]
    exp = plan_face(90.0, targets, at=100.0, next_id=1)
    assert exp is not None
    assert exp.kind == "face"
    assert exp.data["target_bearing"] == 75.0
    assert exp.data["expected_bearing"] == pytest.approx(-15.0)
    assert exp.data["track_id"] == 1
    assert exp.due_at == pytest.approx(100.0 + FACE_DUE)


def test_plan_face_none_when_no_target_is_near_the_turn():
    targets = [Target(160.0, 2.0)]
    assert plan_face(90.0, targets, at=100.0, next_id=1) is None
    assert plan_face(90.0, [], at=100.0, next_id=1) is None


def test_plan_face_wraps_around_180():
    targets = [Target(-175.0, 2.0)]
    exp = plan_face(170.0, targets, at=100.0, next_id=1)
    assert exp is not None
    # abs(wrap_deg(-175 - 170)) = abs(wrap_deg(-345)) = abs(15) <= AIMED_WITHIN
    assert exp.data["expected_bearing"] == pytest.approx(15.0)


def test_plan_closer_none_under_min_drive():
    assert plan_closer(MIN_DRIVE / 2, [Target(0.0, 1.8)], at=100.0, next_id=1) is None


def test_plan_closer_ignores_side_targets_and_missing_distance():
    targets = [Target(50.0, 1.0), Target(0.0, None), Target(-5.0, 3.0)]
    exp = plan_closer(0.5, targets, at=100.0, next_id=1)
    assert exp is not None
    assert exp.data["before"] == 3.0  # only the on-bear with a known distance
    assert exp.data["expected"] == pytest.approx(2.5)
    assert exp.data["tolerance"] == pytest.approx(max(CLOSER_TOLERANCE, 0.35 * 0.5))


def test_plan_closer_picks_the_nearest():
    targets = [Target(5.0, 3.0), Target(-3.0, 1.8), Target(10.0, 2.5)]
    exp = plan_closer(0.5, targets, at=100.0, next_id=1)
    assert exp.data["before"] == 1.8


def test_face_check_met_when_someone_in_front():
    exp = plan_face(90.0, [Target(90.0, 1.5)], at=100.0, next_id=1)
    verdict = check(exp, [{"yaw": 8.0, "distance": 1.4}], at=101.0)
    assert verdict is not None and verdict.met


def test_face_check_keeps_waiting_when_nobody_in_front():
    exp = plan_face(90.0, [Target(90.0, 1.5)], at=100.0, next_id=1)
    assert check(exp, [{"yaw": 40.0}], at=101.0) is None


def test_face_expire_surprise_with_reason():
    exp = plan_face(90.0, [Target(90.0, 1.5)], at=100.0, next_id=1)
    assert expire(exp, at=100.0 + FACE_DUE - 0.1) is None
    verdict = expire(exp, at=100.0 + FACE_DUE)
    assert verdict is not None and not verdict.met
    assert verdict.reason == "you turned +90 degrees to face someone, but nobody was in front of you"


def test_closer_check_met_and_surprise():
    exp = plan_closer(0.5, [Target(0.0, 1.8)], at=100.0, next_id=1)
    assert check(exp, [{"yaw": 0.0, "distance": 1.3}], at=101.0).met
    surprise = check(exp, [{"yaw": 0.0, "distance": 1.8}], at=101.0)
    assert surprise is not None and not surprise.met
    assert "they were still 1.8 m away" in surprise.reason


def test_closer_check_none_then_expire_surprise():
    exp = plan_closer(0.5, [Target(0.0, 1.8)], at=100.0, next_id=1)
    assert check(exp, [{"yaw": 40.0, "distance": 1.8}], at=101.0) is None
    verdict = expire(exp, at=100.0 + 5.0)
    assert verdict is not None and not verdict.met
    assert verdict.reason == (
        "you drove 0.50 m towards someone 1.8 m away, but could not tell how far they were afterwards"
    )


def test_watch_stats_met_and_surprised_per_kind():
    watch = ExpectationWatch()
    face = plan_face(90.0, [Target(90.0, 1.5)], 100.0, watch.next_id())
    closer = plan_closer(0.5, [Target(0.0, 1.8)], 100.0, watch.next_id())
    watch.add(face)
    watch.add(closer)
    assert watch.stats["face"]["made"] == 1 and watch.stats["closer"]["made"] == 1
    watch.on_people([{"yaw": 3.0}], at=101.0)
    watch.tick(100.0 + 5.0)
    assert watch.stats["face"]["met"] == 1
    assert watch.stats["closer"]["surprised"] == 1  # expired with no closer report


def test_watch_cancel_counts_cancelled():
    watch = ExpectationWatch()
    watch.add(plan_face(90.0, [Target(90.0, 1.5)], 100.0, watch.next_id()))
    watch.cancel("another move started")
    assert watch.stats["face"]["cancelled"] == 1
    assert watch.open == []


def test_watch_latest_surprise_is_pure_and_sees_overdue_open():
    watch = ExpectationWatch()
    exp = plan_face(90.0, [Target(90.0, 1.5)], 100.0, 1)
    watch.add(exp)
    assert watch.latest_surprise(99.0) is None  # not yet due
    first = watch.latest_surprise(200.0)
    assert first is not None and not first.met
    # pure: a second read gives the same, and nothing was removed or recorded
    assert watch.latest_surprise(200.0) == first
    assert not watch.history
    assert len(watch.open) == 1


def test_expectation_model_met_end_to_end():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.note(MOTION, "turn_started", angle=90)
    world.note(MOTION, "turn", angle=90, completed=True)
    world.handle_people(1, [{"id": 1, "yaw": 3.0, "distance": 1.5}])
    assert world.watch.stats["face"]["met"] == 1
    assert "A surprise" not in world.summary()


def test_expectation_model_surprise_end_to_end():
    clock = [1000.0]
    world = started_at(clock)[0]
    received = []
    world.add_sink(lambda obs: received.append(obs))
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.note(MOTION, "turn_started", angle=90)
    world.note(MOTION, "turn", angle=90, completed=True)
    world.handle_people(1, [{"id": 1, "yaw": 60.0, "distance": 1.5}])
    clock[0] += 6
    world.note(MIND, "note")  # any non-move observation advances the clock and lets it expire
    assert world.watch.stats["face"]["surprised"] == 1
    text = world.summary()
    assert "A surprise" in text
    assert "nobody was in front of you" in text
    assert any(o.source == EXPECT and o.kind == "surprise" for o in received)
    event = world.timeline.last("surprise")
    assert event is not None
    assert "nobody was in front of you" in describe_event(event, clock[0])


def test_remembered_track_is_an_aimed_target():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.handle_people(0, [])
    world.note(MOTION, "turn_started", angle=90)
    world.note(MOTION, "turn", angle=90, completed=True)
    assert world.watch.stats["face"]["made"] == 1


def test_expectations_are_off_by_default():
    def run(expectations):
        clock = [1000.0]
        world = WorldModel(clock=lambda: clock[0], expectations=expectations)
        world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
        world.note(MOTION, "turn_started", angle=90)
        world.note(MOTION, "turn", angle=90, completed=True)
        return world.summary()

    plain = run(False)
    assert run(True) == plain
    assert "A surprise" not in plain


def test_no_expectation_for_refused_or_uncompleted_move():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.note(MOTION, "turn_started", angle=90)
    world.note(MOTION, "turn", angle=90, completed=False, refused=True, error="obstacle")
    world.note(MOTION, "drive_started", distance=0.5)
    world.note(MOTION, "drive", distance=0.5, completed=False)  # stopped early
    assert world.watch.stats["face"]["made"] == 0
    assert world.watch.stats["closer"]["made"] == 0


def test_new_move_cancels_an_open_expectation():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.note(MOTION, "turn_started", angle=90)
    world.note(MOTION, "turn", angle=90, completed=True)
    world.note(MOTION, "drive_started", distance=0.5)  # a new move before the face settled
    assert world.watch.stats["face"]["made"] == 1
    assert world.watch.stats["face"]["cancelled"] == 1
    assert world.watch.open == []


def test_settled_during_the_turn_is_met_not_surprised():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 90.0, "distance": 1.5}])
    world.note(MOTION, "turn_started", angle=90)
    world.handle_people(1, [{"id": 1, "yaw": 3.0, "distance": 1.5}])  # settled while the turn ran
    world.note(MOTION, "turn", angle=90, completed=True)  # no report follows: met at once
    assert world.watch.stats["face"]["met"] == 1
    assert world.watch.stats["face"]["surprised"] == 0
    clock[0] += 6
    world.note(MIND, "note")
    assert world.watch.stats["face"]["surprised"] == 0


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


def test_replay_expectations_reports_counts(tmp_path):
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "replay_expectations.py"
    )
    spec = importlib.util.spec_from_file_location("replay_expectations", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_expectations"] = module
    spec.loader.exec_module(module)

    t = datetime(2026, 10, 8, 12, 0, 0)
    events = [
        {"at": _iso(t), "kind": "people", "count": 1, "people": [{"id": 1, "yaw": 90.0, "distance": 1.5}]},
        {
            "at": _iso(t + timedelta(seconds=1)),
            "kind": "observation",
            "source": "motion",
            "obs": "turn_started",
            "angle": 90,
        },
        {
            "at": _iso(t + timedelta(seconds=5)),
            "kind": "observation",
            "source": "motion",
            "obs": "turn",
            "angle": 90,
            "completed": True,
        },
        {"at": _iso(t + timedelta(seconds=6)), "kind": "people", "count": 1, "people": [{"id": 1, "yaw": 3.0}]},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (tmp_path / "turns.jsonl").write_text("")

    text, stats = module.replay(str(tmp_path))
    assert stats["face"]["made"] == 1
    assert stats["face"]["met"] == 1
    assert stats["face"]["surprised"] == 0
    assert "made 1" in text and "met 1" in text


def test_a_report_without_a_yaw_does_not_break_the_closer_check():
    exp = plan_closer(0.5, [Target(0.0, 1.8)], at=100.0, next_id=1)
    assert check(exp, [{"yaw": None, "distance": 1.3}, {"distance": 1.2}], at=101.0) is None


def test_a_report_from_before_the_move_is_turned_with_pepper():
    # the bridge reports people only on change: the last report predates the turn, so its bearings are turned by
    # the turn (someone at +90 before a +90 turn is in front after it; the one at 0 is now at -90)
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(2, [{"id": 1, "yaw": 0.0, "distance": 1.5}, {"id": 2, "yaw": 90.0, "distance": 2.0}])
    clock[0] += 1
    world.note(MOTION, "turn_started", angle=90)
    clock[0] += 3
    world.note(MOTION, "turn", angle=90, completed=True)
    assert world.watch.stats["face"]["met"] == 1
    assert world.watch.history[-1].evidence["yaw"] == pytest.approx(0.0)


def test_a_small_turn_with_no_new_report_is_met():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": -6.0, "distance": 1.5}])
    clock[0] += 1
    world.note(MOTION, "turn_started", angle=-6)
    clock[0] += 2
    world.note(MOTION, "turn", angle=-6, completed=True)
    clock[0] += 6
    world.note(MIND, "note")
    assert world.watch.stats["face"] == {"made": 1, "met": 1, "surprised": 0, "cancelled": 0}


def test_distances_from_before_a_drive_are_not_trusted():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.8}])
    clock[0] += 1
    world.note(MOTION, "drive_started", distance=0.5)
    clock[0] += 2
    world.note(MOTION, "drive", distance=0.5, completed=True)
    assert world.watch.stats["closer"]["met"] == 0 and world.watch.stats["closer"]["surprised"] == 0
    clock[0] += 6
    world.note(MIND, "note")
    assert "could not tell" in world.watch.history[-1].reason


def test_a_drive_is_judged_by_how_far_it_measurably_went():
    clock = [1000.0]
    world = started_at(clock)[0]
    world.note(POSE, "pose", pose=[0.0, 0.0, 0.0])
    world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.8}])
    clock[0] += 1
    world.note(MOTION, "drive_started", distance=1.0)
    clock[0] += 2
    world.note(MOTION, "drive", distance=1.0, completed=True, pose=[0.3, 0.0, 0.0])
    exp = world.watch.open[0]
    assert exp.data["driven"] == pytest.approx(0.3) and exp.data["expected"] == pytest.approx(1.5)
    world.handle_people(1, [{"id": 1, "yaw": 0.0, "distance": 1.5}])
    assert world.watch.stats["closer"]["met"] == 1
