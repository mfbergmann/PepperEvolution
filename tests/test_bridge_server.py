"""
Tests for the robot-side bridge server (robot_bridge/pepper_bridge.py).

The bridge must stay Python 2.7 compatible (it runs on the robot). These tests
import it under the host Python with a fake ``qi`` module, exercise the Robot
facade against fake NAOqi services, and check for Python-3-only syntax.
"""

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pytest

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "robot_bridge" / "pepper_bridge.py"


# ---------------------------------------------------------------------------
# Fake NAOqi
# ---------------------------------------------------------------------------


class FakeService:
    """Records calls; returns canned values for known NAOqi methods."""

    def __init__(self, name, memory):
        self.name = name
        self.memory = memory
        self.calls = []
        self.subscribers = []
        self.language = "English"
        self.awake = True
        self.life_state = "solitary"
        self.head = [0.0, 0.0]  # radians; getAngles moves it towards head_target, like a real head
        self.head_target = [0.0, 0.0]
        self.head_step = 0.1  # radians per reading

    def __getattr__(self, method):
        def call(*args, **kwargs):
            self.calls.append((method, args))
            if kwargs.get("_async"):
                looping = method == "run" and "LED/" in str(args[0])  # like the robot's CircleEyes
                self.last_future = FakeFuture(lambda: self._respond(method, args), finished=not looping)
                return self.last_future
            return self._respond(method, args)

        return call

    def _respond(self, method, args):
        if method == "setAngles" and list(args[0]) == ["HeadYaw", "HeadPitch"]:
            self.head_target = list(args[1])
            return None
        if method == "getAngles" and list(args[0]) == ["HeadYaw", "HeadPitch"]:
            for i in (0, 1):
                gap = self.head_target[i] - self.head[i]
                self.head[i] += max(-self.head_step, min(self.head_step, gap))
            return list(self.head)
        if method == "getData":
            return self.memory[args[0]]
        if method == "getListData":
            return [self.memory.get(k) for k in args[0]]
        if method == "subscriber":
            sub = FakeSubscriber(args[0])
            self.subscribers.append(sub)
            return sub
        if method == "getLanguage":
            return self.language
        if method == "setLanguage":
            self.language = args[0]
            return None
        if method == "getAvailableLanguages":
            return ["English", "French"]
        if method == "robotIsWakeUp":
            return self.awake
        if method == "wakeUp":
            self.awake = True
            return None
        if method == "rest":
            self.awake = False
            return None
        if method == "getState":
            return self.life_state
        if method == "setState":
            self.life_state = args[0]
            return None
        if method == "getBatteryCharge":
            return 77
        if method == "getPosture":
            return "Stand"
        if method == "getPostureFamily":
            return "Standing"
        if method == "goToPosture":
            return True
        if method == "robotName":
            return "Pepper"
        if method == "systemVersion":
            return "2.5.10.7"
        if method == "getVolume":
            return 0.6
        if method == "isEnabled":
            return False
        if method == "getInstalledBehaviors":
            return ["animations/Stand/Gestures/Hey_1", "dialog/foo", "animations/Stand/Gestures/BowShort_1"]
        if method == "subscribeCamera":
            return "handle_1"
        if method == "getSubscribersInfo":
            return None
        if method == "getImageRemote":
            return [2, 1, 3, 11, 0, 0, bytearray([255, 0, 0, 0, 255, 0]), 0]
        return None


class FakeFuture(object):
    """Enough of qi.Future for the bridge: wait, isFinished, hasError, error, cancel."""

    def __init__(self, fn=None, finished=True, error=None):
        self.value = fn() if (fn is not None and finished and error is None) else None
        self.finished = finished
        self._error = error
        self.cancelled = False

    def wait(self, timeout_ms=None):
        return None

    def isFinished(self):
        return self.finished

    def hasError(self):
        return self._error is not None

    def error(self):
        return self._error or ""

    def cancel(self):
        self.cancelled = True


class FakeSubscriber:
    """What ALMemory.subscriber(key) returns."""

    def __init__(self, key):
        self.key = key
        self.callbacks = []
        self.signal = self

    def connect(self, callback):
        self.callbacks.append(callback)
        return len(self.callbacks)

    def fire(self, value):
        for cb in self.callbacks:
            cb(value)


class FakeSession:
    def __init__(self):
        self.registered = {}
        self.memory = {
            "Device/SubDeviceList/Head/Touch/Front/Sensor/Value": 1.0,
            "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value": 0.0,
            "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value": 0.0,
            "Device/SubDeviceList/LHand/Touch/Back/Sensor/Value": 0.0,
            "Device/SubDeviceList/RHand/Touch/Back/Sensor/Value": 0.0,
            "Device/SubDeviceList/Platform/FrontLeft/Bumper/Sensor/Value": 0.0,
            "Device/SubDeviceList/Platform/FrontRight/Bumper/Sensor/Value": 0.0,
            "Device/SubDeviceList/Platform/Back/Bumper/Sensor/Value": 0.0,
            "Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value": 0.3,
            "Device/SubDeviceList/Platform/Back/Sonar/Sensor/Value": 1.8,
            "Device/SubDeviceList/Battery/Charge/Sensor/Value": 0.77,
            "Device/SubDeviceList/Battery/Current/Sensor/Value": -0.5,
            "PeoplePerception/VisiblePeopleList": [12, 15],
            "PeoplePerception/Person/12/Distance": 2.4,
            "PeoplePerception/Person/12/IsLookingAtRobot": 0,
            "PeoplePerception/Person/12/EngagementZone": 2,
            "PeoplePerception/Person/12/PresentSince": 40,
            "PeoplePerception/Person/15/Distance": 1.03,
            "PeoplePerception/Person/15/IsLookingAtRobot": 1,
            "PeoplePerception/Person/15/EngagementZone": 1,
            "PeoplePerception/Person/15/PresentSince": 12,
            "PeoplePerception/Person/15/PositionInTorsoFrame": [1.0, 0.25, 0.3],
        }
        self.services = {}
        self.connected = False

    def connect(self, url):
        self.connected = True

    def isConnected(self):
        return self.connected

    def service(self, name):
        if name not in self.services:
            self.services[name] = FakeService(name, self.memory)
        return self.services[name]

    def registerService(self, name, obj):
        if name in self.registered:
            raise RuntimeError("Service %s already registered" % name)
        self.registered[name] = obj
        return len(self.registered)

    def unregisterService(self, service_id):
        for name in list(self.registered):
            if list(self.registered).index(name) + 1 == service_id:
                del self.registered[name]


