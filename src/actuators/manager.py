"""
Actuator manager - boolean-returning convenience wrappers over the bridge.

Use :class:`PepperRobot` directly when you need the bridge's response payload
or the exception; use this when you just want "did it work".
"""

from typing import Any, Optional

from loguru import logger

from ..pepper.connection import PepperConnection


class ActuatorManager:
    """Sends actuation commands via the bridge HTTP API."""

    def __init__(self, connection: PepperConnection):
        self.connection = connection
        self.logger = logger.bind(module="ActuatorManager")

    @property
    def bridge(self):
        return self.connection.bridge

    async def _call(self, name: str, coro) -> bool:
        try:
            await coro
            return True
        except Exception as exc:
            self.logger.error(f"{name} failed: {exc}")
            return False

    async def speak(self, text: str, language: Optional[str] = None, animated: bool = True) -> bool:
        return await self._call("speak", self.bridge.speak(text, language=language, animated=animated))

    async def set_volume(self, level: int) -> bool:
        return await self._call("set_volume", self.bridge.set_volume(level))

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> bool:
        return await self._call("move_forward", self.bridge.move_forward(distance, speed))

    async def turn(self, angle: float) -> bool:
        return await self._call("turn", self.bridge.move_turn(angle))

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> bool:
        return await self._call("move_head", self.bridge.move_head(yaw, pitch, speed))

    async def move_to(self, x: float, y: float, theta: float = 0) -> bool:
        return await self._call("move_to", self.bridge.move_to(x, y, theta))

    async def stop(self) -> bool:
        return await self._call("stop", self.bridge.stop())

    async def emergency_stop(self) -> bool:
        return await self._call("emergency_stop", self.bridge.emergency_stop())

    async def set_posture(self, posture: str, speed: float = 0.5) -> bool:
        return await self._call("set_posture", self.bridge.set_posture(posture, speed))

    async def wake_up(self) -> bool:
        return await self._call("wake_up", self.bridge.wake_up())

    async def rest(self) -> bool:
        return await self._call("rest", self.bridge.rest())

    async def set_eye_color(self, color: str) -> bool:
        return await self._call("set_eye_color", self.bridge.set_eye_leds(color=color))

    async def set_chest_led(self, color: str) -> bool:
        return await self._call("set_chest_led", self.bridge.set_chest_leds(color=color))

    async def play_animation(self, name: str) -> bool:
        return await self._call("play_animation", self.bridge.play_animation(name))

    async def set_awareness(self, enabled: bool) -> bool:
        return await self._call("set_awareness", self.bridge.set_awareness(enabled))

    async def set_autonomous_life(self, state: str) -> bool:
        return await self._call("set_autonomous_life", self.bridge.set_autonomous_life(state))

    async def tablet_text(self, text: str, title: Optional[str] = None) -> bool:
        return await self._call("tablet_text", self.bridge.tablet_text(text, title=title))

    async def tablet_hide(self) -> bool:
        return await self._call("tablet_hide", self.bridge.tablet_hide())

    async def take_picture(self, camera: int = 0) -> Optional[Any]:
        try:
            return await self.bridge.take_picture(camera=camera)
        except Exception as exc:
            self.logger.error(f"take_picture failed: {exc}")
            return None

    async def record_audio(self, duration: float = 3.0) -> Optional[Any]:
        try:
            return await self.bridge.record_audio(duration)
        except Exception as exc:
            self.logger.error(f"record_audio failed: {exc}")
            return None
