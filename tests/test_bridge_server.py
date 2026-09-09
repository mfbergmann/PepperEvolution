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
        self.language = "English"
        self.awake = True
        self.life_state = "solitary"

    def __getattr__(self, method):
        def call(*args):
            self.calls.append((method, args))
            return self._respond(method, args)

        return call

    def _respond(self, method, args):
        if method == "getData":
            return self.memory[args[0]]
        if method == "getListData":
            return [self.memory.get(k) for k in args[0]]
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


class FakeSession:
    def __init__(self):
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
        assert data["bumpers"]["back"] is False

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
        assert result == {"yaw": 119.5, "pitch": -35.0}  # pitch range shrinks when the head is turned
        assert abs(angles[0] - 2.0857) < 1e-3 and abs(angles[1] + 0.6109) < 1e-3
        assert speed == 0.6

    def test_move_head_pitch_limit_straight_ahead(self, robot, bridge):
        assert robot.move_head(0, 90, 0.2) == {"yaw": 0.0, "pitch": 25.5}
        assert robot.move_head(0, -90, 0.2) == {"yaw": 0.0, "pitch": -40.5}
        assert bridge.head_pitch_limits(50) == (-35.2, 20.9)

    def test_emergency_stop_kills_behaviours_motion_and_rests(self, robot):
        result = robot.emergency_stop()
        motion = calls(robot, "ALMotion")
        assert ("killMove", ()) in motion and ("killAll", ()) in motion and ("rest", ()) in motion
        assert not any(c[0] == "setStiffnesses" for c in motion)  # not allowed on Pepper's body
        assert ("stopAllBehaviors", ()) in calls(robot, "ALBehaviorManager")  # animations are behaviours
        assert ("stopAll", ()) in calls(robot, "ALTextToSpeech")
        assert result == {"resting": True, "awake": False, "halted": True}
        assert robot.status()["halted"] is True

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
        assert r.extractors == {"ALSonar": True, "ALPeoplePerception": True}
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
        assert "touch" in types_ and "sonar" in types_ and "battery" in types_ and "people" in types_
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