@pytest.fixture(scope="module")
def bridge():
    """Import pepper_bridge with a fake qi module installed."""
    fake_qi = types.ModuleType("qi")
    fake_qi.Session = FakeSession
    saved = sys.modules.get("qi")
    sys.modules["qi"] = fake_qi
    try:
        spec = importlib.util.spec_from_file_location("pepper_bridge_under_test", BRIDGE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["qi"] = saved
        else:
            sys.modules.pop("qi", None)
    module.NAOQI.connect()
    return module


@pytest.fixture
def robot(bridge):
    bridge.NAOQI._session = FakeSession()
    bridge.NAOQI._session.connect(bridge.NAOQI.url)
    bridge.NAOQI._services = {}
    r = bridge.Robot(bridge.NAOQI)
    now = [1000.0]  # simulated time: sleeping advances it, so settle loops run instantly
    r.clock = lambda: now[0]
    r.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    return r


def calls(robot, service):
    return robot.session.service(service).calls


def clear_path(robot, front=1.5, back=1.8):
    mem = robot.session._session.memory
    mem["Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value"] = front
    mem["Device/SubDeviceList/Platform/Back/Sonar/Sensor/Value"] = back


# ---------------------------------------------------------------------------
# Python 2.7 compatibility
# ---------------------------------------------------------------------------


class TestPython27Compatibility:

    def test_no_python3_only_syntax(self):
        tree = ast.parse(BRIDGE_PATH.read_text())
        for node in ast.walk(tree):
            assert not isinstance(node, ast.JoinedStr), f"f-string at line {node.lineno}"
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)
            ), f"async syntax at line {node.lineno}"
            assert not isinstance(node, ast.Nonlocal), f"nonlocal at line {node.lineno}"
            assert not isinstance(node, ast.AnnAssign), f"annotation at line {node.lineno}"
            if isinstance(node, (ast.FunctionDef,)):
                assert node.returns is None, f"return annotation at line {node.lineno}"
                assert not node.args.kwonlyargs, f"keyword-only args at line {node.lineno}"
                for arg in node.args.args:
                    assert arg.annotation is None, f"arg annotation at line {node.lineno}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("pathlib", "asyncio", "typing"), f"py3 module at line {node.lineno}"
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("pathlib", "asyncio", "typing"), f"py3 module at line {node.lineno}"

    def test_no_print_function_with_keywords(self):
        src = BRIDGE_PATH.read_text()
        assert "print(" not in src.replace("pprint(", ""), "use logging in the bridge, not print"


# ---------------------------------------------------------------------------
# Robot facade
# ---------------------------------------------------------------------------


