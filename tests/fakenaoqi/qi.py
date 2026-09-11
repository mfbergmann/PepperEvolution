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
}

_STATE = {"language": "English", "awake": False, "life": "solitary", "volume": 0.5, "awareness": True}
_SUBSCRIBERS = {}  # service -> set of subscriber names
_SERVICES = {}  # name -> object registered with Session.registerService
_AUDIO_PUMPS = {}  # subscriber name -> _AudioPump thread (ALAudioDevice.subscribe)

AUDIO_FRAME_SAMPLES = 2730  # ~170 ms at 16 kHz, like the robot
AUDIO_FRAME_PERIOD = 0.17


def _tone_frame(n, phase):
    """n samples of a 440 Hz tone as 16-bit little-endian PCM (what ALAudioDevice hands to processRemote)."""
    samples = [int(3000 * math.sin(2 * math.pi * 440 * (phase + i) / 16000.0)) for i in range(n)]
    return struct.pack("<%dh" % n, *samples)


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
            try:
                target.processRemote(1, AUDIO_FRAME_SAMPLES, stamp, _tone_frame(AUDIO_FRAME_SAMPLES, self.phase))
            except Exception as exc:  # the real ALAudioDevice would just drop the frame
                LOG.warning("processRemote failed: %s", exc)
            self.phase += AUDIO_FRAME_SAMPLES

    def stop(self):
        self.running = False


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
        def call(*args):
            LOG.info("%s.%s%s", self.name, method, args)
            return self._respond(method, args)

        return call

    def _respond(self, method, args):
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
            return None
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
