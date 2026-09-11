#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pepper Bridge Server v2.1 - HTTP + WebSocket bridge for NAOqi 2.5.

Runs ON the Pepper robot under Python 2.7 with the Tornado that ships with
NAOqi (3.1.1). Exposes NAOqi services as JSON-over-HTTP endpoints and pushes
sensor events over a WebSocket, so the host never needs the qi SDK.

Design notes
- Every NAOqi call runs on a worker thread. The Tornado IOLoop is never
  blocked, so /stop, /emergency_stop and /sensors always get through even
  while the robot is speaking or driving.
- Tornado's IOLoop is single-threaded: anything that touches a handler or a
  websocket from a worker thread goes through IOLoop.add_callback(), the only
  thread-safe IOLoop method.
- Python 2.7 only: no f-strings, no async/await, no type hints, no pathlib.
  The module also imports cleanly under Python 3 + modern Tornado so it can be
  unit-tested off-robot with a fake ``qi`` module.

Usage:
    python pepper_bridge.py [--port=8888] [--api-key=SECRET]
                            [--naoqi=tcp://127.0.0.1:9559] [--tablet-host=198.18.0.1]
"""

import base64
import io
import json
import logging
import math
import signal
import sys
import threading
import time

# NAOqi packages (and, on some images, Tornado) live outside the default Python path on Pepper.
sys.path.insert(0, "/opt/aldebaran/lib/python2.7/site-packages")

import tornado.ioloop  # noqa: E402
import tornado.web  # noqa: E402
import tornado.websocket  # noqa: E402
from tornado.options import define, options  # noqa: E402

import qi  # noqa: E402

try:  # Python 2 / 3 compatibility shims
    text_type = unicode  # noqa: F821
except NameError:  # pragma: no cover - Python 3
    text_type = str

try:
    from PIL import Image as PILImage
except ImportError:  # PIL is optional; the host can convert raw RGB itself.
    PILImage = None

# Tornado 3.x needs @asynchronous to keep a handler open after the method
# returns; Tornado >= 4 also honours a returned Future, and 6.x removed the
# decorator entirely. Support all of them.
_asynchronous = getattr(tornado.web, "asynchronous", None)


def async_handler(method):
    if _asynchronous is not None:
        return _asynchronous(method)
    return method


try:
    from tornado.concurrent import Future
except ImportError:  # pragma: no cover - very old Tornado
    Future = None


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------
define("port", default=8888, type=int, help="HTTP port")
define("api_key", default="", type=str, help="Optional API key for auth")
define("naoqi", default="tcp://127.0.0.1:9559", type=str, help="NAOqi session URL")
define("tablet_host", default="198.18.0.1", type=str, help="IP of the robot head as seen from the tablet")
define("log_level", default="INFO", type=str, help="Logging level")
define("pip", default="", type=str, help="ignored (passed by NAOqi autoload)")
define("pport", default=0, type=int, help="ignored (passed by NAOqi autoload)")

BRIDGE_VERSION = "2.2.0"
LOGGER = logging.getLogger("pepper_bridge")
START_TIME = time.time()
IOLOOP = None  # the main IOLoop, captured in main(); worker threads must only touch this one
POLLER = None  # SensorPoller, so new WebSocket clients get an initial snapshot

# ---------------------------------------------------------------------------
# Pepper ALMemory keys (Pepper != NAO: sonars are Front/Back, not Left/Right)
# ---------------------------------------------------------------------------
TOUCH_KEYS = [
    ("head_front", "Device/SubDeviceList/Head/Touch/Front/Sensor/Value"),
    ("head_middle", "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value"),
    ("head_rear", "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value"),
    ("hand_left", "Device/SubDeviceList/LHand/Touch/Back/Sensor/Value"),
    ("hand_right", "Device/SubDeviceList/RHand/Touch/Back/Sensor/Value"),
]
BUMPER_KEYS = [
    ("front_left", "Device/SubDeviceList/Platform/FrontLeft/Bumper/Sensor/Value"),
    ("front_right", "Device/SubDeviceList/Platform/FrontRight/Bumper/Sensor/Value"),
    ("back", "Device/SubDeviceList/Platform/Back/Bumper/Sensor/Value"),
]
SONAR_KEYS = [
    ("front", "Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value"),
    ("back", "Device/SubDeviceList/Platform/Back/Sonar/Sensor/Value"),
]
BATTERY_KEY = "Device/SubDeviceList/Battery/Charge/Sensor/Value"
BATTERY_CURRENT_KEY = "Device/SubDeviceList/Battery/Current/Sensor/Value"
PEOPLE_KEY = "PeoplePerception/VisiblePeopleList"

OBSTACLE_DISTANCE = 0.45  # metres; sonar reading below this counts as an obstacle

# ISO codes -> NAOqi language names. Full names pass through unchanged.
LANGUAGE_NAMES = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "no": "Norwegian",
    "pl": "Polish",
    "cs": "Czech",
    "tr": "Turkish",
    "el": "Greek",
    "br": "Brazilian",
}

COLOR_MAP = {
    "red": (1, 0, 0),
    "green": (0, 1, 0),
    "blue": (0, 0, 1),
    "yellow": (1, 1, 0),
    "purple": (1, 0, 1),
    "magenta": (1, 0, 1),
    "cyan": (0, 1, 1),
    "white": (1, 1, 1),
    "orange": (1, 0.5, 0),
    "pink": (1, 0.4, 0.7),
    "off": (0, 0, 0),
}
LED_GROUPS = {"eyes": "FaceLeds", "chest": "ChestLeds", "ears": "EarLeds", "shoulders": "ShoulderLeds"}

POSTURES = ("Stand", "StandInit", "StandZero", "Crouch")
LIFE_STATES = ("solitary", "interactive", "safeguard", "disabled")
TRACKING_MODES = ("Head", "BodyRotation", "WholeBody", "MoveContextually")  # ALBasicAwareness.setTrackingMode
ENGAGEMENT_MODES = ("Unengaged", "SemiEngaged", "FullyEngaged")
STIMULI = ("People", "Touch", "TabletTouch", "Sound", "Movement", "NavigationMotion")
DEFAULT_STIMULI = ("People", "Sound", "Touch")  # what turns Pepper's head when /prepare enables awareness

# Pepper head limits in degrees (NAOqi 2.5 joints_pep.html). HeadPitch range shrinks
# as |HeadYaw| grows because the head would hit the casing/tablet.
HEAD_YAW_LIMIT_DEG = 119.5
HEAD_PITCH_LIMITS = [  # (max |yaw| deg, pitch min deg, pitch max deg)
    (33.33, -40.5, 25.5),
    (61.6, -35.2, 20.9),
    (91.4, -35.1, 13.5),
    (119.5, -35.0, 13.5),
]


def head_pitch_limits(yaw_deg):
    """Allowed (min, max) HeadPitch in degrees for a given HeadYaw."""
    a = abs(yaw_deg)
    for max_yaw, low, high in HEAD_PITCH_LIMITS:
        if a <= max_yaw:
            return low, high
    return HEAD_PITCH_LIMITS[-1][1], HEAD_PITCH_LIMITS[-1][2]


# Pepper base velocity limits (m/s and rad/s); NAOqi defaults are 0.35 / 1.0
MAX_VEL_XY = 0.55
MAX_VEL_THETA = 2.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def to_native_str(value):
    """Return a value NAOqi accepts as a string (UTF-8 bytes on Python 2)."""
    if value is None:
        return ""
    if isinstance(value, text_type) and text_type is not str:
        return value.encode("utf-8")
    return value


def as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def clamp(value, low, high):
    return max(low, min(high, value))


def rgb_to_int(r, g, b):
    ri = clamp(int(round(r * 255)), 0, 255)
    gi = clamp(int(round(g * 255)), 0, 255)
    bi = clamp(int(round(b * 255)), 0, 255)
    return (ri << 16) | (gi << 8) | bi


# ---------------------------------------------------------------------------
# NAOqi session with lazy reconnect
# ---------------------------------------------------------------------------


class NaoqiSession(object):
    """Thread-safe holder for the qi.Session and cached service proxies."""

    def __init__(self, url):
        self.url = url
        self._session = None
        self._services = {}
        self._lock = threading.Lock()
        self.on_connect = []  # callables taking the new qi.Session; run on every (re)connect
        self.connecting = False  # True while connect_with_retry() runs in the background

    def connect(self):
        session = qi.Session()
        session.connect(self.url)
        self._session = session
        self._services = {}
        LOGGER.info("Connected to NAOqi at %s", self.url)
        for hook in self.on_connect:
            try:
                hook(session)
            except Exception as exc:
                LOGGER.warning("post-connect hook %s failed: %s", getattr(hook, "__name__", hook), exc)

    def connect_with_retry(self, attempts=40, delay=3.0):
        """NAOqi can take a minute after boot; keep trying before giving up."""
        last_error = None
        self.connecting = True
        try:
            for attempt in range(1, attempts + 1):
                try:
                    self.connect()
                    return True
                except Exception as exc:  # RuntimeError from qi
                    last_error = exc
                    LOGGER.warning("NAOqi not ready (attempt %d/%d): %s", attempt, attempts, exc)
                    time.sleep(delay)
            LOGGER.error("Giving up connecting to NAOqi: %s", last_error)
            return False
        finally:
            self.connecting = False

    def is_connected(self):
        try:
            return self._session is not None and self._session.isConnected()
        except Exception:
            return False

    def service(self, name):
        with self._lock:
            if not self.is_connected():
                if self.connecting:
                    raise RuntimeError("NAOqi is not connected yet (still starting up)")
                LOGGER.warning("NAOqi session lost, reconnecting")
                self.connect()
            svc = self._services.get(name)
            if svc is None:
                svc = self._session.service(name)
                self._services[name] = svc
            return svc


NAOQI = NaoqiSession("tcp://127.0.0.1:9559")


# ---------------------------------------------------------------------------
# Robot facade: every method here blocks and is only ever called on a worker
# thread. Methods return JSON-serialisable dicts or raise.
# ---------------------------------------------------------------------------


class Robot(object):

    EXTRACTORS = ("ALSonar", "ALPeoplePerception")  # publish to ALMemory only while subscribed

    def __init__(self, session):
        self.session = session
        self.tablet_state = {"text": "", "updated": 0.0}
        self._tablet_shown = False
        self.halted = False  # set by emergency_stop; motion is refused until wake_up/prepare
        self.extractors = {}  # service name -> subscribed?
        session.on_connect.append(self.subscribe_extractors)

    # -- extractors --------------------------------------------------------

    def subscribe_extractors(self, session=None):
        """Subscribe to the sensor extractors so their ALMemory keys keep updating.

        Autonomous Life stops them when it is disabled (our default), so the
        bridge holds its own subscription. Runs on every (re)connect.
        """
        for name in self.EXTRACTORS:
            try:
                svc = session.service(name) if session is not None else self.svc(name)
                svc.subscribe("pepper_bridge")
                self.extractors[name] = True
            except Exception as exc:
                LOGGER.warning("could not subscribe to %s: %s", name, exc)
                self.extractors[name] = False

    def check_extractors(self):
        """Re-subscribe if NAOqi forgot us (e.g. after an extractor restart)."""
        for name in self.EXTRACTORS:
            try:
                svc = self.svc(name)
                info = svc.getSubscribersInfo()
                names = [row[0] for row in info] if info else []
                if "pepper_bridge" not in names:
                    svc.subscribe("pepper_bridge")
                self.extractors[name] = True
            except Exception as exc:
                LOGGER.debug("extractor check for %s failed: %s", name, exc)
                self.extractors[name] = False

    def unsubscribe_extractors(self):
        for name in self.EXTRACTORS:
            self._try(lambda: self.svc(name).unsubscribe("pepper_bridge"))

    def svc(self, name):
        return self.session.service(name)

    # -- status / sensors ---------------------------------------------------

    def _try(self, fn, default=None):
        try:
            return fn()
        except Exception as exc:
            LOGGER.debug("ignored NAOqi error: %s", exc)
            return default

    def health(self):
        return {
            "bridge": "pepper_bridge",
            "version": BRIDGE_VERSION,
            "naoqi": self._try(lambda: self.svc("ALSystem").systemVersion(), "unknown"),
            "robot_name": self._try(lambda: self.svc("ALSystem").robotName(), "Pepper"),
            "naoqi_connected": self.session.is_connected(),
            "uptime": round(time.time() - START_TIME, 1),
            "timestamp": time.time(),
            "audio": AUDIO.info(),
        }

    def status(self):
        motion = self.svc("ALMotion")
        tts = self.svc("ALTextToSpeech")
        return {
            "battery": self._try(lambda: self.svc("ALBattery").getBatteryCharge()),
            "posture": self._try(lambda: self.svc("ALRobotPosture").getPosture(), "unknown"),
            "posture_family": self._try(lambda: self.svc("ALRobotPosture").getPostureFamily(), "unknown"),
            "robot_name": self._try(lambda: self.svc("ALSystem").robotName(), "Pepper"),
            "naoqi_version": self._try(lambda: self.svc("ALSystem").systemVersion(), "unknown"),
            "autonomous_life": self._try(lambda: self.svc("ALAutonomousLife").getState(), "unknown"),
            "awake": self._try(lambda: bool(motion.robotIsWakeUp())),
            "language": self._try(lambda: tts.getLanguage(), "unknown"),
            "volume": self._try(lambda: int(round(tts.getVolume() * 100))),
            "awareness": self._try(lambda: bool(self.svc("ALBasicAwareness").isEnabled())),
            "charging": self._charging(),
            "halted": self.halted,
            "bridge_version": BRIDGE_VERSION,
        }

    def _charging(self):
        current = self._try(lambda: self.svc("ALMemory").getData(BATTERY_CURRENT_KEY))
        return (current > 0) if isinstance(current, (int, float)) else None

    def _memory_values(self, keys):
        """Read several ALMemory keys; missing keys come back as None."""
        mem = self.svc("ALMemory")
        try:
            values = mem.getListData(list(keys))
            if len(values) == len(keys):
                return list(values)
        except Exception:
            pass
        out = []
        for key in keys:
            try:
                out.append(mem.getData(key))
            except Exception:
                out.append(None)
        return out

    def sensors(self):
        keys = [v for _, v in TOUCH_KEYS] + [v for _, v in BUMPER_KEYS] + [v for _, v in SONAR_KEYS]
        keys += [BATTERY_KEY, BATTERY_CURRENT_KEY]
        values = self._memory_values(keys)
        touch = {}
        bumpers = {}
        sonar = {}
        idx = 0
        for name, _ in TOUCH_KEYS:
            touch[name] = bool(values[idx]) if values[idx] is not None else False
            idx += 1
        for name, _ in BUMPER_KEYS:
            bumpers[name] = bool(values[idx]) if values[idx] is not None else False
            idx += 1
        for name, _ in SONAR_KEYS:
            v = values[idx]
            sonar[name] = round(float(v), 3) if v is not None else None
            idx += 1
        battery_frac = values[idx]
        current = values[idx + 1]
        battery = int(round(battery_frac * 100)) if battery_frac is not None else None
        charging = (current > 0) if isinstance(current, (int, float)) else None

        people_ids = self._try(lambda: list(self.svc("ALMemory").getData(PEOPLE_KEY)), None)
        obstacle = any(d is not None and d < OBSTACLE_DISTANCE for d in sonar.values())
        return {
            "battery": battery,
            "charging": charging,
            "touch": touch,
            "bumpers": bumpers,
            "sonar": sonar,
            "obstacle": obstacle,
            "people_count": len(people_ids) if people_ids is not None else None,
            "people_ids": people_ids,
            "sonar_ok": bool(self.extractors.get("ALSonar", False)),
            "people_ok": bool(self.extractors.get("ALPeoplePerception", False)),
            "timestamp": time.time(),
        }

    def _obstacle_ahead(self, direction):
        """Return the sonar reading (m) if something is closer than OBSTACLE_DISTANCE in that direction."""
        key = SONAR_KEYS[0][1] if direction >= 0 else SONAR_KEYS[1][1]
        reading = self._try(lambda: self.svc("ALMemory").getData(key))
        if isinstance(reading, (int, float)) and reading < OBSTACLE_DISTANCE:
            return reading
        return None

    # -- speech ---------------------------------------------------------------

    def _ensure_language(self, tts, language):
        """Switch TTS language for one utterance; returns the previous language to restore, or None."""
        if not language:
            return None
        name = LANGUAGE_NAMES.get(str(language).lower(), str(language))
        previous = self._try(tts.getLanguage)
        if previous == name:
            return None
        available = self._try(lambda: list(tts.getAvailableLanguages()), None)
        if available is not None and name not in available:
            raise ValueError("language %r not installed on this robot (available: %s)" % (name, ", ".join(available)))
        tts.setLanguage(name)
        return previous

    def speak(self, text, language=None, animated=True, body_language="contextual"):
        text = to_native_str(text)
        if not text:
            raise ValueError("text is required")
        tts = self.svc("ALTextToSpeech")
        previous_language = self._ensure_language(tts, language)
        started = time.time()
        broadcast_from_thread("speech", {"state": "start", "text": text})
        AUDIO.speaking_begin()
        try:
            if animated:
                anim = self.svc("ALAnimatedSpeech")
                try:
                    anim.say(text, {"bodyLanguageMode": body_language})
                except (TypeError, RuntimeError):
                    anim.say(text)
            else:
                tts.say(text)
        finally:
            AUDIO.speaking_end()
            broadcast_from_thread("speech", {"state": "end", "text": text})
            if previous_language:
                self._try(lambda: tts.setLanguage(previous_language))
        return {
            "spoken": text,
            "duration": round(time.time() - started, 2),
            "animated": bool(animated),
            "language": language or previous_language or None,
        }

    def stop_speaking(self):
        self._try(lambda: self.svc("ALTextToSpeech").stopAll())
        return {}

    def set_volume(self, level):
        level = clamp(as_int(level, 50), 0, 100)
        self.svc("ALTextToSpeech").setVolume(level / 100.0)
        return {"level": level}

    # -- motion ---------------------------------------------------------------

    def _ensure_awake(self, motion):
        """Refuse motion while halted or resting; never wake the robot implicitly."""
        if self.halted:
            raise RuntimeError("robot is halted by emergency stop; POST /wake_up or /prepare first")
        awake = self._try(motion.robotIsWakeUp, True)  # unknown -> proceed
        if not awake:
            raise RuntimeError("robot is resting (motors off); POST /wake_up or /prepare first")

    def _check_obstacle(self, distance, force):
        if force or abs(distance) < 0.05:
            return
        reading = self._obstacle_ahead(distance)
        if reading is not None:
            side = "front" if distance >= 0 else "back"
            raise ValueError(
                "not moving: %s sonar shows an obstacle at %.2f m (pass force=true to override)" % (side, reading)
            )

    @staticmethod
    def _completed(result):
        """moveTo returns False when stopped early (obstacle, stopMove); None from fakes/old APIs."""
        return True if result is None else bool(result)

    def move_forward(self, distance, speed, force=False):
        distance = clamp(as_float(distance, 0.5), -2.0, 2.0)
        speed = clamp(as_float(speed, 0.3), 0.1, MAX_VEL_XY)
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        self._check_obstacle(distance, force)
        done = motion.moveTo(distance, 0.0, 0.0, [["MaxVelXY", speed]])
        return {"distance": distance, "speed": speed, "completed": self._completed(done)}

    def turn(self, angle_deg):
        angle_deg = clamp(as_float(angle_deg, 90), -180.0, 180.0)
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        done = motion.moveTo(0.0, 0.0, math.radians(angle_deg))
        return {"angle": angle_deg, "completed": self._completed(done)}

    def move_to(self, x, y, theta_deg, speed=None, force=False):
        x = clamp(as_float(x, 0), -3.0, 3.0)
        y = clamp(as_float(y, 0), -3.0, 3.0)
        theta = math.radians(clamp(as_float(theta_deg, 0), -180.0, 180.0))
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        self._check_obstacle(x, force)
        if speed is None:
            done = motion.moveTo(x, y, theta)
        else:
            done = motion.moveTo(x, y, theta, [["MaxVelXY", clamp(as_float(speed, 0.3), 0.1, MAX_VEL_XY)]])
        return {"x": x, "y": y, "theta": theta_deg, "completed": self._completed(done)}

    def move_head(self, yaw_deg, pitch_deg, speed):
        yaw_deg = clamp(as_float(yaw_deg, 0), -HEAD_YAW_LIMIT_DEG, HEAD_YAW_LIMIT_DEG)
        pitch_low, pitch_high = head_pitch_limits(yaw_deg)
        pitch_deg = clamp(as_float(pitch_deg, 0), pitch_low, pitch_high)
        speed = clamp(as_float(speed, 0.2), 0.05, 0.6)
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        motion.setStiffnesses("Head", 1.0)
        motion.setAngles(["HeadYaw", "HeadPitch"], [math.radians(yaw_deg), math.radians(pitch_deg)], speed)
        return {"yaw": round(yaw_deg, 1), "pitch": round(pitch_deg, 1)}

    def stop(self):
        """Stop base motion and any running animation (animations are behaviours)."""
        motion = self.svc("ALMotion")
        self._try(lambda: self.svc("ALBehaviorManager").stopAllBehaviors())
        self._try(motion.stopMove)
        self._try(motion.killMove)
        return {}

    def halt(self):
        """Stop everything that moves or talks, without changing stiffness (used on shutdown)."""
        self._try(lambda: self.svc("ALBehaviorManager").stopAllBehaviors())
        self._try(lambda: self.svc("ALTextToSpeech").stopAll())
        motion = self.svc("ALMotion")
        self._try(motion.stopMove)
        self._try(motion.killMove)

    def emergency_stop(self):
        """Kill all behaviours, motion and speech immediately, then rest (motors off).

        Pepper does not allow manual stiffness control of the body (only head and
        arms), so rest() is the supported way to relax the motors. Motion stays
        refused until /wake_up or /prepare clears the halt.
        """
        self.halted = True
        motion = self.svc("ALMotion")
        self._try(lambda: self.svc("ALBehaviorManager").stopAllBehaviors())
        self._try(lambda: self.svc("ALTextToSpeech").stopAll())
        self._try(motion.killMove)
        self._try(motion.killAll)
        motion.rest()
        return {"resting": True, "awake": False, "halted": True}

    def set_posture(self, posture, speed):
        posture = str(posture or "Stand")
        if posture not in POSTURES:
            raise ValueError("unknown posture %r (expected one of %s)" % (posture, ", ".join(POSTURES)))
        speed = clamp(as_float(speed, 0.5), 0.1, 1.0)
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        ok = self.svc("ALRobotPosture").goToPosture(posture, speed)
        return {"posture": posture, "reached": bool(ok)}

    def wake_up(self):
        self.svc("ALMotion").wakeUp()
        self.halted = False
        return {"awake": True, "halted": False}

    def rest(self):
        self.svc("ALMotion").rest()
        return {"awake": False}

    def set_autonomous_life(self, state):
        state = str(state)
        if state not in LIFE_STATES:
            raise ValueError("unknown autonomous life state %r" % state)
        life = self.svc("ALAutonomousLife")
        current = self._try(life.getState)
        if current != state:
            if current == "interactive" and state == "solitary":
                life.setState("disabled")  # NAOqi refuses interactive -> solitary directly
            life.setState(state)
        return {"state": state, "previous": current}

    def set_awareness(self, enabled, tracking_mode=None, engagement_mode=None, stimuli=None):
        """ALBasicAwareness on/off, optionally configuring how it tracks people.

        Only ``Head`` tracking is safe near furniture: ``BodyRotation`` and
        ``MoveContextually`` drive the base. Awareness pauses by itself while
        another activity (our /move/head) uses the head motors and resumes after.
        """
        ba = self.svc("ALBasicAwareness")
        enabled = as_bool(enabled, True)
        result = {"enabled": enabled}
        if enabled:
            if tracking_mode is not None:
                tracking_mode = to_native_str(tracking_mode)
                if tracking_mode not in TRACKING_MODES:
                    raise ValueError(
                        "unknown tracking mode %r (use one of %s)" % (tracking_mode, ", ".join(TRACKING_MODES))
                    )
                ba.setTrackingMode(tracking_mode)
                result["tracking_mode"] = tracking_mode
            if engagement_mode is not None:
                engagement_mode = to_native_str(engagement_mode)
                if engagement_mode not in ENGAGEMENT_MODES:
                    raise ValueError(
                        "unknown engagement mode %r (use one of %s)" % (engagement_mode, ", ".join(ENGAGEMENT_MODES))
                    )
                ba.setEngagementMode(engagement_mode)
                result["engagement_mode"] = engagement_mode
            if not isinstance(stimuli, (list, tuple, set)) and stimuli is not None:
                stimuli = [part.strip() for part in to_native_str(stimuli).split(",") if part.strip()]
            if stimuli:  # an empty list leaves the stimulus configuration alone
                wanted = set(to_native_str(name) for name in stimuli)
                unknown = sorted(wanted - set(STIMULI))
                if unknown:
                    raise ValueError("unknown stimuli %s (use %s)" % (", ".join(unknown), ", ".join(STIMULI)))
                for name in STIMULI:
                    ba.setStimulusDetectionEnabled(name, name in wanted)
                result["stimuli"] = sorted(wanted)
        try:
            ba.setEnabled(enabled)
        except AttributeError:  # older API
            if enabled:
                ba.startAwareness()
            else:
                ba.stopAwareness()
        return result

    def prepare(self, life_state="disabled", wake_up=True, posture=None, awareness=None):
        """Put the robot into a known state for external control.

        Each step is attempted even if an earlier one fails; failures are
        reported in ``errors`` instead of aborting (e.g. setState is refused
        until the robot's setup wizard has been completed).
        """
        result = {"errors": []}

        def step(name, fn):
            try:
                result[name] = fn()
            except Exception as exc:
                LOGGER.warning("prepare: %s failed: %s", name, exc)
                result["errors"].append("%s: %s" % (name, exc))

        if life_state:
            step("autonomous_life", lambda: self.set_autonomous_life(life_state))
        if wake_up:

            def do_wake():
                motion = self.svc("ALMotion")
                if not self._try(motion.robotIsWakeUp, False):
                    motion.wakeUp()
                self.halted = False
                return True

            step("awake", do_wake)
        if posture:
            step("posture", lambda: self.set_posture(posture, 0.6))
        if awareness is not None:
            if as_bool(awareness, False):
                step("awareness", lambda: self.set_awareness(True, tracking_mode="Head", stimuli=DEFAULT_STIMULI))
            else:
                step("awareness", lambda: self.set_awareness(False))
        return result

    # -- camera ---------------------------------------------------------------

    def picture(self, camera, resolution):
        camera = clamp(as_int(camera, 0), 0, 1)
        resolution = clamp(as_int(resolution, 2), 0, 3)  # 0=QQVGA 1=QVGA 2=VGA 3=4VGA
        color_space = 11  # RGB
        video = self.svc("ALVideoDevice")
        handle = video.subscribeCamera(
            "pepper_bridge_%d" % int(time.time() * 1000 % 100000), camera, resolution, color_space, 5
        )
        if not handle:
            raise RuntimeError("ALVideoDevice refused the camera subscription (too many subscribers?)")
        try:
            image = video.getImageRemote(handle)
        finally:
            self._try(lambda: video.unsubscribe(handle))
        if not image:
            raise RuntimeError("camera returned no image")
        width, height, raw = image[0], image[1], image[6]
        raw = bytes(bytearray(raw))
        if PILImage is not None:
            img = PILImage.frombytes("RGB", (width, height), raw)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            data, fmt = buf.getvalue(), "jpeg"
        else:
            data, fmt = raw, "rgb"
        return {
            "image": base64.b64encode(data).decode("ascii"),
            "width": width,
            "height": height,
            "format": fmt,
            "camera": camera,
        }

    # -- LEDs / animation -----------------------------------------------------

    def set_leds(self, group, color=None, r=0, g=0, b=0, duration=0.5):
        group_name = LED_GROUPS.get(group, group)
        if color:
            key = str(color).lower()
            if key not in COLOR_MAP:
                raise ValueError("unknown color %r (known: %s)" % (color, ", ".join(sorted(COLOR_MAP))))
            r, g, b = COLOR_MAP[key]
        rgb = rgb_to_int(as_float(r, 0), as_float(g, 0), as_float(b, 0))
        self.svc("ALLeds").fadeRGB(group_name, rgb, clamp(as_float(duration, 0.5), 0.0, 10.0))
        return {"group": group_name, "rgb": "#%06x" % rgb}

    def play_animation(self, name):
        name = to_native_str(name)
        if not name:
            raise ValueError("name is required")
        motion = self.svc("ALMotion")
        self._ensure_awake(motion)
        started = time.time()
        self.svc("ALAnimationPlayer").run(name)
        return {"animation": name, "duration": round(time.time() - started, 2)}

    def list_animations(self):
        behaviors = self.svc("ALBehaviorManager").getInstalledBehaviors()
        names = sorted(b for b in behaviors if str(b).startswith("animations/"))
        return {"animations": names, "count": len(names)}

    # -- audio ----------------------------------------------------------------

    def record_audio(self, duration):
        duration = clamp(as_float(duration, 3.0), 0.5, 15.0)
        filename = "/tmp/pepper_bridge_recording.wav"
        recorder = self.svc("ALAudioRecorder")
        self._try(recorder.stopMicrophonesRecording)
        recorder.startMicrophonesRecording(filename, "wav", 16000, [0, 0, 1, 0])
        try:
            time.sleep(duration)
        finally:
            recorder.stopMicrophonesRecording()
        with open(filename, "rb") as fh:
            audio = fh.read()
        return {
            "audio": base64.b64encode(audio).decode("ascii"),
            "format": "wav",
            "sample_rate": 16000,
            "channels": 1,
            "duration": duration,
        }

    # -- tablet ---------------------------------------------------------------

    def _tablet_page_url(self):
        return "http://%s:%d/tablet/page" % (options.tablet_host, options.port)

    def tablet_text(self, text, title=None):
        self.tablet_state = {"text": to_native_str(text), "title": to_native_str(title or ""), "updated": time.time()}
        tablet = self.svc("ALTabletService")
        if not self._tablet_shown:
            tablet.showWebview(self._tablet_page_url())
            self._tablet_shown = True
        return {"shown": True}

    def tablet_web(self, url):
        if not url:
            raise ValueError("url is required")
        self.svc("ALTabletService").showWebview(to_native_str(url))
        self._tablet_shown = False
        return {"url": url}

    def tablet_image(self, url):
        if not url:
            raise ValueError("url is required")
        self.svc("ALTabletService").showImage(to_native_str(url))
        self._tablet_shown = False
        return {"url": url}

    def tablet_hide(self):
        tablet = self.svc("ALTabletService")
        self._try(tablet.hideWebview)
        self._try(tablet.hideImage)
        self._tablet_shown = False
        return {}


# ---------------------------------------------------------------------------
# Microphone streaming. ALAudioDevice pushes 16 kHz mono PCM into
# processRemote() on a NAOqi thread; frames are forwarded to /ws/audio clients
# as binary WebSocket messages. Capture runs only while a client is connected
# and is muted while the robot itself speaks (Pepper has no echo cancellation).
# ---------------------------------------------------------------------------

AUDIO_SERVICE_NAME = "PepperBridgeAudio"
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNEL = 3  # ALAudioDevice channel: 0=all four (48 kHz only), 1=left, 2=right, 3=front, 4=rear
AUDIO_MUTE_TAIL = 0.4  # seconds of capture still dropped after speech ends (room reverb, TTS tail)
TTS_EVENT_TIMEOUT = 60.0  # a TTS "started" event older than this without a "done" no longer mutes
AUDIO_MAX_BEHIND = 30  # frames (~5 s) a slow client may lag before it is disconnected


class _AudioCallback(object):
    """The object registered with qi: only processRemote is exposed to NAOqi."""

    def __init__(self, tap):
        self._tap = tap

    def processRemote(self, nbOfChannels, nbOfSamplesByChannel, timeStamp, buffer):
        self._tap.on_buffer(nbOfChannels, nbOfSamplesByChannel, timeStamp, buffer)


class AudioTap(object):
    """Owns the ALAudioDevice subscription and the speaking/muted state."""

    def __init__(self, session):
        self.session = session
        self.qi_session = None
        self.service_id = None
        self.subscribed = False
        self.speaking = 0  # nesting count of /speak calls in progress
        self.tts_active = False  # ALTextToSpeech/Status says a sentence is being said (any source)
        self.muted_until = 0.0
        self.host_muted = False  # set by the host over /ws/audio ({"type": "mute", "muted": true})
        self.frames = 0
        self.dropped = 0
        self.last_frame_at = 0.0
        self.tts_started_at = 0.0
        self._callback = _AudioCallback(self)
        self._tts_subscriber = None
        self._lock = threading.Lock()  # subscribe/unsubscribe
        self._mute_lock = threading.Lock()  # the speaking counter
        session.on_connect.append(self.register)

    # -- NAOqi side (worker threads) -----------------------------------------

    def register(self, session):
        """Expose the callback as a qi service so ALAudioDevice can reach it (runs on every (re)connect).

        A new NAOqi session has forgotten our subscription and any speech that was in
        progress, so the mute state is reset and capture is restarted for connected clients.
        """
        self.subscribed = False
        self.tts_active = False
        self.host_muted = False
        self.speaking = 0
        self.qi_session = session
        try:
            self.service_id = session.registerService(AUDIO_SERVICE_NAME, self._callback)
        except Exception as exc:
            LOGGER.warning("could not register the audio service: %s", exc)
            self.service_id = None
            return
        try:  # speech from any source (not only /speak) mutes capture
            subscriber = session.service("ALMemory").subscriber("ALTextToSpeech/Status")
            subscriber.signal.connect(self._on_tts_status)
            self._tts_subscriber = subscriber  # keep a reference: the subscription dies with the object
        except Exception as exc:
            LOGGER.debug("ALTextToSpeech/Status events unavailable: %s", exc)
        if AudioWebSocket.clients:
            # Not inline: this hook may run under NaoqiSession's lock, which start() needs too.
            _run_in_thread(AudioWebSocket.start_capture)

    def start(self):
        """Subscribe to ALAudioDevice (front microphone, 16 kHz) while a client is connected. Idempotent."""
        with self._lock:
            if self.subscribed or not AudioWebSocket.clients:
                return self.info()
            if self.service_id is None:
                raise RuntimeError("audio service is not registered (NAOqi not connected?)")
            audio = self.session.service("ALAudioDevice")
            audio.setClientPreferences(AUDIO_SERVICE_NAME, AUDIO_SAMPLE_RATE, AUDIO_CHANNEL, 0)
            audio.subscribe(AUDIO_SERVICE_NAME)
            self.subscribed = True
            LOGGER.info("Microphone streaming started")
            return self.info()

    def stop(self):
        """Unsubscribe unless a client is still connected. Idempotent."""
        with self._lock:
            if not self.subscribed or AudioWebSocket.clients:
                return self.info()
            self.subscribed = False
            try:
                self.session.service("ALAudioDevice").unsubscribe(AUDIO_SERVICE_NAME)
            except Exception as exc:
                LOGGER.warning("audio unsubscribe failed: %s", exc)
            LOGGER.info("Microphone streaming stopped")
            return self.info()

    def unregister(self):
        """Shutdown: unsubscribe and drop the qi service so a restart can register the same name."""
        AudioWebSocket.clients.clear()
        self.stop()
        if self.service_id is not None and self.qi_session is not None:
            try:
                self.qi_session.unregisterService(self.service_id)
            except Exception as exc:
                LOGGER.debug("unregisterService failed: %s", exc)
        self.service_id = None

    # -- mute bookkeeping (called from /speak worker threads and qi event threads) ----

    def speaking_begin(self):
        with self._mute_lock:
            self.speaking += 1

    def speaking_end(self):
        with self._mute_lock:
            self.speaking = max(0, self.speaking - 1)
            self.muted_until = time.time() + AUDIO_MUTE_TAIL

    def _on_tts_status(self, value):
        try:
            status = value[1]
        except (TypeError, IndexError, KeyError):
            return
        if status == "started":
            self.tts_active = True
            self.tts_started_at = time.time()
        elif status in ("done", "stopped", "thrown"):
            self.tts_active = False
            self.muted_until = time.time() + AUDIO_MUTE_TAIL

    def muted(self):
        if self.tts_active and time.time() - self.tts_started_at > TTS_EVENT_TIMEOUT:
            self.tts_active = False  # a "started" whose "done" never came (NAOqi hiccup) must not mute forever
        return self.speaking > 0 or self.tts_active or self.host_muted or time.time() < self.muted_until

    def info(self):
        return {
            "streaming": self.subscribed,
            "clients": len(AudioWebSocket.clients),
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": 1,
            "format": "pcm_s16le",
            "muted": self.muted(),
            "frames": self.frames,
            "dropped": self.dropped,
            "last_frame_age": round(time.time() - self.last_frame_at, 2) if self.last_frame_at else None,
        }

    # -- called by NAOqi on its own thread; must return quickly ---------------

    def on_buffer(self, nb_channels, nb_samples, timestamp, buffer):
        try:
            self.frames += 1
            self.last_frame_at = time.time()
            if not self.subscribed or not AudioWebSocket.clients:
                return
            if self.muted():
                self.dropped += 1
                return
            try:
                data = bytes(buffer)
            except Exception:
                data = str(buffer)
            main_ioloop().add_callback(AudioWebSocket.broadcast, data)
        except Exception as exc:  # never let an error escape into libqi's thread
            self.dropped += 1
            LOGGER.debug("audio frame dropped: %s", exc)


ROBOT = Robot(NAOQI)
AUDIO = AudioTap(NAOQI)


# ---------------------------------------------------------------------------
# Base handlers
# ---------------------------------------------------------------------------


class JSONHandler(tornado.web.RequestHandler):
    """Parses a JSON body, checks the API key and runs NAOqi work off-loop."""

    def check_auth(self):
        if not options.api_key:
            return True
        key = self.request.headers.get("X-API-Key", "") or self.get_argument("api_key", "")
        if key != options.api_key:
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"ok": False, "error": "unauthorized"}))
            return False
        return True

    def prepare(self):
        if not self.check_auth():
            self.finish()
            return
        self.json_body = {}
        if self.request.body:
            try:
                body = self.request.body
                if isinstance(body, bytes) and not isinstance(body, str):
                    body = body.decode("utf-8")
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    self.json_body = parsed
            except (ValueError, TypeError):
                pass

    def arg(self, name, default=None):
        """Read a parameter from the JSON body, falling back to the query string."""
        if name in self.json_body:
            return self.json_body[name]
        return self.get_argument(name, default)

    def ok(self, data=None):
        resp = {"ok": True}
        if data:
            resp.update(data)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(resp))

    def fail(self, message, status=400):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"ok": False, "error": message}))

    def run_in_thread(self, fn, *args, **kwargs):
        """Run fn(*args) on a worker thread and answer from the IOLoop."""
        loop = main_ioloop()
        future = Future() if Future is not None else None

        def work():
            try:
                result = fn(*args, **kwargs)
                loop.add_callback(self._respond, result, None, future)
            except Exception as exc:
                LOGGER.warning("%s %s failed: %s", self.request.method, self.request.path, exc)
                loop.add_callback(self._respond, None, "%s" % exc, future)

        thread = threading.Thread(target=work)
        thread.daemon = True
        thread.start()
        return future

    def _respond(self, result, error, future):
        try:
            if not self._finished:
                if error is not None:
                    self.fail(error, 500)
                else:
                    self.ok(result)
                self.finish()
        finally:
            if future is not None and not future.done():
                future.set_result(None)


# ---------------------------------------------------------------------------
# Endpoint handlers (thin: parse args, delegate to ROBOT on a thread)
# ---------------------------------------------------------------------------


class HealthHandler(JSONHandler):
    @async_handler
    def get(self):
        if not NAOQI.is_connected():
            # Answer without touching NAOqi so deploy can tell "booting" from "dead".
            self.set_status(503)
            self.set_header("Content-Type", "application/json")
            self.write(
                json.dumps(
                    {
                        "ok": False,
                        "error": "naoqi_connecting",
                        "naoqi_connected": False,
                        "bridge": "pepper_bridge",
                        "version": BRIDGE_VERSION,
                        "uptime": round(time.time() - START_TIME, 1),
                    }
                )
            )
            return self.finish()
        return self.run_in_thread(ROBOT.health)


class StatusHandler(JSONHandler):
    @async_handler
    def get(self):
        return self.run_in_thread(ROBOT.status)


class SensorsHandler(JSONHandler):
    @async_handler
    def get(self):
        return self.run_in_thread(ROBOT.sensors)


class SpeakHandler(JSONHandler):
    @async_handler
    def post(self):
        text = self.arg("text", "")
        if not text:
            self.fail("text is required")
            return self.finish()
        animated = as_bool(self.arg("animated"), True)
        body_language = self.arg("body_language", "contextual")
        language = self.arg("language")
        if as_bool(self.arg("wait"), True):
            return self.run_in_thread(ROBOT.speak, text, language, animated, body_language)
        # Fire and forget: speak on a thread, answer immediately.
        thread = threading.Thread(target=lambda: _safe(ROBOT.speak, text, language, animated, body_language))
        thread.daemon = True
        thread.start()
        self.ok({"queued": True})
        return self.finish()


class StopSpeakingHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.stop_speaking)


class VolumeHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.set_volume, self.arg("level", 50))


class MoveForwardHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(
            ROBOT.move_forward, self.arg("distance", 0.5), self.arg("speed", 0.3), as_bool(self.arg("force"), False)
        )


class MoveTurnHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.turn, self.arg("angle", 90))


class MoveHeadHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.move_head, self.arg("yaw", 0), self.arg("pitch", 0), self.arg("speed", 0.2))


class MoveToHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(
            ROBOT.move_to,
            self.arg("x", 0),
            self.arg("y", 0),
            self.arg("theta", 0),
            self.arg("speed"),
            as_bool(self.arg("force"), False),
        )


class StopHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.stop)


class EmergencyStopHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.emergency_stop)


class PostureHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.set_posture, self.arg("posture", "Stand"), self.arg("speed", 0.5))


class WakeUpHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.wake_up)


class RestHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.rest)


class PrepareHandler(JSONHandler):
    @async_handler
    def post(self):
        awareness = self.arg("awareness")
        return self.run_in_thread(
            ROBOT.prepare,
            self.arg("autonomous_life", "disabled") or None,
            as_bool(self.arg("wake_up"), True),
            self.arg("posture"),
            None if awareness is None else as_bool(awareness),
        )


class PictureHandler(JSONHandler):
    @async_handler
    def get(self):
        return self.run_in_thread(ROBOT.picture, self.get_argument("camera", "0"), self.get_argument("resolution", "2"))


class LEDHandler(JSONHandler):
    group = "eyes"

    @async_handler
    def post(self):
        return self.run_in_thread(
            ROBOT.set_leds,
            self.group,
            self.arg("color"),
            self.arg("r", 0),
            self.arg("g", 0),
            self.arg("b", 0),
            self.arg("duration", 0.5),
        )


class LEDEyesHandler(LEDHandler):
    group = "eyes"


class LEDChestHandler(LEDHandler):
    group = "chest"


class AnimationHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.play_animation, self.arg("name", "") or self.arg("animation", ""))


class AnimationListHandler(JSONHandler):
    @async_handler
    def get(self):
        return self.run_in_thread(ROBOT.list_animations)


class AwarenessHandler(JSONHandler):
    @async_handler
    def post(self):
        stimuli = self.json_body.get("stimuli", self.get_arguments("stimuli") or None)
        return self.run_in_thread(
            ROBOT.set_awareness,
            self.arg("enabled", True),
            self.arg("tracking_mode"),
            self.arg("engagement_mode"),
            stimuli,
        )


class AutonomousLifeHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.set_autonomous_life, self.arg("state", "solitary"))


class AudioRecordHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.record_audio, self.arg("duration", 3.0))


class AudioStreamHandler(JSONHandler):
    """State of the microphone stream (see /ws/audio)."""

    def get(self):
        self.ok(AUDIO.info())


class TabletTextHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.tablet_text, self.arg("text", ""), self.arg("title"))


class TabletWebHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.tablet_web, self.arg("url", ""))


class TabletImageHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.tablet_image, self.arg("url", ""))


class TabletHideHandler(JSONHandler):
    @async_handler
    def post(self):
        return self.run_in_thread(ROBOT.tablet_hide)


TABLET_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Pepper</title>
<style>
html,body{margin:0;height:100%;background:#0b1e3f;color:#fff;font-family:Helvetica,Arial,sans-serif;}
#wrap{display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:40px;box-sizing:border-box;text-align:center;}
#title{font-size:28px;opacity:.7;margin-bottom:20px;}
#text{font-size:44px;line-height:1.3;}
</style></head><body><div id="wrap"><div id="title"></div><div id="text"></div></div>
<script>
var last=0;
function poll(){
  var x=new XMLHttpRequest();
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      try{var s=JSON.parse(x.responseText);
        if(s.updated!==last){last=s.updated;
          document.getElementById('title').textContent=s.title||'';
          document.getElementById('text').textContent=s.text||'';}
      }catch(e){}
    }
  };
  x.open('GET','/tablet/state?ts='+Date.now(),true);x.send();
}
setInterval(poll,500);poll();
</script></body></html>"""


class TabletPageHandler(tornado.web.RequestHandler):
    """Served to the tablet's browser; intentionally unauthenticated."""

    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.write(TABLET_PAGE)


class TabletStateHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        state = dict(ROBOT.tablet_state)
        for key in ("text", "title"):
            value = state.get(key, "")
            if isinstance(value, bytes) and not isinstance(value, str):
                state[key] = value.decode("utf-8", "replace")
            elif not isinstance(value, text_type) and text_type is not str:
                state[key] = value.decode("utf-8", "replace")
        self.write(json.dumps(state))


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        LOGGER.warning("background %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None


# ---------------------------------------------------------------------------
# WebSocket event push
# ---------------------------------------------------------------------------


class EventWebSocket(tornado.websocket.WebSocketHandler):
    """Push robot events (touch, bumper, sonar, battery, people, speech)."""

    clients = set()

    def check_origin(self, origin):
        return True

    def open(self):
        if options.api_key:
            key = self.get_argument("api_key", "") or self.request.headers.get("X-API-Key", "")
            if key != options.api_key:
                LOGGER.warning("WS client rejected: bad api key")
                try:
                    self.write_message(
                        json.dumps({"type": "error", "data": {"error": "unauthorized"}, "timestamp": time.time()})
                    )
                except Exception:
                    pass
                self.close()
                return
        EventWebSocket.clients.add(self)
        LOGGER.info("WS client connected (%d total)", len(EventWebSocket.clients))
        try:
            self.write_message(
                json.dumps({"type": "hello", "data": {"version": BRIDGE_VERSION}, "timestamp": time.time()})
            )
            if POLLER is not None and POLLER.last is not None:
                self.write_message(json.dumps({"type": "sensors", "data": POLLER.last, "timestamp": time.time()}))
        except Exception:
            pass

    def on_close(self):
        EventWebSocket.clients.discard(self)
        LOGGER.info("WS client disconnected (%d total)", len(EventWebSocket.clients))

    def on_message(self, message):
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict) and data.get("type") == "ping":
            try:
                self.write_message(json.dumps({"type": "pong", "timestamp": time.time()}))
            except Exception:
                pass

    @classmethod
    def broadcast(cls, event_type, payload):
        """Must be called on the IOLoop thread (use broadcast_from_thread elsewhere)."""
        if not cls.clients:
            return
        msg = json.dumps({"type": event_type, "data": payload, "timestamp": time.time()})
        dead = []
        for client in list(cls.clients):
            try:
                client.write_message(msg)
            except Exception:
                dead.append(client)
        for client in dead:
            cls.clients.discard(client)


class AudioWebSocket(tornado.websocket.WebSocketHandler):
    """Microphone stream: a JSON ``hello`` text frame, then binary frames of 16-bit PCM."""

    clients = set()

    def check_origin(self, origin):
        return True

    def open(self):
        if options.api_key:
            key = self.get_argument("api_key", "") or self.request.headers.get("X-API-Key", "")
            if key != options.api_key:
                LOGGER.warning("audio WS client rejected: bad api key")
                self.send_json({"type": "error", "error": "unauthorized"})
                self.close()
                return
        AudioWebSocket.clients.add(self)
        LOGGER.info("audio WS client connected (%d total)", len(AudioWebSocket.clients))
        self.behind = 0  # frames queued while the socket was still writing the previous ones
        self.send_json(
            {
                "type": "hello",
                "version": BRIDGE_VERSION,
                "sample_rate": AUDIO_SAMPLE_RATE,
                "channels": 1,
                "format": "pcm_s16le",
            }
        )
        _run_in_thread(AudioWebSocket.start_capture)

    @staticmethod
    def start_capture():
        """Worker thread: subscribe and tell the clients; on failure drop them so they reconnect and retry."""
        try:
            info = AUDIO.start()
            if info["streaming"]:
                main_ioloop().add_callback(AudioWebSocket.broadcast_json, {"type": "state", "streaming": True})
        except Exception as exc:
            LOGGER.warning("microphone streaming failed to start: %s", exc)
            main_ioloop().add_callback(AudioWebSocket.close_all, "%s" % exc)

    def on_close(self):
        AudioWebSocket.clients.discard(self)
        LOGGER.info("audio WS client disconnected (%d total)", len(AudioWebSocket.clients))
        if not AudioWebSocket.clients:
            AUDIO.host_muted = False  # a host-side mute must not outlive the host
            _run_in_thread(AUDIO.stop)

    def on_message(self, message):
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if data.get("type") == "ping":
            self.send_json({"type": "pong", "timestamp": time.time()})
        elif data.get("type") == "mute":
            AUDIO.host_muted = bool(data.get("muted", True))
            self.send_json({"type": "state", "streaming": AUDIO.subscribed, "muted": AUDIO.muted()})

    def send_json(self, payload):
        try:
            self.write_message(json.dumps(payload))
        except Exception:
            pass

    def _still_writing(self):
        """True when the previous frames have not left the socket yet (slow or stalled host)."""
        try:
            return self.ws_connection.stream.writing()
        except Exception:
            return False

    @classmethod
    def broadcast(cls, data):
        """Send one binary PCM frame to every client (IOLoop thread only).

        Tornado 3.1 has no write-buffer limit, so a host that stops reading would make
        the robot buffer audio without bound; clients that fall AUDIO_MAX_BEHIND frames
        behind are disconnected instead (they reconnect and get a fresh stream).
        """
        dead = []
        for client in list(cls.clients):
            if client._still_writing():
                client.behind += 1
                if client.behind > AUDIO_MAX_BEHIND:
                    LOGGER.warning("audio WS client too slow (%d frames behind); disconnecting", client.behind)
                    dead.append(client)
                    continue
            else:
                client.behind = 0
            try:
                client.write_message(data, binary=True)
            except Exception:
                dead.append(client)
        for client in dead:
            cls.clients.discard(client)
            try:
                client.close()
            except Exception:
                pass

    @classmethod
    def broadcast_json(cls, payload):
        for client in list(cls.clients):
            client.send_json(payload)

    @classmethod
    def close_all(cls, error):
        """IOLoop thread: report an error to every client and disconnect them."""
        for client in list(cls.clients):
            client.send_json({"type": "error", "error": error})
            cls.clients.discard(client)
            try:
                client.close()
            except Exception:
                pass


def _run_in_thread(fn, *args):
    """Fire-and-forget NAOqi work from the IOLoop thread (errors are logged, never raised)."""
    thread = threading.Thread(target=_safe, args=(fn,) + args)
    thread.daemon = True
    thread.start()


def main_ioloop():
    """The IOLoop that serves requests (safe to reference from any thread)."""
    return IOLOOP if IOLOOP is not None else tornado.ioloop.IOLoop.instance()


def broadcast_from_thread(event_type, payload):
    """Thread-safe broadcast: hop onto the main IOLoop first."""
    try:
        main_ioloop().add_callback(EventWebSocket.broadcast, event_type, payload)
    except Exception as exc:
        LOGGER.debug("broadcast failed: %s", exc)


# ---------------------------------------------------------------------------
# Sensor poller (worker thread) -> edge-triggered events
# ---------------------------------------------------------------------------


class SensorPoller(object):
    POLL_INTERVAL = 0.25

    def __init__(self, robot):
        self.robot = robot
        self._running = False
        self._thread = None
        self.last = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False

    EXTRACTOR_CHECK_EVERY = 120  # polls (~30 s)

    def _run(self):
        polls = 0
        while self._running:
            try:
                snapshot = self.robot.sensors()
                self._diff(self.last, snapshot)
                self.last = snapshot
                polls += 1
                if polls % self.EXTRACTOR_CHECK_EVERY == 0:
                    self.robot.check_extractors()
            except Exception as exc:
                LOGGER.warning("sensor poll failed: %s", exc)
                time.sleep(2.0)
            time.sleep(self.POLL_INTERVAL)

    def _diff(self, prev, now):
        prev = prev or {}
        for name, touched in now["touch"].items():
            if touched != prev.get("touch", {}).get(name, False):
                broadcast_from_thread("touch", {"sensor": name, "touched": touched})
        for name, pressed in now["bumpers"].items():
            if pressed != prev.get("bumpers", {}).get(name, False):
                broadcast_from_thread("bumper", {"sensor": name, "pressed": pressed})
        if now["obstacle"] != prev.get("obstacle", False):
            payload = dict(now["sonar"])
            payload["obstacle"] = now["obstacle"]
            broadcast_from_thread("sonar", payload)
        if now["battery"] != prev.get("battery") or now["charging"] != prev.get("charging"):
            broadcast_from_thread("battery", {"level": now["battery"], "charging": now["charging"]})
        if now["people_count"] != prev.get("people_count"):
            broadcast_from_thread("people", {"count": now["people_count"], "ids": now["people_ids"]})


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def make_app():
    return tornado.web.Application(
        [
            (r"/health", HealthHandler),
            (r"/status", StatusHandler),
            (r"/sensors", SensorsHandler),
            (r"/speak", SpeakHandler),
            (r"/speak/stop", StopSpeakingHandler),
            (r"/volume", VolumeHandler),
            (r"/move/forward", MoveForwardHandler),
            (r"/move/turn", MoveTurnHandler),
            (r"/move/head", MoveHeadHandler),
            (r"/move/to", MoveToHandler),
            (r"/stop", StopHandler),
            (r"/emergency_stop", EmergencyStopHandler),
            (r"/posture", PostureHandler),
            (r"/wake_up", WakeUpHandler),
            (r"/rest", RestHandler),
            (r"/prepare", PrepareHandler),
            (r"/picture", PictureHandler),
            (r"/leds/eyes", LEDEyesHandler),
            (r"/leds/chest", LEDChestHandler),
            (r"/animation", AnimationHandler),
            (r"/animations", AnimationListHandler),
            (r"/awareness", AwarenessHandler),
            (r"/autonomous_life", AutonomousLifeHandler),
            (r"/audio/record", AudioRecordHandler),
            (r"/audio/stream", AudioStreamHandler),
            (r"/tablet/text", TabletTextHandler),
            (r"/tablet/web", TabletWebHandler),
            (r"/tablet/image", TabletImageHandler),
            (r"/tablet/hide", TabletHideHandler),
            (r"/tablet/page", TabletPageHandler),
            (r"/tablet/state", TabletStateHandler),
            (r"/ws/events", EventWebSocket),
            (r"/ws/audio", AudioWebSocket),
        ]
    )


def _connect_naoqi_in_background():
    """Connect to NAOqi with retries; start the sensor poller once connected."""
    if NAOQI.connect_with_retry():
        try:
            LOGGER.info("Robot: %s, NAOqi %s", ROBOT.svc("ALSystem").robotName(), ROBOT.svc("ALSystem").systemVersion())
        except Exception as exc:
            LOGGER.warning("Could not read robot identity: %s", exc)
        POLLER.start()
    else:
        LOGGER.error("NAOqi never came up; exiting")
        IOLOOP.add_callback(IOLOOP.stop)


def _on_signal(signum, frame):
    """SIGTERM/SIGINT: halt the robot before the process disappears (deploy restarts do this)."""
    LOGGER.info("Signal %d received: halting robot and shutting down", signum)
    if NAOQI.is_connected():
        _safe(ROBOT.halt)  # quick and safety-critical; the audio service is dropped after the loop stops
    if POLLER is not None:
        POLLER.stop()
    try:
        IOLOOP.add_callback_from_signal(IOLOOP.stop)
    except AttributeError:  # very old Tornado
        IOLOOP.add_callback(IOLOOP.stop)


def main():
    tornado.options.parse_command_line()  # also configures logging (tornado's pretty logging)
    logging.getLogger().setLevel(getattr(logging, options.log_level.upper(), logging.INFO))
    LOGGER.info("Pepper Bridge %s starting on port %d", BRIDGE_VERSION, options.port)

    global IOLOOP, POLLER
    IOLOOP = tornado.ioloop.IOLoop.instance()
    POLLER = SensorPoller(ROBOT)
    NAOQI.url = options.naoqi

    # Serve /health immediately so a deploy can watch NAOqi come up; everything else
    # answers 500 "not connected yet" until the background connect succeeds.
    app = make_app()
    app.listen(options.port)
    LOGGER.info(
        "Bridge listening on http://0.0.0.0:%d (events: ws://0.0.0.0:%d/ws/events, "
        "microphone: ws://0.0.0.0:%d/ws/audio)",
        options.port,
        options.port,
        options.port,
    )

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    connector = threading.Thread(target=_connect_naoqi_in_background)
    connector.daemon = True
    connector.start()

    IOLOOP.start()
    LOGGER.info("Shutting down")
    POLLER.stop()
    if NAOQI.is_connected():
        _safe(AUDIO.unregister)
        _safe(ROBOT.unsubscribe_extractors)


if __name__ == "__main__":
    main()