class TestRobotFacade:

    def test_health_and_status(self, robot, bridge):
        health = robot.health()
        assert health["version"] == bridge.BRIDGE_VERSION and health["naoqi"] == "2.5.10.7"
        status = robot.status()
        assert status["battery"] == 77
        assert status["posture"] == "Stand"
        assert status["awake"] is True
        assert status["autonomous_life"] == "solitary"
        assert status["language"] == "English"
        assert status["volume"] == 60

    def test_sensors_use_pepper_keys(self, robot):
        data = robot.sensors()
        assert data["touch"]["head_front"] is True
        assert data["touch"]["hand_left"] is False
        assert data["sonar"] == {"front": 0.3, "back": 1.8}
        assert data["obstacle"] is True
        assert data["battery"] == 77 and data["charging"] is False
        assert data["people_count"] == 2 and data["people_ids"] == [12, 15]
        assert data["people"] == [  # nearest first
            {"id": 15, "distance": 1.03, "looking": True, "zone": 1, "present_for": 12, "yaw": 14.0, "pitch": 2.8},
            {"id": 12, "distance": 2.4, "looking": False, "zone": 2, "present_for": 40},
        ]
        assert data["bumpers"]["back"] is False

    def test_head_direction_to_face_a_person(self, bridge):
        assert bridge.head_direction([2.0, 0.0, 0.35]) == {"yaw": 0.0, "pitch": -0.0}
        assert bridge.head_direction([1.0, -1.0, 0.35])["yaw"] == -45.0  # to Pepper's right
        assert bridge.head_direction([1.0, 0.0, 1.35])["pitch"] == -45.0  # a tall person close up: look up
        assert bridge.head_direction([-1.0, 0.0, 0.3]) == {}  # behind the camera: no answer
        assert bridge.head_direction(None) == {} and bridge.head_direction([1.0]) == {}

    def test_move_head_waits_until_the_head_has_stopped(self, robot):
        motion = robot.svc("ALMotion")
        result = robot.move_head(60, 0, 0.2)
        assert result["settled"] is True and result["waited"] > 0.5  # about 1 rad at 0.1 rad per 50 ms reading
        assert abs(motion.head[0] - 1.0472) < 1e-3  # the head really is there
        assert result["measured"] == [60.0, 0.0]
        assert robot.move_head(0, 0, 0.2, wait=False).get("settled") is None  # reflexes do not wait

    def test_move_head_stops_waiting_when_the_head_stopped_short(self, robot):
        robot.svc("ALMotion").head_step = 0.0  # stuck (a /stop mid-turn, a hand in the way)
        result = robot.move_head(30, 0, 0.2)
        assert result["settled"] is False and result["waited"] < 1.0  # not the full 3 s

    def test_move_head_gives_up_at_the_timeout_while_still_moving(self, robot):
        robot.svc("ALMotion").head_step = 0.01  # creeping (0.6 degrees per reading): never still, not there in 3 s
        result = robot.move_head(90, 0, 0.05)
        assert result["settled"] is False and 3.0 <= result["waited"] < 3.2

    def test_emergency_stop_ends_the_wait(self, robot):
        motion = robot.svc("ALMotion")
        motion.head_step = 0.0
        robot.sleep = lambda seconds: setattr(robot, "halted", True)  # a stop arrives mid-wait
        assert robot.move_head(30, 0, 0.2)["settled"] is False

    def test_photo_waits_for_a_still_head_and_drops_the_first_frame(self, robot):
        motion = robot.svc("ALMotion")
        motion.head_target = [0.5, 0.0]  # still turning from an earlier move
        video = robot.svc("ALVideoDevice")
        video._respond = lambda method, args: (
            "handle"
            if method == "subscribeCamera"
            else [2, 1, 3, 0, 0, 0, b"\x00" * 6] if method == "getImageRemote" else None
        )
        result = robot.picture(0, 2)
        assert result["head_still"] is True and abs(motion.head[0] - 0.5) < 1e-6
        assert result["head"] == [28.6, 0.0]  # measured at the shot: 0.5 rad
        assert [c[0] for c in video.calls].count("getImageRemote") == 2

    def test_photo_pauses_face_tracking_and_resumes_it_soon(self, robot, bridge, monkeypatch):
        scheduled = []
        monkeypatch.setattr(robot, "_schedule_awareness_resume", lambda delay=None: scheduled.append(delay))
        awareness = robot.svc("ALBasicAwareness")
        awareness._respond = lambda method, args: {"isEnabled": True, "isAwarenessPaused": False}.get(method)
        video = robot.svc("ALVideoDevice")
        video._respond = lambda method, args: (
            "handle"
            if method == "subscribeCamera"
            else [2, 1, 3, 0, 0, 0, b"\x00" * 6] if method == "getImageRemote" else None
        )
        robot.picture(0, 2)
        assert ("setEnabled", (False,)) in awareness.calls  # off for the shot, not just paused
        assert scheduled[-1] == bridge.PHOTO_TRACKING_RESUME

    def test_neutral_pose_moves_arms_and_legs_not_the_head(self, robot, bridge):
        assert robot.neutral_pose() == {"joints": len(bridge.NEUTRAL_JOINTS)}
        call = [c for c in calls(robot, "ALMotion") if c[0] == "angleInterpolationWithSpeed"][0]
        names = call[1][0]
        assert "LShoulderPitch" in names and "HeadYaw" not in names and "HeadPitch" not in names

    def test_neutral_pose_is_skipped_while_resting_halted_or_animating(self, robot):
        robot._animations = 1
        assert robot.neutral_pose() == {"skipped": "animation running"}
        robot._animations = 0
        robot.svc("ALMotion").awake = False
        assert robot.neutral_pose() == {"skipped": "resting"}
        robot.halted = True
        assert robot.neutral_pose() == {"skipped": "halted"}

    def test_missing_person_details_are_none(self, robot):
        mem = robot.session._session.memory
        mem["PeoplePerception/VisiblePeopleList"] = [99]
        assert robot.sensors()["people"] == [
            {"id": 99, "distance": None, "looking": None, "zone": None, "present_for": None}
        ]
        mem["PeoplePerception/VisiblePeopleList"] = []
        assert robot.sensors()["people"] == []

    def test_speak_animated_with_language_code_restores_language(self, robot):
        result = robot.speak("Bonjour", language="fr", animated=True)
        tts = calls(robot, "ALTextToSpeech")
        set_calls = [c for c in tts if c[0] == "setLanguage"]
        assert set_calls == [("setLanguage", ("French",)), ("setLanguage", ("English",))]
        assert robot.session.service("ALTextToSpeech").language == "English"
        anim = calls(robot, "ALAnimatedSpeech")
        assert anim[0][0] == "say" and anim[0][1][0] == "Bonjour"
        assert anim[0][1][1] == {"bodyLanguageMode": "contextual"}
        assert result["spoken"] == "Bonjour" and result["animated"] is True and result["language"] == "fr"

    def test_speak_plain_does_not_change_language_when_same(self, robot):
        robot.speak("Hello", language="English", animated=False)
        tts = calls(robot, "ALTextToSpeech")
        assert ("say", ("Hello",)) in tts
        assert not any(c[0] == "setLanguage" for c in tts)

    def test_speak_rejects_uninstalled_language(self, robot):
        with pytest.raises(ValueError, match="not installed"):
            robot.speak("Hola", language="Spanish")

    def test_speak_requires_text(self, robot):
        with pytest.raises(ValueError):
            robot.speak("")

    def test_move_forward_clamps_and_uses_velocity_config(self, robot):
        clear_path(robot)
        result = robot.move_forward(5, 2)
        motion = calls(robot, "ALMotion")
        move = [c for c in motion if c[0] == "moveTo"][0]
        assert move[1][:3] == (2.0, 0.0, 0.0)
        assert move[1][3] == [["MaxVelXY", 0.55]]
        assert result == {"distance": 2.0, "speed": 0.55, "completed": True}

    def test_motion_refused_while_resting(self, robot):
        robot.session.service("ALMotion").awake = False
        with pytest.raises(RuntimeError, match="resting"):
            robot.turn(90)
        assert not any(c[0] in ("moveTo", "wakeUp") for c in calls(robot, "ALMotion"))

    def test_turn_converts_degrees(self, robot):
        result = robot.turn(90)
        turn = [c for c in calls(robot, "ALMotion") if c[0] == "moveTo"][0]
        assert abs(turn[1][2] - 1.5708) < 0.01 and result["completed"] is True

    def test_zero_sonar_means_no_measurement_not_obstacle(self, robot):
        clear_path(robot, front=0.0, back=0.0)
        sensors = robot.sensors()
        assert sensors["sonar"] == {"front": None, "back": None} and sensors["obstacle"] is False
        assert robot.move_forward(0.3, 0.3)["completed"] is True  # NAOqi's own protection still applies

    def test_move_forward_refused_when_obstacle_ahead(self, robot):
        # FakeSession memory: front sonar 0.3 m, back 1.8 m
        with pytest.raises(ValueError, match="front sonar shows an obstacle at 0.30"):
            robot.move_forward(0.5, 0.3)
        assert not any(c[0] == "moveTo" for c in calls(robot, "ALMotion"))
        assert robot.move_forward(-0.5, 0.3)["completed"] is True  # backwards is clear
        assert robot.move_forward(0.5, 0.3, force=True)["completed"] is True
        with pytest.raises(ValueError, match="front sonar"):
            robot.move_to(1.0, 0, 0)

    def test_motion_refused_after_emergency_stop_until_wake(self, robot):
        clear_path(robot)
        robot.emergency_stop()
        with pytest.raises(RuntimeError, match="halted"):
            robot.move_forward(0.3, 0.3)
        with pytest.raises(RuntimeError, match="halted"):
            robot.play_animation("animations/Stand/Gestures/Hey_1")
        assert robot.wake_up() == {"awake": True, "halted": False}
        assert robot.move_forward(0.3, 0.3)["completed"] is True

    def test_move_head_clamps_to_pepper_limits(self, robot):
        result = robot.move_head(200, -90, 5)
        motion = calls(robot, "ALMotion")
        set_angles = [c for c in motion if c[0] == "setAngles"][0]
        names, angles, speed = set_angles[1]
        assert names == ["HeadYaw", "HeadPitch"]
        assert result["yaw"] == 119.5 and result["pitch"] == -35.0  # pitch range shrinks when the head is turned
        assert result["awareness_paused"] is False and result["settled"] is True
        assert abs(angles[0] - 2.0857) < 1e-3 and abs(angles[1] + 0.6109) < 1e-3
        assert speed == 0.6

    def test_move_head_pitch_limit_straight_ahead(self, robot, bridge):
        for pitch, expected in ((90, 25.5), (-90, -40.5)):
            result = robot.move_head(0, pitch, 0.2, wait=False)
            assert result == {"yaw": 0.0, "pitch": expected, "awareness_paused": False}
        assert bridge.head_pitch_limits(50) == (-35.2, 20.9)

    def test_emergency_stop_kills_behaviours_motion_and_rests(self, robot):
        result = robot.emergency_stop()
        motion = calls(robot, "ALMotion")
        assert ("killMove", ()) in motion and ("killAll", ()) in motion and ("rest", ()) in motion
        assert not any(c[0] == "setStiffnesses" for c in motion)  # not allowed on Pepper's body
        assert ("stopAllBehaviors", ()) in calls(robot, "ALBehaviorManager")  # animations are behaviours
        assert ("stopAll", ()) in calls(robot, "ALTextToSpeech")
        assert motion.index(("stopMove", ())) < motion.index(("rest", ()))  # rest right after killAll is a no-op
        assert result == {"resting": True, "awake": False, "halted": True}
        assert robot.status()["halted"] is True

    def test_stop_speaking_ends_a_sentence_still_in_flight(self, robot):
        class Pending(FakeFuture):
            def __init__(self):
                super(Pending, self).__init__(finished=False)
                self.stops = 0

            def cancel(self):
                self.cancelled = True

        pending = Pending()
        robot._speech.add(pending)
        tts = robot.session.service("ALTextToSpeech")
        original = tts._respond

        def respond(method, args):
            if method == "stopAll":
                pending.stops += 1
                if pending.stops == 2:  # the second stopAll lands once the sentence has started
                    robot._speech.discard(pending)
            return original(method, args)

        tts._respond = respond
        assert robot.stop_speaking() == {}
        assert pending.cancelled and pending.stops == 3 and not robot._speech  # one last stopAll after it ends

    def test_a_stopped_sentence_is_not_an_error(self, robot):
        anim = robot.session.service("ALAnimatedSpeech")
        robot._speech_cancelled_at = __import__("time").time()
        anim.say = lambda *a, **k: FakeFuture(error="say cancelled")
        assert robot.speak("hello", animated=True)["spoken"] == "hello"

    def test_animation_completes(self, robot):
        result = robot.play_animation("animations/Stand/Gestures/Hey_1")
        assert result["completed"] is True and result["animation"] == "animations/Stand/Gestures/Hey_1"
        assert ("run", ("animations/Stand/Gestures/Hey_1",)) in calls(robot, "ALAnimationPlayer")

    def test_looping_animation_is_stopped_at_the_time_limit(self, robot):
        result = robot.play_animation("animations/LED/CircleEyes")
        assert result["completed"] is False
        assert ("stopAllBehaviors", ()) in calls(robot, "ALBehaviorManager")
        assert robot.session.service("ALAnimationPlayer").last_future.cancelled is True

    def test_emergency_stop_retries_rest_and_reports_the_truth(self, bridge, robot, monkeypatch):
        monkeypatch.setattr(bridge, "REST_RETRY_DELAY", 0)
        motion = robot.session.service("ALMotion")
        original = motion._respond
        rests = []

        def respond(method, args):
            if method == "rest":
                rests.append(1)
                if len(rests) < 3:
                    return None  # the robot ignores it, like right after killAll()
            return original(method, args)

        motion._respond = respond
        motion.awake = True
        assert robot.emergency_stop() == {"resting": True, "awake": False, "halted": True}
        assert len(rests) == 3

    def test_emergency_stop_says_so_when_the_motors_stay_on(self, bridge, robot, monkeypatch):
        monkeypatch.setattr(bridge, "REST_RETRY_DELAY", 0)
        motion = robot.session.service("ALMotion")
        original = motion._respond
        motion._respond = lambda m, a: None if m == "rest" else original(m, a)
        motion.awake = True
        result = robot.emergency_stop()
        assert result == {"resting": False, "awake": True, "halted": True}
        assert calls(robot, "ALMotion").count(("rest", ())) == bridge.REST_ATTEMPTS

    def test_stop_also_stops_behaviours(self, robot):
        robot.stop()
        assert ("stopAllBehaviors", ()) in calls(robot, "ALBehaviorManager")
        assert ("stopMove", ()) in calls(robot, "ALMotion")

    def test_move_reports_completion(self, robot):
        clear_path(robot)
        assert robot.move_forward(0.5, 0.3)["completed"] is True
        assert robot.turn(30)["completed"] is True

    def test_status_reports_charging(self, robot):
        assert robot.status()["charging"] is False  # current -0.5 A in the fake memory
        robot.session._session.memory["Device/SubDeviceList/Battery/Current/Sensor/Value"] = 0.8
        assert robot.status()["charging"] is True

    def test_interactive_to_solitary_goes_via_disabled(self, robot):
        life = robot.session.service("ALAutonomousLife")
        life.life_state = "interactive"
        robot.set_autonomous_life("solitary")
        assert [c for c in life.calls if c[0] == "setState"] == [
            ("setState", ("disabled",)),
            ("setState", ("solitary",)),
        ]

    def test_extractors_subscribed_on_connect_and_rechecked(self, bridge, robot):
        session = bridge.NaoqiSession("tcp://fake")
        r = bridge.Robot(session)
        session.connect()
        assert r.extractors == {
            "ALSonar": True,
            "ALPeoplePerception": True,
            "ALGazeAnalysis": True,
            "ALEngagementZones": True,
        }
        assert ("subscribe", ("pepper_bridge",)) in session.service("ALSonar").calls
        assert ("subscribe", ("pepper_bridge",)) in session.service("ALPeoplePerception").calls
        r.check_extractors()  # getSubscribersInfo returns None in the fake -> re-subscribe
        assert session.service("ALSonar").calls.count(("subscribe", ("pepper_bridge",))) == 2
        assert r.sensors()["sonar_ok"] is True

    def test_posture_validation(self, robot):
        with pytest.raises(ValueError):
            robot.set_posture("Handstand", 0.5)
        assert robot.set_posture("Stand", 5)["reached"] is True
        assert ("goToPosture", ("Stand", 1.0)) in calls(robot, "ALRobotPosture")

    def test_prepare_disables_life_and_wakes(self, robot):
        life = robot.session.service("ALAutonomousLife")
        motion = robot.session.service("ALMotion")
        motion.awake = False
        result = robot.prepare("disabled", True, "Stand", False)
        assert life.life_state == "disabled"
        assert result["errors"] == []
        assert result["autonomous_life"] == {"state": "disabled", "previous": "solitary"}
        assert ("wakeUp", ()) in motion.calls
        assert result["posture"]["posture"] == "Stand"
        assert result["awareness"] == {"enabled": False}
        assert ("setEnabled", (False,)) in calls(robot, "ALBasicAwareness")

    def test_prepare_continues_after_a_failed_step(self, robot):
        life = robot.session.service("ALAutonomousLife")
        life._respond = lambda method, args: (
            (_ for _ in ()).throw(RuntimeError("wizard not finished"))
            if method == "setState"
            else FakeService._respond(life, method, args)
        )
        motion = robot.session.service("ALMotion")
        motion.awake = False
        result = robot.prepare("disabled", True, None, None)
        assert result["errors"] and "wizard" in result["errors"][0]
        assert result["awake"] is True and ("wakeUp", ()) in motion.calls

    def test_prepare_is_idempotent_for_life_state(self, robot):
        life = robot.session.service("ALAutonomousLife")
        life.life_state = "disabled"
        robot.prepare("disabled", False, None, None)
        assert not any(c[0] == "setState" for c in life.calls)

    def test_picture_converts_to_jpeg_and_unsubscribes(self, robot, bridge):
        result = robot.picture(0, 2)
        video = calls(robot, "ALVideoDevice")
        assert video[0][0] == "subscribeCamera" and video[0][1][1:] == (0, 2, 11, 5)
        assert ("unsubscribe", ("handle_1",)) in video
        assert result["width"] == 2 and result["height"] == 1
        if bridge.PILImage is not None:
            assert result["format"] == "jpeg"
        else:
            assert result["format"] == "rgb"

    def test_leds_named_and_rgb(self, robot):
        assert robot.set_leds("eyes", color="blue")["rgb"] == "#0000ff"
        assert robot.set_leds("chest", r=1, g=0.5, b=0)["rgb"] == "#ff8000"
        leds = calls(robot, "ALLeds")
        assert leds[0][1][0] == "FaceLeds" and leds[1][1][0] == "ChestLeds"
        with pytest.raises(ValueError):
            robot.set_leds("eyes", color="plaid")

    def test_animations_listed_from_behavior_manager(self, robot):
        result = robot.list_animations()
        assert result["animations"] == ["animations/Stand/Gestures/BowShort_1", "animations/Stand/Gestures/Hey_1"]

    def test_tablet_text_shows_bridge_page_once(self, robot, bridge):
        robot.tablet_text("Hello", "Pepper")
        robot.tablet_text("Again")
        tablet = calls(robot, "ALTabletService")
        shows = [c for c in tablet if c[0] == "showWebview"]
        assert len(shows) == 1
        assert shows[0][1][0].startswith("http://198.18.0.1:8888/tablet/page")
        assert robot.tablet_state["text"] == "Again"

    def test_unicode_text_passes_through(self, bridge):
        assert bridge.to_native_str("café") == "café"  # bytes on Python 2, str on Python 3
        assert bridge.to_native_str(None) == ""

    def test_helpers(self, bridge):
        assert bridge.as_bool("true") is True and bridge.as_bool("0") is False and bridge.as_bool(None, True) is True
        assert bridge.clamp(5, 0, 3) == 3
        assert bridge.rgb_to_int(1, 1, 1) == 0xFFFFFF


