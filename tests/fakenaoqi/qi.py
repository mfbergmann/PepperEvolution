# -*- coding: utf-8 -*-
"""
A tiny stand-in for NAOqi's ``qi`` module so the bridge can run off-robot.

Python 2.7 and 3 compatible. Put ``tests/fakenaoqi`` on PYTHONPATH and start
``robot_bridge/pepper_bridge.py`` as usual; every service call is logged and
answered with plausible data. The front head touch sensor toggles every
second so the sensor poller has something to report.
"""

import logging
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
            return None
        if method == "unsubscribe":
            _SUBSCRIBERS.setdefault(self.name, set()).discard(args[0])
            return None
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

    def isConnected(self):
        return self._connected

    def service(self, name):
        if name not in self._services:
            self._services[name] = _Service(name)
        return self._services[name]
