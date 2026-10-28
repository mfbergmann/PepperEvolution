# -*- coding: utf-8 -*-
"""
A tiny stand-in for NAOqi's ``qi`` module so the bridge can run off-robot.

Python 2.7 and 3 compatible. Put ``tests/fakenaoqi`` on PYTHONPATH and start
``robot_bridge/pepper_bridge.py`` as usual; every service call is logged and
answered with plausible data. The front head touch sensor toggles every
second so the sensor poller has something to report.
"""

import logging
import math
import struct
import threading
import time

LOG = logging.getLogger("fake_qi")

_MEMORY = {
    "Device/SubDeviceList/Head/Touch/Front/Sensor/Value": 0.0,
    "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value": 0.0,
    "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value": 0.0,
    "Device/SubDeviceList/LHand/Touch/Back/Sensor/Value": 0.0,
    "Device/SubDeviceList/RHand/Touch/Back/Sensor/Value": 0.0,
    "Device/SubDeviceList/Platform/FrontLeft/Bumper/Sensor/Value": 0.0,
    "Device/SubDeviceList/Platform/FrontRight/Bumper/Sensor/Value": 0.0,
    "Device/SubDeviceList/Platform/Back/Bumper/Sensor/Value": 0.0,
    "Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value": 1.2,
    "Device/SubDeviceList/Platform/Back/Sonar/Sensor/Value": 2.0,
    "Device/SubDeviceList/Battery/Charge/Sensor/Value": 0.81,
    "Device/SubDeviceList/Battery/Current/Sensor/Value": -0.4,
    "PeoplePerception/VisiblePeopleList": [7],
    "PeoplePerception/Person/7/Distance": 1.2,
    "PeoplePerception/Person/7/IsLookingAtRobot": 1,
    "PeoplePerception/Person/7/EngagementZone": 1,
    "PeoplePerception/Person/7/PresentSince": 30,
}

_STATE = {
    "language": "English",
    "awake": False,
    "life": "solitary",
    "volume": 0.5,
    "awareness": True,
    "pose": [0.0, 0.0, 0.0],  # odometry (FRAME_WORLD): moveTo moves it
}
# ALNavigation's exploration API (NAOqi 2.5 docs): the map frame is the odometry frame shifted by "offset", fixed by
# relocalizeInMap, so moveTo moves Pepper in the map too
_MAP = {"loaded": None, "localizing": False, "offset": [0.0, 0.0, 0.0], "saved": 0}
_SUBSCRIBERS = {}  # service -> set of subscriber names
_SERVICES = {}  # name -> object registered with Session.registerService
_AUDIO_PUMPS = {}  # subscriber name -> _AudioPump thread (ALAudioDevice.subscribe)
_AUDIO_PREFS = {}  # subscriber name -> (sample rate, channel) from setClientPreferences (channel 0: all four at 48 kHz)

AUDIO_FRAME_SAMPLES = 1365  # 85 ms at 16 kHz, measured on the robot (NAOqi 2.5.10)
AUDIO_FRAME_PERIOD = 0.085


_TONES = {}  # (rate, channels) -> one second of the interleaved tone (440 whole cycles), computed once


def _tone_frame(n, phase, rate=16000, channels=1):
    """n samples (per channel) of a 440 Hz tone as interleaved 16-bit little-endian PCM, like ALAudioDevice's buffers;
    channel c of a four-channel frame is scaled by (c + 1) so tests can tell the microphones apart."""
    key = (rate, channels)
    if key not in _TONES:
        second = []
        for i in range(rate):
            value = 3000 * math.sin(2 * math.pi * 440 * i / float(rate))
            second.extend(int(value * (c + 1) / channels) for c in range(channels))
        _TONES[key] = second
    tone = _TONES[key]
    start = (phase % rate) * channels
    samples = (
        (tone[start:] + tone)[: n * channels]
        if start + n * channels > len(tone)
        else tone[start : start + n * channels]
    )
    while len(samples) < n * channels:  # a frame longer than a second (not used)
        samples = samples + tone[: n * channels - len(samples)]
    return struct.pack("<%dh" % len(samples), *samples)


class _AudioPump(object):
    """Calls the registered service's processRemote every 170 ms, like ALAudioDevice does."""

    def __init__(self, subscriber):
        self.subscriber = subscriber
        self.running = True
        self.phase = 0
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()

    def _run(self):
        while self.running:
            time.sleep(AUDIO_FRAME_PERIOD)
            target = _SERVICES.get(self.subscriber)
            if target is None or not self.running:
                continue
            now = time.time()
            stamp = [int(now), int((now - int(now)) * 1e6)]
            rate, channel = _AUDIO_PREFS.get(self.subscriber, (16000, 3))
            channels = 4 if channel == 0 else 1
            n = AUDIO_FRAME_SAMPLES * rate // 16000
            try:
                target.processRemote(channels, n, stamp, _tone_frame(n, self.phase, rate, channels))
            except Exception as exc:  # the real ALAudioDevice would just drop the frame
                LOG.warning("processRemote failed: %s", exc)
            self.phase += n

    def stop(self):
        self.running = False