class TestSensorPoller:

    def test_edge_triggered_events(self, robot, bridge, monkeypatch):
        events = []
        monkeypatch.setattr(bridge, "broadcast_from_thread", lambda t, p: events.append((t, p)))
        poller = bridge.SensorPoller(robot)
        first = robot.sensors()
        poller._diff(None, first)
        types_ = [e[0] for e in events]
        assert "touch" in types_ and "sonar" in types_ and "battery" in types_
        assert "people" not in types_  # people changes wait until they have been stable (see below)
        touch = [e for e in events if e[0] == "touch"]
        assert touch == [("touch", {"sensor": "head_front", "touched": True})]

        events.clear()
        poller._diff(first, first)
        assert events == []

        robot.session._session.memory["Device/SubDeviceList/Head/Touch/Front/Sensor/Value"] = 0.0
        robot.session._session.memory["Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value"] = 1.5
        second = robot.sensors()
        poller._diff(first, second)
        assert ("touch", {"sensor": "head_front", "touched": False}) in events
        assert ("sonar", {"front": 1.5, "back": 1.8, "obstacle": False}) in events


class TestApplication:

    def test_routes_cover_documented_endpoints(self, bridge):
        app = bridge.make_app()
        patterns = set()
        for rule in app.wildcard_router.rules if hasattr(app, "wildcard_router") else app.handlers[0][1]:
            matcher = getattr(rule, "matcher", None)
            patterns.add(getattr(matcher, "regex", getattr(rule, "regex", None)).pattern)
        for path in (
            "/health",
            "/status",
            "/sensors",
            "/speak",
            "/prepare",
            "/picture",
            "/animations",
            "/tablet/text",
            "/tablet/page",
            "/ws/events",
            "/emergency_stop",
        ):
            assert any(p.startswith(path) for p in patterns), path

    def test_tablet_page_is_html(self, bridge):
        assert "<html" in bridge.TABLET_PAGE and "/tablet/state" in bridge.TABLET_PAGE


