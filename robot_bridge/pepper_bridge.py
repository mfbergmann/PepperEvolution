#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pepper Bridge Server - Tornado HTTP + WebSocket bridge for NAOqi 2.5
Runs on the Pepper robot (Python 2.7 + Tornado 3.1.1).

Exposes NAOqi services as REST endpoints and pushes sensor events
over WebSocket so the host application never needs qi bindings.

Usage:
    python pepper_bridge.py [--port=8888] [--api-key=SECRET]
"""

import base64
import json
import logging
import struct
import sys
import time
import threading
import wave
import io

import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado.options import define, options

# NAOqi packages live outside the default Python path on Pepper
sys.path.insert(0, '/opt/aldebaran/lib/python2.7/site-packages')
import qi

# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------
define("port", default=8888, type=int, help="HTTP port")
define("api_key", default="", type=str, help="Optional API key for auth")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SESSION = None          # qi.Session
LOGGER = logging.getLogger("pepper_bridge")

# ---------------------------------------------------------------------------
# NAOqi helpers
# ---------------------------------------------------------------------------

def get_service(name):
    """Retrieve a NAOqi service from the global session."""
    return SESSION.service(name)


def safe_call(service_name, method, *args):
    """Call a NAOqi service method, return (result, None) or (None, error_str)."""
    try:
        svc = get_service(service_name)
        fn = getattr(svc, method)
        result = fn(*args)
        return result, None
    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Auth mixin
# ---------------------------------------------------------------------------

class AuthMixin(object):
    """Check X-API-Key header if an api_key is configured."""

    def check_auth(self):
        if not options.api_key:
            return True
        key = self.request.headers.get("X-API-Key", "")
        if key != options.api_key:
            self.set_status(401)
            self.write({"ok": False, "error": "unauthorized"})
            return False
        return True


# ---------------------------------------------------------------------------
# Base JSON handler
# ---------------------------------------------------------------------------

class JSONHandler(tornado.web.RequestHandler, AuthMixin):
    """Base handler that parses JSON body and returns JSON responses."""

    def prepare(self):
        if not self.check_auth():
            self.finish()
            return
        self.json_body = {}
        content_type = self.request.headers.get("Content-Type", "")
        if "application/json" in content_type and self.request.body:
            try:
                self.json_body = json.loads(self.request.body)
            except (ValueError, TypeError):
                pass

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


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------

class HealthHandler(JSONHandler):
    def get(self):
        self.ok({
            "bridge": "pepper_bridge",
            "version": "2.0.0",
            "naoqi": "2.5",
            "timestamp": time.time(),
        })


class StatusHandler(JSONHandler):
    def get(self):
        data = {}
        # Battery
        battery, err = safe_call("ALBattery", "getBatteryCharge")
        data["battery"] = battery if err is None else None

        # Posture
        posture, err = safe_call("ALRobotPosture", "getPostureFamily")
        data["posture"] = posture if err is None else "unknown"

        # Robot name
        name, err = safe_call("ALSystem", "robotName")
        data["robot_name"] = name if err is None else "Pepper"

        # NAOqi version
        ver, err = safe_call("ALSystem", "systemVersion")
        data["naoqi_version"] = ver if err is None else "unknown"

        # Autonomous life state
        state, err = safe_call("ALAutonomousLife", "getState")
        data["autonomous_life"] = state if err is None else "unknown"

        self.ok(data)


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------

class SpeakHandler(JSONHandler):
    def post(self):
        text = self.json_body.get("text", "")
        if not text:
            return self.fail("text is required")
        language = self.json_body.get("language")
        animated = self.json_body.get("animated", False)

        try:
            if language:
                tts = get_service("ALTextToSpeech")
                tts.setLanguage(language)

            if animated:
                svc = get_service("ALAnimatedSpeech")
                svc.say(text)
            else:
                svc = get_service("ALTextToSpeech")
                svc.say(text)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------

class MoveForwardHandler(JSONHandler):
    def post(self):
        distance = float(self.json_body.get("distance", 0.5))
        speed = float(self.json_body.get("speed", 0.3))
        # Clamp safety limits
        distance = max(-2.0, min(2.0, distance))
        speed = max(0.1, min(0.8, speed))
        try:
            motion = get_service("ALMotion")
            motion.moveTo(distance, 0, 0)
            self.ok({"distance": distance})
        except Exception as exc:
            self.fail(str(exc), 500)


class MoveTurnHandler(JSONHandler):
    def post(self):
        import math
        angle_deg = float(self.json_body.get("angle", 90))
        # Clamp
        angle_deg = max(-180, min(180, angle_deg))
        angle_rad = math.radians(angle_deg)
        try:
            motion = get_service("ALMotion")
            motion.moveTo(0, 0, angle_rad)
            self.ok({"angle": angle_deg})
        except Exception as exc:
            self.fail(str(exc), 500)


class MoveHeadHandler(JSONHandler):
    def post(self):
        import math
        yaw = float(self.json_body.get("yaw", 0))
        pitch = float(self.json_body.get("pitch", 0))
        speed = float(self.json_body.get("speed", 0.2))
        # Clamp
        yaw = max(-2.0, min(2.0, math.radians(yaw)))
        pitch = max(-0.7, min(0.5, math.radians(pitch)))
        speed = max(0.05, min(0.5, speed))
        try:
            motion = get_service("ALMotion")
            motion.setAngles("HeadYaw", yaw, speed)
            motion.setAngles("HeadPitch", pitch, speed)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


class MoveToHandler(JSONHandler):
    def post(self):
        import math
        x = float(self.json_body.get("x", 0))
        y = float(self.json_body.get("y", 0))
        theta = float(self.json_body.get("theta", 0))
        x = max(-3.0, min(3.0, x))
        y = max(-3.0, min(3.0, y))
        theta = math.radians(max(-180, min(180, theta)))
        try:
            motion = get_service("ALMotion")
            motion.moveTo(x, y, theta)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


class StopHandler(JSONHandler):
    def post(self):
        try:
            motion = get_service("ALMotion")
            motion.stopMove()
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


class EmergencyStopHandler(JSONHandler):
    def post(self):
        try:
            motion = get_service("ALMotion")
            motion.stopMove()
            motion.setStiffnesses("Body", 0.0)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------

class PostureHandler(JSONHandler):
    def post(self):
        posture = self.json_body.get("posture", "Stand")
        speed = float(self.json_body.get("speed", 0.5))
        speed = max(0.1, min(1.0, speed))
        try:
            svc = get_service("ALRobotPosture")
            svc.goToPosture(posture, speed)
            self.ok({"posture": posture})
        except Exception as exc:
            self.fail(str(exc), 500)


class WakeUpHandler(JSONHandler):
    def post(self):
        try:
            motion = get_service("ALMotion")
            motion.wakeUp()
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


class RestHandler(JSONHandler):
    def post(self):
        try:
            motion = get_service("ALMotion")
            motion.rest()
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Camera / Picture
# ---------------------------------------------------------------------------

class PictureHandler(JSONHandler):
    def get(self):
        camera_id = int(self.get_argument("camera", "0"))  # 0=top, 1=bottom
        resolution = int(self.get_argument("resolution", "2"))  # 2=VGA
        color_space = 11  # RGB
        fps = 5
        try:
            video = get_service("ALVideoDevice")
            handle = video.subscribeCamera(
                "pepper_bridge_cam", camera_id, resolution, color_space, fps
            )
            image = video.getImageRemote(handle)
            video.unsubscribe(handle)

            if image is None:
                return self.fail("camera returned no image", 500)

            width = image[0]
            height = image[1]
            raw = image[6]

            # Convert raw RGB to JPEG via PIL
            try:
                from PIL import Image as PILImage
                img = PILImage.frombytes("RGB", (width, height), bytes(raw))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            except ImportError:
                # Fallback: return raw base64 (less useful but still data)
                b64 = base64.b64encode(bytes(raw)).decode("ascii")

            self.ok({
                "image": b64,
                "width": width,
                "height": height,
                "format": "jpeg",
            })
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

class SensorsHandler(JSONHandler):
    def get(self):
        data = {}
        mem, err = None, None
        try:
            mem = get_service("ALMemory")
        except Exception as exc:
            return self.fail("cannot access ALMemory: " + str(exc), 500)

        # Battery
        try:
            data["battery"] = mem.getData("Device/SubDeviceList/Battery/Charge/Sensor/Value") * 100
        except Exception:
            data["battery"] = None

        # Touch sensors
        touch = {}
        touch_keys = {
            "head_front": "Device/SubDeviceList/Head/Touch/Front/Sensor/Value",
            "head_middle": "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value",
            "head_rear": "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value",
            "hand_left": "Device/SubDeviceList/LHand/Touch/Back/Sensor/Value",
            "hand_right": "Device/SubDeviceList/RHand/Touch/Back/Sensor/Value",
        }
        for name, key in touch_keys.items():
            try:
                touch[name] = bool(mem.getData(key))
            except Exception:
                touch[name] = False
        data["touch"] = touch

        # Sonar
        sonar = {}
        try:
            sonar["left"] = mem.getData("Device/SubDeviceList/US/Left/Sensor/Value")
        except Exception:
            sonar["left"] = None
        try:
            sonar["right"] = mem.getData("Device/SubDeviceList/US/Right/Sensor/Value")
        except Exception:
            sonar["right"] = None
        data["sonar"] = sonar

        # People detection
        try:
            people_svc = get_service("ALPeoplePerception")
            people_ids = people_svc.getVisiblePeopleList()
            data["people_count"] = len(people_ids)
        except Exception:
            data["people_count"] = None

        self.ok(data)


# ---------------------------------------------------------------------------
# LEDs
# ---------------------------------------------------------------------------

class LEDEyesHandler(JSONHandler):
    def post(self):
        r = float(self.json_body.get("r", 0))
        g = float(self.json_body.get("g", 0))
        b = float(self.json_body.get("b", 0))
        color_name = self.json_body.get("color")
        duration = float(self.json_body.get("duration", 0.5))

        color_map = {
            "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
            "yellow": (1, 1, 0), "purple": (1, 0, 1), "cyan": (0, 1, 1),
            "white": (1, 1, 1), "off": (0, 0, 0),
        }
        if color_name and color_name in color_map:
            r, g, b = color_map[color_name]

        # Pack as 0xRRGGBB int
        ri = max(0, min(255, int(r * 255)))
        gi = max(0, min(255, int(g * 255)))
        bi = max(0, min(255, int(b * 255)))
        rgb_int = (ri << 16) | (gi << 8) | bi

        try:
            leds = get_service("ALLeds")
            leds.fadeRGB("FaceLeds", rgb_int, duration)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


class LEDChestHandler(JSONHandler):
    def post(self):
        r = float(self.json_body.get("r", 0))
        g = float(self.json_body.get("g", 0))
        b = float(self.json_body.get("b", 0))
        color_name = self.json_body.get("color")
        duration = float(self.json_body.get("duration", 0.5))

        color_map = {
            "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
            "yellow": (1, 1, 0), "purple": (1, 0, 1), "cyan": (0, 1, 1),
            "white": (1, 1, 1), "off": (0, 0, 0),
        }
        if color_name and color_name in color_map:
            r, g, b = color_map[color_name]

        ri = max(0, min(255, int(r * 255)))
        gi = max(0, min(255, int(g * 255)))
        bi = max(0, min(255, int(b * 255)))
        rgb_int = (ri << 16) | (gi << 8) | bi

        try:
            leds = get_service("ALLeds")
            leds.fadeRGB("ChestLeds", rgb_int, duration)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

class AnimationHandler(JSONHandler):
    def post(self):
        name = self.json_body.get("name", "")
        if not name:
            return self.fail("name is required")
        try:
            anim = get_service("ALAnimationPlayer")
            anim.run(name)
            self.ok()
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

class VolumeHandler(JSONHandler):
    def post(self):
        level = int(self.json_body.get("level", 50))
        level = max(0, min(100, level))
        try:
            tts = get_service("ALTextToSpeech")
            tts.setVolume(level / 100.0)
            self.ok({"level": level})
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Awareness
# ---------------------------------------------------------------------------

class AwarenessHandler(JSONHandler):
    def post(self):
        enabled = bool(self.json_body.get("enabled", True))
        try:
            ba = get_service("ALBasicAwareness")
            if enabled:
                ba.setEnabled(True)
            else:
                ba.setEnabled(False)
            self.ok({"enabled": enabled})
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Autonomous Life
# ---------------------------------------------------------------------------

class AutonomousLifeHandler(JSONHandler):
    def post(self):
        state = self.json_body.get("state", "solitary")
        try:
            al = get_service("ALAutonomousLife")
            al.setState(state)
            self.ok({"state": state})
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# Audio Recording
# ---------------------------------------------------------------------------

class AudioRecordHandler(JSONHandler):
    def post(self):
        duration = float(self.json_body.get("duration", 3.0))
        duration = max(0.5, min(10.0, duration))
        filename = "/tmp/pepper_bridge_recording.wav"
        try:
            recorder = get_service("ALAudioRecorder")
            recorder.startMicrophonesRecording(filename, "wav", 16000, [0, 0, 1, 0])
            # Wait for recording
            import time as _time
            _time.sleep(duration)
            recorder.stopMicrophonesRecording()

            # Read the file and encode
            with open(filename, "rb") as f:
                audio_data = f.read()
            b64 = base64.b64encode(audio_data).decode("ascii")
            self.ok({"audio": b64, "format": "wav", "duration": duration})
        except Exception as exc:
            self.fail(str(exc), 500)


# ---------------------------------------------------------------------------
# WebSocket for live events
# ---------------------------------------------------------------------------

class EventWebSocket(tornado.websocket.WebSocketHandler, AuthMixin):
    """Push NAOqi events (touch, sonar, people, battery) to connected clients."""

    clients = set()

    def check_origin(self, origin):
        return True

    def open(self):
        # Check auth via query param for WebSocket
        if options.api_key:
            key = self.get_argument("api_key", "")
            if key != options.api_key:
                self.close(4001, "unauthorized")
                return
        EventWebSocket.clients.add(self)
        LOGGER.info("WS client connected (%d total)", len(EventWebSocket.clients))

    def on_close(self):
        EventWebSocket.clients.discard(self)
        LOGGER.info("WS client disconnected (%d total)", len(EventWebSocket.clients))

    def on_message(self, message):
        # Clients can send a ping; we just echo
        try:
            data = json.loads(message)
            if data.get("type") == "ping":
                self.write_message(json.dumps({"type": "pong", "timestamp": time.time()}))
        except (ValueError, TypeError):
            pass

    @classmethod
    def broadcast(cls, event_type, payload):
        msg = json.dumps({"type": event_type, "data": payload, "timestamp": time.time()})
        dead = []
        for client in cls.clients:
            try:
                client.write_message(msg)
            except Exception:
                dead.append(client)
        for c in dead:
            cls.clients.discard(c)


# ---------------------------------------------------------------------------
# Event subscriber thread
# ---------------------------------------------------------------------------

class EventSubscriber(object):
    """Background thread that subscribes to ALMemory events and broadcasts."""

    POLL_INTERVAL = 0.5  # seconds

    def __init__(self):
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        mem = None
        try:
            mem = get_service("ALMemory")
        except Exception:
            LOGGER.warning("Could not get ALMemory for event polling")
            return

        last_battery = None
        touch_keys = {
            "head_front": "Device/SubDeviceList/Head/Touch/Front/Sensor/Value",
            "head_middle": "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value",
            "head_rear": "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value",
            "hand_left": "Device/SubDeviceList/LHand/Touch/Back/Sensor/Value",
            "hand_right": "Device/SubDeviceList/RHand/Touch/Back/Sensor/Value",
        }
        last_touch = {}

        while self._running:
            try:
                # Touch events
                touch_now = {}
                for name, key in touch_keys.items():
                    try:
                        touch_now[name] = bool(mem.getData(key))
                    except Exception:
                        touch_now[name] = False

                if touch_now != last_touch:
                    changed = {k: v for k, v in touch_now.items() if v != last_touch.get(k)}
                    if changed:
                        EventWebSocket.broadcast("touch", touch_now)
                    last_touch = dict(touch_now)

                # Sonar
                try:
                    left = mem.getData("Device/SubDeviceList/US/Left/Sensor/Value")
                    right = mem.getData("Device/SubDeviceList/US/Right/Sensor/Value")
                    if left is not None and (left < 0.4 or right < 0.4):
                        EventWebSocket.broadcast("sonar", {
                            "left": left, "right": right,
                            "obstacle": True,
                        })
                except Exception:
                    pass

                # Battery (report every 30s or on change)
                try:
                    bat = mem.getData("Device/SubDeviceList/Battery/Charge/Sensor/Value")
                    bat_pct = int(bat * 100) if bat is not None else None
                    if bat_pct != last_battery:
                        EventWebSocket.broadcast("battery", {"level": bat_pct})
                        last_battery = bat_pct
                except Exception:
                    pass

                # People
                try:
                    people = get_service("ALPeoplePerception")
                    ids = people.getVisiblePeopleList()
                    if ids:
                        EventWebSocket.broadcast("people", {"count": len(ids), "ids": ids})
                except Exception:
                    pass

            except Exception as exc:
                LOGGER.error("Event loop error: %s", exc)

            time.sleep(self.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

def make_app():
    return tornado.web.Application([
        (r"/health", HealthHandler),
        (r"/status", StatusHandler),
        (r"/speak", SpeakHandler),
        (r"/move/forward", MoveForwardHandler),
        (r"/move/turn", MoveTurnHandler),
        (r"/move/head", MoveHeadHandler),
        (r"/move/to", MoveToHandler),
        (r"/stop", StopHandler),
        (r"/emergency_stop", EmergencyStopHandler),
        (r"/posture", PostureHandler),
        (r"/wake_up", WakeUpHandler),
        (r"/rest", RestHandler),
        (r"/picture", PictureHandler),
        (r"/sensors", SensorsHandler),
        (r"/leds/eyes", LEDEyesHandler),
        (r"/leds/chest", LEDChestHandler),
        (r"/animation", AnimationHandler),
        (r"/volume", VolumeHandler),
        (r"/awareness", AwarenessHandler),
        (r"/autonomous_life", AutonomousLifeHandler),
        (r"/audio/record", AudioRecordHandler),
        (r"/ws/events", EventWebSocket),
    ])


def main():
    tornado.options.parse_command_line()

    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Starting Pepper Bridge Server on port %d", options.port)

    # Connect to local NAOqi
    global SESSION
    SESSION = qi.Session()
    try:
        SESSION.connect("tcp://127.0.0.1:9559")
        LOGGER.info("Connected to NAOqi")
    except Exception as exc:
        LOGGER.error("Cannot connect to NAOqi: %s", exc)
        sys.exit(1)

    # Start event subscriber
    subscriber = EventSubscriber()
    subscriber.start()

    # Start Tornado
    app = make_app()
    app.listen(options.port)
    LOGGER.info("Bridge server listening on http://0.0.0.0:%d", options.port)

    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down...")
        subscriber.stop()


if __name__ == "__main__":
    main()