class _Future(object):
    """What a qi call with _async=True returns (already finished here)."""

    def __init__(self, value):
        self.value_ = value

    def wait(self, timeout_ms=None):
        return None

    def isFinished(self):
        return True

    def hasError(self):
        return False

    def error(self):
        return ""

    def value(self):
        return self.value_

    def cancel(self):
        return None


class _Signal(object):
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)
        return len(self.callbacks)

    def disconnect(self, link_id):
        return True


class _Subscriber(object):
    """What ALMemory.subscriber(key) returns: an object with a ``signal``."""

    def __init__(self, key):
        self.key = key
        self.signal = _Signal()


def _toggle_touch():
    while True:
        time.sleep(1.0)
        key = "Device/SubDeviceList/Head/Touch/Front/Sensor/Value"
        _MEMORY[key] = 0.0 if _MEMORY[key] else 1.0


_thread = threading.Thread(target=_toggle_touch)
_thread.daemon = True
_thread.start()


class _Service(object):
    def __init__(self, name):
        self.name = name

    def __getattr__(self, method):
        def call(*args, **kwargs):
            LOG.info("%s.%s%s", self.name, method, args)
            if kwargs.get("_async"):
                return _Future(self._respond(method, args))
            return self._respond(method, args)

        return call

    def _respond(self, method, args):
        if self.name == "ALNavigation":
            return _navigation(method, args)
        if method == "getData":
            return _MEMORY[args[0]]
        if method == "getListData":
            return [_MEMORY.get(k) for k in args[0]]
        if method == "getLanguage":
            return _STATE["language"]
        if method == "setLanguage":
            _STATE["language"] = args[0]
            return None
        if method == "getAvailableLanguages":
            return ["English", "French"]
        if method == "say":
            time.sleep(0.05 * max(1, len(str(args[0]).split())))
            return None
        if method == "robotIsWakeUp":
            return _STATE["awake"]
        if method == "wakeUp":
            _STATE["awake"] = True
            return None
        if method == "rest":
            _STATE["awake"] = False
            return None
        if method == "getState":
            return _STATE["life"]
        if method == "setState":
            _STATE["life"] = args[0]
            return None
        if method == "isEnabled":
            return _STATE["awareness"]
        if method == "setEnabled":
            _STATE["awareness"] = bool(args[0])
            return None
        if method == "getBatteryCharge":
            return 81
        if method == "getPosture":
            return "Stand"
        if method == "getPostureFamily":
            return "Standing"
        if method == "goToPosture":
            time.sleep(0.05)
            return True
        if method == "moveTo":
            time.sleep(0.1)
            x, y, theta = [float(a) for a in args[:3]]
            px, py, pt = _STATE["pose"]
            c, s = math.cos(pt), math.sin(pt)
            _STATE["pose"] = [px + x * c - y * s, py + x * s + y * c, pt + theta]
            return None
        if method == "getRobotPosition":
            return list(_STATE["pose"])
        if method == "robotName":
            return "FakePepper"
        if method == "systemVersion":
            return "2.5.10.7"
        if method == "getVolume":
            return _STATE["volume"]
        if method == "setVolume":
            _STATE["volume"] = args[0]
            return None
        if method == "subscribe":
            _SUBSCRIBERS.setdefault(self.name, set()).add(args[0])
            if self.name == "ALAudioDevice" and args[0] not in _AUDIO_PUMPS:
                _AUDIO_PUMPS[args[0]] = _AudioPump(args[0])
            return None
        if method == "unsubscribe":
            _SUBSCRIBERS.setdefault(self.name, set()).discard(args[0])
            pump = _AUDIO_PUMPS.pop(args[0], None)
            if pump is not None:
                pump.stop()
            return None
        if method == "setClientPreferences":
            if self.name == "ALAudioDevice":
                _AUDIO_PREFS[args[0]] = (int(args[1]), int(args[2]))
            return None
        if method == "subscriber":
            return _Subscriber(args[0])
        if method == "getSubscribersInfo":
            return [[n, 100, 0.0] for n in sorted(_SUBSCRIBERS.get(self.name, ()))]
        if method == "stopAllBehaviors":
            return None
        if method == "getInstalledBehaviors":
            return ["animations/Stand/Gestures/Hey_1", "animations/Stand/Gestures/BowShort_1", "dialog/x"]
        if method == "run":
            time.sleep(0.1)
            return None
        if method == "subscribeCamera":
            return "fake_cam_handle"
        if method == "getImageRemote":
            width, height = 8, 4
            data = bytearray()
            for y in range(height):
                for x in range(width):
                    data.extend([x * 30 % 256, y * 60 % 256, 128])
            return [width, height, 3, 11, 0, 0, data, 0]
        if method == "startMicrophonesRecording":
            with open(args[0], "wb") as fh:
                fh.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
            return None
        return None