# ---------------------------------------------------------------------------
# Awareness options
# ---------------------------------------------------------------------------


class TestAwareness:
    def test_head_tracking_and_stimuli(self, robot):
        result = robot.set_awareness(True, tracking_mode="Head", stimuli=["People", "Sound"])
        ba = calls(robot, "ALBasicAwareness")
        assert ("setTrackingMode", ("Head",)) in ba
        assert ("setStimulusDetectionEnabled", ("People", True)) in ba
        assert ("setStimulusDetectionEnabled", ("Movement", False)) in ba
        assert ("setEnabled", (True,)) in ba and ba.index(("setEnabled", (True,))) > ba.index(
            ("setTrackingMode", ("Head",))
        )
        assert result == {"enabled": True, "tracking_mode": "Head", "stimuli": ["People", "Sound"]}

    def test_a_stimulus_this_naoqi_lacks_is_reported_not_fatal(self, robot):
        ba = robot.session.service("ALBasicAwareness")
        original = ba._respond

        def respond(method, args):
            if method == "setStimulusDetectionEnabled" and args[0] == "Sound":
                raise RuntimeError("ALBasicAwareness::setStimulusDetectionEnabled Wrong stimulus name, got: Sound")
            return original(method, args)

        ba._respond = respond
        result = robot.set_awareness(True, stimuli=["People", "Sound"])
        assert result["stimuli"] == ["People"] and result["stimuli_unavailable"] == ["Sound"]
        assert ("setEnabled", (True,)) in ba.calls

    def test_stimuli_from_a_query_string(self, robot):
        assert robot.set_awareness(True, stimuli="People, Touch")["stimuli"] == ["People", "Touch"]
        assert robot.set_awareness(True, stimuli="People,Sound")["stimuli"] == ["People", "Sound"]
        assert "stimuli" not in robot.set_awareness(True, stimuli=[])  # empty list: configuration untouched

    def test_engagement_mode(self, robot):
        result = robot.set_awareness(True, engagement_mode="SemiEngaged")
        assert result["engagement_mode"] == "SemiEngaged"
        assert ("setEngagementMode", ("SemiEngaged",)) in calls(robot, "ALBasicAwareness")

    def test_rejects_unknown_options(self, robot):
        with pytest.raises(ValueError):
            robot.set_awareness(True, tracking_mode="Spin")
        with pytest.raises(ValueError):
            robot.set_awareness(True, engagement_mode="Clingy")
        with pytest.raises(ValueError):
            robot.set_awareness(True, stimuli=["People", "Ghosts"])
        assert not any(c[0] == "setEnabled" for c in calls(robot, "ALBasicAwareness"))

    def test_off_ignores_options(self, robot):
        assert robot.set_awareness(False, tracking_mode="Head", stimuli=["People"]) == {"enabled": False}
        ba = calls(robot, "ALBasicAwareness")
        assert ba == [("setEnabled", (False,))]

    def test_head_move_holds_tracking_off_and_turns_it_back_on(self, bridge, robot, monkeypatch):
        # On the robot a merely *paused* tracker still pulled the head back to the face, so a head move
        # switches tracking off (setEnabled(False)) and on again after the hold.
        import time as _time

        monkeypatch.setattr(bridge, "AWARENESS_RESUME_AFTER", 0.05)
        ba = robot.session.service("ALBasicAwareness")
        state = {"enabled": True}
        original = ba._respond

        def respond(method, args):
            if method == "isEnabled":
                return state["enabled"]
            if method == "setEnabled":
                state["enabled"] = bool(args[0])
                return None
            return original(method, args)

        ba._respond = respond
        result = robot.move_head(30, 0, 0.2)
        assert result["awareness_paused"] is True and state["enabled"] is False
        assert robot.status()["awareness"] is True  # held, not off: it comes back by itself
        robot.move_head(-30, 0, 0.2)  # a second move extends the hold, no second switch-off
        assert ba.calls.count(("setEnabled", (False,))) == 1
        for _ in range(50):
            if state["enabled"]:
                break
            _time.sleep(0.01)
        assert state["enabled"] is True and ("setEnabled", (True,)) in ba.calls

    def test_an_explicit_request_cancels_the_hold(self, bridge, robot):
        ba = robot.session.service("ALBasicAwareness")
        original = ba._respond
        ba._respond = lambda m, a: True if m == "isEnabled" else original(m, a)
        robot.move_head(30, 0, 0.2)
        robot.set_awareness(False)  # "stop looking at me" during the hold: stays off
        assert robot._tracking_held is False and robot._awareness_resume is None

    def test_head_move_leaves_disabled_tracking_alone(self, robot):
        result = robot.move_head(10, 0, 0.2)
        assert result["awareness_paused"] is False
        assert not any(c[0] in ("pauseAwareness", "setEnabled") for c in calls(robot, "ALBasicAwareness"))

    def test_enabling_awareness_resumes_a_paused_tracker(self, robot):
        ba = robot.session.service("ALBasicAwareness")
        original = ba._respond
        ba._respond = lambda m, a: True if m == "isAwarenessPaused" else original(m, a)
        robot.set_awareness(True)
        assert ba.calls[-1] == ("resumeAwareness", ())

    def test_prepare_enables_head_tracking_only(self, robot):
        result = robot.prepare("", False, None, True)
        assert result["awareness"] == {
            "enabled": True,
            "tracking_mode": "Head",
            "stimuli": ["People", "Touch"],
        }
        assert ("setStimulusDetectionEnabled", ("Movement", False)) in calls(robot, "ALBasicAwareness")


