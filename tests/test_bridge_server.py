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
        assert ba[-1] == ("setEnabled", (True,))
        assert result == {"enabled": True, "tracking_mode": "Head", "stimuli": ["People", "Sound"]}

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

    def test_prepare_enables_head_tracking_only(self, robot):
        result = robot.prepare("", False, None, True)
        assert result["awareness"] == {
            "enabled": True,
            "tracking_mode": "Head",
            "stimuli": ["People", "Sound", "Touch"],
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