def _to_map(pose):
    ox, oy, ot = _MAP["offset"]
    c, s = math.cos(ot), math.sin(ot)
    return [ox + pose[0] * c - pose[1] * s, oy + pose[0] * s + pose[1] * c, pose[2] + ot]


def _from_map(x, y):
    ox, oy, ot = _MAP["offset"]
    c, s = math.cos(-ot), math.sin(-ot)
    return [(x - ox) * c - (y - oy) * s, (x - ox) * s + (y - oy) * c]


def _place_in_map(estimate):
    """Choose the offset that puts Pepper's odometry pose at ``estimate`` [x, y, theta] in the map."""
    x, y, theta = [float(v) for v in estimate]
    px, py, pt = _STATE["pose"]
    ot = theta - pt
    c, s = math.cos(ot), math.sin(ot)
    _MAP["offset"] = [x - (px * c - py * s), y - (px * s + py * c), ot]


def _localized():
    if not _MAP["localizing"]:
        raise RuntimeError("ALNavigation: localization is not running")


def _navigation(method, args):
    """The documented shapes: explore and navigateToInMap give error codes (0 OK, 3 TargetReached, 5 TargetOutOfMap),
    a localized pose is [[x, y, theta], [radiusX, radiusY, orientation]], the map 0 (occupied) to 100 (free)."""
    if method == "explore":
        time.sleep(0.1)
        _MAP.update(loaded="(exploration)", localizing=False)
        _place_in_map([0.0, 0.0, 0.0])  # the map's origin is where exploring started
        return 0
    if method == "saveExploration":
        _MAP["saved"] += 1
        return "/home/nao/.local/share/Explorer/fake-%d.explo" % _MAP["saved"]
    if method == "loadExploration":
        _MAP.update(loaded=args[0], localizing=False)
        return True
    if method == "relocalizeInMap":
        if not _MAP["loaded"]:
            raise RuntimeError("ALNavigation: no exploration loaded")
        _place_in_map((list(args[0]) + [0.0, 0.0, 0.0])[:3])
        return [_to_map(_STATE["pose"]), [0.1, 0.1, 0.0]]
    if method == "startLocalization":
        if not _MAP["loaded"]:
            raise RuntimeError("ALNavigation: no exploration loaded")
        _MAP["localizing"] = True
        return None
    if method in ("stopLocalization", "stopExploration"):
        _MAP["localizing"] = False
        return None
    if method == "getRobotPositionInMap":
        _localized()
        return [_to_map(_STATE["pose"]), [0.12, 0.08, 0.0]]
    if method == "navigateToInMap":
        _localized()
        x, y = float(args[0][0]), float(args[0][1])
        if math.hypot(x, y) > 6.0:
            return 5
        time.sleep(0.1)
        ox, oy = _from_map(x, y)
        _STATE["pose"] = [ox, oy, _STATE["pose"][2]]  # the heading is not a target (NAOqi 2.5 docs)
        return 3
    if method == "getMetricalMap":
        _localized()
        width, height = 20, 10
        pixels = []
        for row in range(height):
            for col in range(width):
                edge = row in (0, height - 1) or col in (0, width - 1)
                pixels.append(0.0 if edge else 100.0)
        return [0.1, width, height, [-1.0, -0.5], pixels]
    return None


class Session(object):
    def __init__(self):
        self._connected = False
        self._services = {}

    def connect(self, url):
        LOG.info("fake qi session connect %s", url)
        self._connected = True
        for pump in _AUDIO_PUMPS.values():  # services registered by an earlier session are gone
            pump.stop()
        _AUDIO_PUMPS.clear()
        _SERVICES.clear()

    def isConnected(self):
        return self._connected

    def service(self, name):
        if name not in self._services:
            self._services[name] = _Service(name)
        return self._services[name]

    def registerService(self, name, obj):
        if name in _SERVICES:
            raise RuntimeError("Service %s already registered" % name)
        _SERVICES[name] = obj
        LOG.info("fake qi registerService %s", name)
        return len(_SERVICES)

    def unregisterService(self, service_id):
        for name, obj in list(_SERVICES.items()):
            if service_id == list(_SERVICES).index(name) + 1:
                del _SERVICES[name]
                return None
        return None