# ---------------------------------------------------------------------------
# Microphone streaming
# ---------------------------------------------------------------------------


@pytest.fixture
def audio(bridge, robot):
    bridge.AudioWebSocket.clients.clear()
    tap = bridge.AudioTap(robot.session)
    tap.register(robot.session._session)
    yield tap
    bridge.AudioWebSocket.clients.clear()
    bridge.AUDIO.subscribed = False  # never leak a started global tap into the next test
    bridge.AUDIO.host_muted = False


class FakeClient:
    """Stands in for a connected AudioWebSocket (only what broadcast/close_all touch)."""

    def __init__(self, slow=False):
        self.slow = slow
        self.behind = 0
        self.frames = []
        self.messages = []
        self.closed = False

    def _still_writing(self):
        return self.slow

    def write_message(self, data, binary=False):
        self.frames.append(data)

    def send_json(self, payload):
        self.messages.append(payload)

    def close(self):
        self.closed = True


def connect_client(bridge, slow=False):
    client = FakeClient(slow=slow)
    bridge.AudioWebSocket.clients.add(client)
    return client


@pytest.fixture
def sent(bridge, monkeypatch):
    """Capture what the tap hands to the IOLoop instead of needing a running loop."""
    frames = []

    class Loop:
        def add_callback(self, fn, *args):
            frames.append((fn, args))

    monkeypatch.setattr(bridge, "IOLOOP", Loop())
    return frames


