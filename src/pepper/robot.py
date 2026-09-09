"""
Main Pepper robot interface providing high-level control methods.

Delegates all hardware interaction to the bridge via BridgeClient. Methods
raise :class:`BridgeError` on failure so callers (the tool executor, the API)
can report precise errors; the thin ActuatorManager/SensorManager wrappers
provide the boolean-returning convenience layer.
"""

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

from ..actuators import ActuatorManager
from ..sensors import SensorManager
from .bridge_client import BridgeError
from .connection import ConnectionConfig, PepperConnection


@dataclass
class RobotState:
    """Current state of the Pepper robot (refreshed from /status)."""

    battery_level: float = 0.0
    charging: Optional[bool] = None
    posture: str = "unknown"
    robot_name: str = "Pepper"
    autonomous_life: str = "unknown"
    awake: Optional[bool] = None
    language: str = "unknown"
    volume: Optional[int] = None
    is_connected: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "battery_level": self.battery_level,
            "charging": self.charging,
            "posture": self.posture,
            "robot_name": self.robot_name,
            "autonomous_life": self.autonomous_life,
            "awake": self.awake,
            "language": self.language,
            "volume": self.volume,
            "is_connected": self.is_connected,
        }


@dataclass
class PrepareOptions:
    """What to do to the robot right after connecting."""

    enabled: bool = True
    autonomous_life: Optional[str] = "disabled"
    wake_up: bool = True
    posture: Optional[str] = None
    awareness: Optional[bool] = None


@dataclass
class Photo:
    """A camera frame ready to hand to a vision model."""

    media_type: str
    base64_data: str
    width: int
    height: int
    camera: int = 0

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64_data}"


def _to_jpeg_photo(result: Dict[str, Any]) -> Photo:
    """Normalise a bridge /picture result to JPEG/PNG base64."""
    fmt = (result.get("format") or "jpeg").lower()
    width = int(result.get("width") or 0)
    height = int(result.get("height") or 0)
    data = result.get("image") or ""
    if fmt in ("jpeg", "jpg"):
        return Photo("image/jpeg", data, width, height, int(result.get("camera", 0)))
    if fmt == "png":
        return Photo("image/png", data, width, height, int(result.get("camera", 0)))
    # Raw RGB from a robot without PIL: convert on the host.
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is in requirements
        raise BridgeError("Bridge returned raw RGB and Pillow is not installed on the host") from exc
    raw = base64.b64decode(data)
    img = Image.frombytes("RGB", (width, height), raw)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return Photo(
        "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii"), width, height, int(result.get("camera", 0))
    )


