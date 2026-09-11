"""
In-memory stand-in for the bridge, for running the whole stack without a robot.

Enable with ``PEPPER_FAKE_BRIDGE=true``. Every action is logged instead of
executed, speech takes a little simulated time so streaming/sequencing can be
observed, and ``take_picture`` returns a generated test image so the vision
path can be exercised end to end.
"""

import asyncio
import base64
import io
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .bridge_client import BridgeError

try:
    from PIL import Image, ImageDraw
except ImportError:  # Pillow is in requirements, but stay importable without it
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

# 1x1 white PNG, used if Pillow is unavailable.
_FALLBACK_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

SIMULATED_SPEECH_SECONDS_PER_WORD = 0.12


def _test_image(width: int = 640, height: int = 480) -> Dict[str, Any]:
    if Image is None:
        return {"image": _FALLBACK_PNG, "width": 1, "height": 1, "format": "png"}
    img = Image.new("RGB", (width, height), (30, 60, 110))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, int(height * 0.7), width, height], fill=(90, 70, 50))  # a "floor"
    draw.rectangle([int(width * 0.55), int(height * 0.35), int(width * 0.9), int(height * 0.7)], fill=(180, 140, 90))
    draw.ellipse([int(width * 0.15), int(height * 0.2), int(width * 0.35), int(height * 0.45)], fill=(240, 220, 200))
    draw.text((20, 20), f"FAKE PEPPER CAMERA {time.strftime('%H:%M:%S')}", fill=(255, 255, 255))
    draw.text((20, 40), "a table, a lamp and a person-ish blob", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return {
        "image": base64.b64encode(buf.getvalue()).decode("ascii"),
        "width": width,
        "height": height,
        "format": "jpeg",
    }


class FakeBridgeClient:
    """Drop-in replacement for :class:`BridgeClient` that never touches a robot."""

    def __init__(self, base_url: str = "fake://pepper", api_key: str = "", timeout: float = 15.0, **_: Any):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.logger = logger.bind(module="FakeBridge")
        self.calls: List[Dict[str, Any]] = []
        self.connected = False
        self.state: Dict[str, Any] = {
            "battery": 87,
            "charging": False,
            "posture": "Stand",
            "awake": True,
            "autonomous_life": "disabled",
            "language": "English",
            "volume": 60,
            "awareness": False,
            "eye_color": "white",
            "chest_color": "off",
            "tablet": "",
        }
        self.speech_delay = SIMULATED_SPEECH_SECONDS_PER_WORD
        self.fail_next: Optional[str] = None

    # -- plumbing -----------------------------------------------------------

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    def _record(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        if self.fail_next:
            error, self.fail_next = self.fail_next, None
            raise BridgeError(error)
        entry = {"action": action, "time": time.time(), **kwargs}
        self.calls.append(entry)
        self.logger.info(f"[fake robot] {action} {kwargs if kwargs else ''}")
        return {"ok": True, **kwargs}

    # -- endpoints ------------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        return {"ok": True, "bridge": "fake_bridge", "version": "2.2.0", "naoqi": "fake", "robot_name": "FakePepper"}

    async def status(self) -> Dict[str, Any]:
        return {"ok": True, "robot_name": "FakePepper", "naoqi_version": "fake", **self.state}

    async def get_sensors(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "battery": self.state["battery"],
            "charging": self.state["charging"],
            "touch": {k: False for k in ("head_front", "head_middle", "head_rear", "hand_left", "hand_right")},
            "bumpers": {"front_left": False, "front_right": False, "back": False},
            "sonar": {"front": 1.8, "back": 2.4},
            "obstacle": False,
            "people_count": 1,
            "people_ids": [42],
            "timestamp": time.time(),
        }

    async def speak(
        self,
        text: str,
        language: Optional[str] = None,
        animated: bool = True,
        wait: bool = True,
        body_language: str = "contextual",
    ) -> Dict[str, Any]:
        result = self._record("speak", text=text, animated=animated, language=language)
        if wait and self.speech_delay:
            await asyncio.sleep(min(6.0, self.speech_delay * max(1, len(text.split()))))
        return result

    async def stop_speaking(self) -> Dict[str, Any]:
        return self._record("stop_speaking")

    async def set_volume(self, level: int) -> Dict[str, Any]:
        self.state["volume"] = int(level)
        return self._record("set_volume", level=int(level))

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> Dict[str, Any]:
        result = self._record("move_forward", distance=distance, speed=speed)
        await asyncio.sleep(0.2)
        return result

    async def move_turn(self, angle: float) -> Dict[str, Any]:
        result = self._record("turn", angle=angle)
        await asyncio.sleep(0.2)
        return result

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> Dict[str, Any]:
        return self._record("move_head", yaw=yaw, pitch=pitch, speed=speed)

    async def move_to(self, x: float, y: float, theta: float = 0, speed: Optional[float] = None) -> Dict[str, Any]:
        return self._record("move_to", x=x, y=y, theta=theta)

    async def stop(self) -> Dict[str, Any]:
        return self._record("stop")

    async def emergency_stop(self) -> Dict[str, Any]:
        self.state["awake"] = False
        self.state["posture"] = "Crouch"
        return self._record("emergency_stop", resting=True)

    async def set_posture(self, posture: str, speed: float = 0.5) -> Dict[str, Any]:
        self.state["posture"] = posture
        return self._record("set_posture", posture=posture)

    async def wake_up(self) -> Dict[str, Any]:
        self.state["awake"] = True
        return self._record("wake_up")

    async def rest(self) -> Dict[str, Any]:
        self.state["awake"] = False
        self.state["posture"] = "Crouch"
        return self._record("rest")

    async def prepare(
        self,
        autonomous_life: Optional[str] = "disabled",
        wake_up: bool = True,
        posture: Optional[str] = None,
        awareness: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if autonomous_life:
            self.state["autonomous_life"] = autonomous_life
        if wake_up:
            self.state["awake"] = True
        if posture:
            self.state["posture"] = posture
        if awareness is not None:
            self.state["awareness"] = awareness
        return self._record(
            "prepare", autonomous_life=autonomous_life, wake_up=wake_up, posture=posture, awareness=awareness
        )

    async def set_awareness(
        self,
        enabled: bool,
        tracking_mode: Optional[str] = None,
        engagement_mode: Optional[str] = None,
        stimuli: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self.state["awareness"] = bool(enabled)
        return self._record(
            "set_awareness",
            enabled=bool(enabled),
            tracking_mode=tracking_mode,
            engagement_mode=engagement_mode,
            stimuli=list(stimuli) if stimuli is not None else None,
        )

    async def set_autonomous_life(self, state: str) -> Dict[str, Any]:
        self.state["autonomous_life"] = state
        return self._record("set_autonomous_life", state=state)

    async def take_picture(self, camera: int = 0, resolution: int = 2) -> Dict[str, Any]:
        self._record("take_picture", camera=camera)
        return {"ok": True, "camera": camera, **_test_image()}

    async def record_audio(self, duration: float = 3.0) -> Dict[str, Any]:
        self._record("record_audio", duration=duration)
        return {"ok": True, "audio": "", "format": "wav", "duration": duration}

    async def audio_stream_info(self) -> Dict[str, Any]:
        return {"ok": True, "streaming": False, "clients": 0, "sample_rate": 16000, "channels": 1, "muted": False}

    async def set_eye_leds(
        self, color: Optional[str] = None, r: float = 0, g: float = 0, b: float = 0, duration: float = 0.5
    ) -> Dict[str, Any]:
        self.state["eye_color"] = color or f"rgb({r},{g},{b})"
        return self._record("set_eye_leds", color=self.state["eye_color"])

    async def set_chest_leds(
        self, color: Optional[str] = None, r: float = 0, g: float = 0, b: float = 0, duration: float = 0.5
    ) -> Dict[str, Any]:
        self.state["chest_color"] = color or f"rgb({r},{g},{b})"
        return self._record("set_chest_leds", color=self.state["chest_color"])

    async def play_animation(self, name: str) -> Dict[str, Any]:
        result = self._record("play_animation", name=name)
        await asyncio.sleep(0.3)
        return result

    async def list_animations(self) -> List[str]:
        from ..ai.tools import KNOWN_ANIMATIONS

        return sorted(KNOWN_ANIMATIONS)

    async def tablet_text(self, text: str, title: Optional[str] = None) -> Dict[str, Any]:
        self.state["tablet"] = text
        return self._record("tablet_text", text=text, title=title)

    async def tablet_web(self, url: str) -> Dict[str, Any]:
        self.state["tablet"] = url
        return self._record("tablet_web", url=url)

    async def tablet_image(self, url: str) -> Dict[str, Any]:
        self.state["tablet"] = url
        return self._record("tablet_image", url=url)

    async def tablet_hide(self) -> Dict[str, Any]:
        self.state["tablet"] = ""
        return self._record("tablet_hide")