class TestAudioTap:
    def test_registers_a_service_and_the_tts_status_event(self, robot, audio):
        session = robot.session._session
        assert audio.service_id is not None
        assert "PepperBridgeAudio" in session.registered
        callback = session.registered["PepperBridgeAudio"]
        assert hasattr(callback, "processRemote") and not hasattr(callback, "start")  # only the callback is exposed
        memory = robot.session.service("ALMemory")
        assert memory.subscribers[0].key == "ALTextToSpeech/Status"

    def test_start_subscribes_front_mic_at_16k(self, bridge, robot, audio):
        connect_client(bridge)
        info = audio.start()
        device = calls(robot, "ALAudioDevice")
        assert ("setClientPreferences", ("PepperBridgeAudio", 16000, 3, 0)) in device
        assert device[-1] == ("subscribe", ("PepperBridgeAudio",))
        assert info["streaming"] is True and info["sample_rate"] == 16000 and info["format"] == "pcm_s16le"
        audio.start()  # idempotent
        assert device.count(("subscribe", ("PepperBridgeAudio",))) == 1

    def test_start_requires_a_registered_service(self, bridge, robot):
        bridge.AudioWebSocket.clients.clear()
        connect_client(bridge)
        tap = bridge.AudioTap(robot.session)  # never registered (NAOqi not connected)
        with pytest.raises(RuntimeError):
            tap.start()

    def test_start_without_a_client_does_nothing(self, robot, audio):
        assert audio.start()["streaming"] is False  # the client left while start() was queued
        assert not any(c[0] == "subscribe" for c in calls(robot, "ALAudioDevice"))

    def test_register_restarts_capture_for_connected_clients(self, bridge, robot, audio, monkeypatch):
        monkeypatch.setattr(bridge, "_run_in_thread", lambda fn, *a: fn(*a))
        monkeypatch.setattr(bridge, "AUDIO", audio)  # start_capture works on the module's tap
        connect_client(bridge)
        audio.host_muted = True
        audio.tts_active = True
        audio.speaking = 1
        session = robot.session._session
        session.registered.clear()  # NAOqi restarted: our service is gone
        audio.register(session)
        assert audio.subscribed is True and "PepperBridgeAudio" in session.registered
        assert audio.host_muted is False and audio.tts_active is False and audio.speaking == 0

    def test_failed_start_disconnects_clients_so_they_retry(self, bridge, robot, monkeypatch):
        bridge.AudioWebSocket.clients.clear()
        client = connect_client(bridge)
        tap = bridge.AudioTap(robot.session)  # not registered -> start() raises
        monkeypatch.setattr(bridge, "AUDIO", tap)
        called = []
        monkeypatch.setattr(
            bridge, "IOLOOP", type("Loop", (), {"add_callback": lambda self, fn, *a: called.append((fn, a))})()
        )
        bridge.AudioWebSocket.start_capture()
        fn, args = called[0]
        fn(*args)
        assert client.closed and client.messages[-1]["type"] == "error"
        assert not bridge.AudioWebSocket.clients

    def test_stop_keeps_capture_while_a_client_is_connected(self, bridge, robot, audio):
        connect_client(bridge)
        audio.start()
        assert audio.stop()["streaming"] is True
        bridge.AudioWebSocket.clients.clear()
        assert audio.stop()["streaming"] is False
        assert calls(robot, "ALAudioDevice")[-1] == ("unsubscribe", ("PepperBridgeAudio",))
        audio.stop()  # idempotent
        assert calls(robot, "ALAudioDevice").count(("unsubscribe", ("PepperBridgeAudio",))) == 1

    def test_frames_are_forwarded_only_while_streaming_and_not_muted(self, bridge, audio, sent):
        frame = b"\x01\x00" * 4
        connect_client(bridge)
        audio.on_buffer(1, 4, [0, 0], frame)  # not subscribed yet
        assert sent == [] and audio.frames == 1
        audio.start()
        audio.on_buffer(1, 4, [0, 0], bytearray(frame))
        assert sent == [(bridge.AudioWebSocket.broadcast, (frame,))]
        audio.speaking_begin()
        audio.on_buffer(1, 4, [0, 0], frame)
        assert len(sent) == 1 and audio.dropped == 1
        audio.speaking_end()  # still muted for the tail
        audio.on_buffer(1, 4, [0, 0], frame)
        assert len(sent) == 1 and audio.dropped == 2
        audio.muted_until = 0.0
        audio.on_buffer(1, 4, [0, 0], frame)
        assert len(sent) == 2

    def test_tts_status_events_mute_capture(self, robot, audio):
        status = robot.session.service("ALMemory").subscribers[0]
        status.fire([3, "started"])
        assert audio.muted() is True
        status.fire([3, "done"])
        audio.muted_until = 0.0
        assert audio.muted() is False
        status.fire("garbage")  # ignored
        status.fire([4, "thrown"])
        assert audio.tts_active is False

    def test_host_mute(self, audio):
        audio.host_muted = True
        assert audio.muted() is True
        audio.host_muted = False
        audio.muted_until = 0.0
        assert audio.muted() is False

    def test_a_lost_tts_done_event_stops_muting_eventually(self, bridge, audio):
        audio._on_tts_status([1, "started"])
        assert audio.muted() is True
        audio.tts_started_at -= bridge.TTS_EVENT_TIMEOUT + 1
        audio.muted_until = 0.0
        assert audio.muted() is False and audio.tts_active is False

    def test_broadcast_drops_clients_that_stop_reading(self, bridge):
        bridge.AudioWebSocket.clients.clear()
        fast, slow = connect_client(bridge), connect_client(bridge, slow=True)
        for _ in range(bridge.AUDIO_MAX_BEHIND + 1):
            bridge.AudioWebSocket.broadcast(b"\x00\x00")
        assert len(fast.frames) == bridge.AUDIO_MAX_BEHIND + 1 and fast.behind == 0
        assert len(slow.frames) == bridge.AUDIO_MAX_BEHIND and slow.closed
        assert bridge.AudioWebSocket.clients == {fast}

    def test_close_all_reports_the_error(self, bridge):
        bridge.AudioWebSocket.clients.clear()
        client = connect_client(bridge)
        bridge.AudioWebSocket.close_all("NAOqi not connected")
        assert client.closed and client.messages == [{"type": "error", "error": "NAOqi not connected"}]
        assert not bridge.AudioWebSocket.clients

    def test_speak_mutes_capture_while_talking(self, bridge, robot):
        anim = robot.session.service("ALAnimatedSpeech")
        muted_during = []
        original = anim._respond

        def respond(method, args):
            if method == "say":
                muted_during.append(bridge.AUDIO.muted())
            return original(method, args)

        anim._respond = respond
        bridge.AUDIO.muted_until = 0.0
        assert bridge.AUDIO.muted() is False
        robot.speak("hello there", animated=True)
        assert muted_during == [True]
        assert bridge.AUDIO.speaking == 0 and bridge.AUDIO.muted() is True  # tail after speech
        bridge.AUDIO.muted_until = 0.0

    def test_unregister_drops_the_service(self, bridge, robot, audio):
        connect_client(bridge)
        audio.start()
        audio.unregister()
        assert audio.service_id is None
        assert "PepperBridgeAudio" not in robot.session._session.registered
        assert audio.subscribed is False

    def test_health_reports_the_stream(self, robot, audio, bridge):
        health = robot.health()
        assert health["audio"]["streaming"] is False and health["audio"]["clients"] == 0
        assert health["version"] == bridge.BRIDGE_VERSION


class TestPeopleDebounce:
    """The detector flickers on the robot; events must only follow changes that hold."""

    def make(self, bridge, robot, monkeypatch):
        events = []
        monkeypatch.setattr(bridge, "broadcast_from_thread", lambda t, p: events.append((t, p)))
        clock = [100.0]
        poller = bridge.SensorPoller(robot, clock=lambda: clock[0])
        return poller, events, clock

    @staticmethod
    def snap(count, *people):
        return {
            "people_count": count,
            "people_ids": list(range(count)),
            "people": [
                {"id": i, "distance": 1.0, "looking": looking, "zone": zone, "present_for": 1}
                for i, (zone, looking) in enumerate(people)
            ],
        }

    def test_a_change_is_reported_once_it_has_held(self, bridge, robot, monkeypatch):
        poller, events, clock = self.make(bridge, robot, monkeypatch)
        one = self.snap(1, (1, True))
        poller._people_changes(one)
        clock[0] += 0.5
        poller._people_changes(one)
        assert events == []
        clock[0] += 0.6
        poller._people_changes(one)
        assert [e[0] for e in events] == ["people"]
        assert events[0][1]["count"] == 1 and events[0][1]["people"][0]["looking"] is True
        clock[0] += 5
        poller._people_changes(one)
        assert len(events) == 1  # nothing new, nothing sent

    def test_flicker_is_ignored(self, bridge, robot, monkeypatch):
        poller, events, clock = self.make(bridge, robot, monkeypatch)
        one, two = self.snap(1, (1, True)), self.snap(2, (1, True), (2, False))
        for _ in range(3):
            poller._people_changes(one)
            clock[0] += 1.1
        poller._people_changes(one)
        events.clear()
        for _ in range(20):  # like the robot: 1, 2, 1, 2 ... every quarter second
            poller._people_changes(two)
            clock[0] += 0.25
            poller._people_changes(one)
            clock[0] += 0.25
        assert events == []

    def test_gaze_and_zone_changes_count(self, bridge, robot, monkeypatch):
        poller, events, clock = self.make(bridge, robot, monkeypatch)
        for state in (self.snap(1, (2, False)), self.snap(1, (1, True))):
            poller._people_changes(state)
            clock[0] += 1.1
            poller._people_changes(state)
        assert [e[1]["people"][0]["zone"] for e in events] == [2, 1]  # walked closer and looked at Pepper

    def test_everyone_leaving_is_reported(self, bridge, robot, monkeypatch):
        poller, events, clock = self.make(bridge, robot, monkeypatch)
        for state in (self.snap(1, (1, True)), self.snap(0)):
            poller._people_changes(state)
            clock[0] += 1.1
            poller._people_changes(state)
        assert [e[1]["count"] for e in events] == [1, 0]