class PepperRobot:
    """Main interface for controlling Pepper robot."""

    def __init__(self, connection_config: ConnectionConfig, bridge: Optional[Any] = None):
        self.connection = PepperConnection(connection_config, bridge=bridge)
        self.sensors = SensorManager(self.connection)
        self.actuators = ActuatorManager(self.connection)
        self.state = RobotState()
        self.animations: List[str] = []
        self.last_photo: Optional[Photo] = None
        self.halted = False  # emergency stop pressed; cleared by wake_up()/prepare()
        self.direct_commands_running = 0  # UI/API commands in flight (event reactions wait)
        self.photo_resolution = 2  # 0=QQVGA 1=QVGA 2=VGA 3=4VGA
        self.last_prepare: Dict[str, Any] = {}
        self.logger = logger.bind(module="PepperRobot")
        self._event_callbacks: List[Callable[[str, Dict[str, Any]], Coroutine]] = []
        self._state_task: Optional[asyncio.Task] = None

    @property
    def bridge(self):
        return self.connection.bridge

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, prepare: Optional[PrepareOptions] = None) -> bool:
        """Connect to the bridge, optionally prepare the robot, and load state."""
        try:
            self.logger.info("Initializing Pepper robot...")
            if not await self.connection.connect():
                return False

            self.connection.events.on_any(self._on_bridge_event)

            if prepare and prepare.enabled:
                prepared = await self.prepare(prepare)
                if prepared.get("errors"):
                    self.logger.warning(f"Prepare reported {len(prepared['errors'])} problem(s): {prepared['errors']}")

            await self.refresh_state()
            await self.load_animations()
            self.logger.success(
                f"Pepper ready: {self.state.robot_name}, battery {self.state.battery_level}%, "
                f"posture {self.state.posture}, life={self.state.autonomous_life}"
            )
            return True
        except Exception as exc:
            self.logger.error(f"Failed to initialize robot: {exc}")
            return False

    async def prepare(self, options: Optional[PrepareOptions] = None) -> Dict[str, Any]:
        """Disable autonomous life, wake up, optionally set posture/awareness.

        The bridge attempts every step and reports failures in ``errors``; they
        are logged here and returned so callers can show them.
        """
        options = options or PrepareOptions()
        self.logger.info(
            f"Preparing robot: autonomous_life={options.autonomous_life} wake_up={options.wake_up} "
            f"posture={options.posture} awareness={options.awareness}"
        )
        try:
            result = await self.bridge.prepare(
                autonomous_life=options.autonomous_life,
                wake_up=options.wake_up,
                posture=options.posture,
                awareness=options.awareness,
            )
        except BridgeError as exc:
            self.logger.warning(f"Prepare failed (continuing anyway): {exc}")
            return {"ok": False, "error": str(exc), "errors": [str(exc)]}
        for err in result.get("errors") or []:
            self.logger.warning(f"Prepare step failed: {err}")
        if options.wake_up and result.get("awake"):
            self.halted = False
        self.last_prepare = result
        return result

    async def shutdown(self, rest: bool = False):
        """Shutdown the robot connection; optionally put the robot to rest first."""
        self.logger.info("Shutting down Pepper robot...")
        if self._state_task:
            self._state_task.cancel()
            self._state_task = None
        if rest and self.connection.is_connected():
            try:
                await self.bridge.rest()
            except Exception as exc:
                self.logger.warning(f"rest failed during shutdown: {exc}")
        await self.connection.disconnect()
        self.state.is_connected = False
        self.logger.info("Pepper robot shutdown complete")

    async def refresh_state(self) -> RobotState:
        """Update robot state from the bridge /status endpoint."""
        try:
            data = await self.bridge.status()
            self.state.battery_level = data.get("battery") or 0.0
            self.state.charging = data.get("charging")
            self.state.posture = data.get("posture") or "unknown"
            self.state.robot_name = data.get("robot_name") or "Pepper"
            self.state.autonomous_life = data.get("autonomous_life") or "unknown"
            self.state.awake = data.get("awake")
            self.state.language = data.get("language") or "unknown"
            self.state.volume = data.get("volume")
            if "halted" in data:
                self.halted = bool(data["halted"])
            self.state.is_connected = self.connection.is_connected()
        except Exception as exc:
            self.logger.warning(f"Failed to update state: {exc}")
        return self.state

    async def load_animations(self) -> List[str]:
        try:
            self.animations = await self.bridge.list_animations()
            self.logger.info(f"Robot reports {len(self.animations)} installed animations")
        except Exception as exc:
            self.logger.warning(f"Could not list animations: {exc}")
            self.animations = []
        return self.animations

    # ------------------------------------------------------------------
    # High-level control (raise BridgeError on failure)
    # ------------------------------------------------------------------

    async def speak(
        self, text: str, language: Optional[str] = None, animated: bool = True, wait: bool = True
    ) -> Dict[str, Any]:
        return await self.bridge.speak(text, language=language, animated=animated, wait=wait)

    async def stop_speaking(self) -> Dict[str, Any]:
        return await self.bridge.stop_speaking()

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> Dict[str, Any]:
        return await self.bridge.move_forward(distance, speed)

    async def turn(self, angle: float) -> Dict[str, Any]:
        return await self.bridge.move_turn(angle)

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> Dict[str, Any]:
        return await self.bridge.move_head(yaw, pitch, speed)

    async def set_posture(self, posture: str, speed: float = 0.5) -> Dict[str, Any]:
        return await self.bridge.set_posture(posture, speed)

    async def wake_up(self) -> Dict[str, Any]:
        result = await self.bridge.wake_up()
        self.halted = False
        return result

    async def rest(self) -> Dict[str, Any]:
        return await self.bridge.rest()

    async def stop(self) -> Dict[str, Any]:
        return await self.bridge.stop()

    async def emergency_stop(self) -> Dict[str, Any]:
        self.logger.warning("Emergency stop activated!")
        self.halted = True  # set before the request so concurrent turns stop immediately
        return await self.bridge.emergency_stop()

    async def take_picture(self, camera: int = 0, resolution: Optional[int] = None) -> Photo:
        """Take a photo and return it as a JPEG/PNG :class:`Photo`."""
        if resolution is None:
            resolution = self.photo_resolution
        result = await self.bridge.take_picture(camera=camera, resolution=resolution)
        if not result.get("image"):
            raise BridgeError("Camera returned no image")
        photo = _to_jpeg_photo(result)
        self.last_photo = photo
        return photo

    async def play_animation(self, name: str) -> Dict[str, Any]:
        return await self.bridge.play_animation(name)

    async def set_eye_color(self, color: str) -> Dict[str, Any]:
        return await self.bridge.set_eye_leds(color=color)

    async def set_chest_color(self, color: str) -> Dict[str, Any]:
        return await self.bridge.set_chest_leds(color=color)

    async def set_volume(self, level: int) -> Dict[str, Any]:
        return await self.bridge.set_volume(level)

    async def set_awareness(self, enabled: bool) -> Dict[str, Any]:
        return await self.bridge.set_awareness(enabled)

    async def set_autonomous_life(self, state: str) -> Dict[str, Any]:
        return await self.bridge.set_autonomous_life(state)

    async def tablet_text(self, text: str, title: Optional[str] = None) -> Dict[str, Any]:
        return await self.bridge.tablet_text(text, title=title)

    async def tablet_web(self, url: str) -> Dict[str, Any]:
        return await self.bridge.tablet_web(url)

    async def tablet_hide(self) -> Dict[str, Any]:
        return await self.bridge.tablet_hide()

    async def get_sensors(self) -> Dict[str, Any]:
        """Get aggregated sensor data."""
        return await self.sensors.get_all()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_event(self, callback: Callable[[str, Dict[str, Any]], Coroutine]):
        """Register callback for all bridge events."""
        self._event_callbacks.append(callback)

    async def _on_bridge_event(self, event_type: str, data: Dict[str, Any]):
        if event_type == "battery" and data.get("level") is not None:
            self.state.battery_level = data["level"]
            self.state.charging = data.get("charging")
        for cb in self._event_callbacks:
            try:
                await cb(event_type, data)
            except Exception as exc:
                self.logger.error(f"Event callback error: {exc}")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def get_state(self) -> RobotState:
        return self.state

    async def is_ready(self) -> bool:
        return self.connection.is_connected()

    def start_state_loop(self, interval: float = 10.0) -> asyncio.Task:
        """Refresh state periodically in the background."""
        if self._state_task is None or self._state_task.done():
            self._state_task = asyncio.create_task(self._state_loop(interval), name="robot-state-loop")
        return self._state_task

    async def _state_loop(self, interval: float):
        self.logger.info(f"State refresh loop every {interval:.0f}s")
        while self.connection.is_connected():
            await asyncio.sleep(interval)
            try:
                await self.refresh_state()
            except Exception as exc:
                self.logger.error(f"State refresh error: {exc}")

    async def start_event_loop(self):
        """Backwards-compatible alias: run the state loop in the foreground."""
        await self._state_loop(10.0)