def test_bridge_and_host_versions_match(bridge):
    from src import __version__

    assert bridge.BRIDGE_VERSION == __version__  # the robot cannot import src/, so the number lives twice


class TestCameraStream:
    """/ws/camera: one persistent subscription, JPEG frames, released when the last client leaves."""

    def make(self, bridge, robot, monkeypatch, frames=3):
        sent, scheduled = [], []

        class Loop:
            def add_callback(self, fn, *args):
                scheduled.append(fn.__name__)
                if fn.__name__ == "broadcast":
                    sent.append(args[0])

        monkeypatch.setattr(bridge, "main_ioloop", lambda: Loop())
        video = robot.session.service("ALVideoDevice")
        rgb = b"\x80" * (32 * 24 * 3)
        video._respond = lambda method, args: (
            "pepper_bridge_stream_0"
            if method == "subscribeCamera"
            else [32, 24, 3, 0, 0, 0, rgb] if method == "getImageRemote" else None
        )
        naps = []

        def sleep(seconds):
            naps.append(seconds)
            if len(naps) >= frames:
                stream.stop()

        stream = bridge.CameraStream(robot.session, clock=lambda: 100.0, sleep=sleep)
        return stream, video, sent, naps, scheduled

    def test_frames_are_jpeg_at_the_asked_rate_and_the_camera_is_released(self, bridge, robot, monkeypatch):
        stream, video, sent, naps, _ = self.make(bridge, robot, monkeypatch)
        stream.fps = 2.0
        stream._running = True
        stream._loop()
        assert len(sent) == 3 and all(f[:2] == b"\xff\xd8" for f in sent)  # JPEG
        assert naps == [0.5, 0.5, 0.5]
        names = [c[0] for c in video.calls]
        assert names[0] == "subscribeCamera" and video.calls[0][1][0] == "pepper_bridge_stream"
        assert names[-1] == "unsubscribe" and names.count("releaseImage") == 3

    def test_fps_is_capped(self, bridge, robot):
        stream = bridge.CameraStream(robot.session)
        stream._running = True  # do not start a thread
        stream.start(50)
        assert stream.fps == bridge.STREAM_MAX_FPS

    def test_stale_subscriptions_are_cleared(self, bridge, robot):
        bridge.CameraStream(robot.session).clear_stale()
        assert ("unsubscribeAllInstances", ("pepper_bridge_stream",)) in robot.session.service("ALVideoDevice").calls

    def test_a_refused_subscription_closes_the_clients(self, bridge, robot, monkeypatch):
        stream, video, sent, _, scheduled = self.make(bridge, robot, monkeypatch)
        video._respond = lambda method, args: None  # subscribeCamera refused
        stream._running = True
        stream._loop()
        assert sent == [] and "close_all" in scheduled and stream.last_error


class TestOfferHand:
    def run_with_arm(self, bridge, robot, pitches, hold=8):
        """offer_hand with the shoulder reading ``pitches`` (radians) one 0.1 s reading after another."""
        robot.session._session.memory[bridge.HAND_TOUCH_KEY] = 0.0
        motion = robot.svc("ALMotion")
        readings = iter(pitches)
        last = [pitches[-1]]

        def respond(method, args, original=motion._respond):
            if method == "getAngles" and "RShoulderPitch" in args[0]:
                last[0] = next(readings, last[0])
                return [last[0], -0.12, 0.3, 1.27, 0.0]
            return original(method, args)

        motion._respond = respond
        now = [0.0]
        robot.clock = lambda: now[0]
        robot.sleep = lambda s: now.__setitem__(0, now[0] + s)
        return robot.offer_hand(hold)

    def test_the_arm_settling_is_not_a_handshake(self, bridge, robot):
        # sagging ~3 degrees over the first second after reaching the pose, as on the robot
        sag = [0.35 + 0.0055 * i for i in range(10)] + [0.405] * 200
        result = self.run_with_arm(bridge, robot, sag, hold=5)
        assert result["taken"] is False

    def test_slow_drift_is_not_a_handshake(self, bridge, robot):
        drift = [0.35] * 5 + [0.35 + 0.0005 * i for i in range(100)]  # 3 degrees over 10 s, slowly
        assert self.run_with_arm(bridge, robot, drift, hold=8)["taken"] is False

    def test_a_reach_nudge_is_not_a_handshake(self, bridge, robot):
        # the arm collision protection moved the untouched arm 2.3 degrees as a hand came near (2026-10-08)
        nudge = [0.35] * 8 + [0.35] * 10 + [0.35 + 0.004 * i for i in range(10)] + [0.39] * 100
        assert self.run_with_arm(bridge, robot, nudge, hold=6)["taken"] is False

    def test_a_hand_taking_the_arms_weight_counts(self, bridge, robot):
        mem = robot.session._session.memory
        currents = iter([1.35] * 15 + [0.3] * 10)
        mem_get = robot.session._session.service("ALMemory")._respond

        def respond(method, args):
            if method == "getData" and args[0] == bridge.SHOULDER_CURRENT_KEY:
                return next(currents, 0.3)
            return mem_get(method, args)

        robot.session._session.service("ALMemory")._respond = respond
        mem[bridge.HAND_TOUCH_KEY] = 0.0
        result = self.run_with_arm(bridge, robot, [0.35] * 100)
        assert result["taken"] is True and result["how"] == "weight taken"
        assert result["shoulder_current_holding"] == 1.35 and result["trace"]

    def test_a_gripped_and_moved_hand_counts(self, bridge, robot):
        shake = [0.35] * 8 + [0.35] * 20 + [0.45, 0.28, 0.45]  # settled, then a person shakes it
        result = self.run_with_arm(bridge, robot, shake)
        assert result["taken"] is True and result["how"] == "shaken" and result["largest_move_deg"] >= 4.0

    def test_hand_held_out_then_shaken_when_taken(self, bridge, robot):
        mem = robot.session._session.memory
        mem[bridge.HAND_TOUCH_KEY] = 0.0
        naps = []

        def sleep(seconds):
            naps.append(seconds)
            if len(naps) == 3:
                mem[bridge.HAND_TOUCH_KEY] = 1.0  # someone takes the hand

        robot.sleep = sleep
        result = robot.offer_hand(8)
        assert result["taken"] is True and result["waited"] < 1.0
        motion = [c[0] for c in calls(robot, "ALMotion")]
        assert motion.count("angleInterpolationWithSpeed") >= 4  # out, grip, let go, neutral
        assert "angleInterpolation" in motion  # the shake

    def test_nobody_takes_it(self, bridge, robot):
        robot.session._session.memory[bridge.HAND_TOUCH_KEY] = 0.0
        result = robot.offer_hand(1.0)
        assert result["taken"] is False and result["waited"] >= 1.0
        assert "angleInterpolation" not in [c[0] for c in calls(robot, "ALMotion")]  # no shake

    def test_refused_while_resting(self, robot):
        import pytest

        robot.svc("ALMotion").awake = False
        with pytest.raises(RuntimeError, match="resting"):
            robot.offer_hand(5)
