"""
Actuator manager - delegates to the bridge for all robot control.
"""

from typing import Optional

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

    async def speak(self, text: str, language: Optional[str] = None, animated: bool = False) -> bool:
        try:
            await self.bridge.speak(text, language=language, animated=animated)
            return True
        except Exception as exc:
            self.logger.error(f"speak failed: {exc}")
            return False

    async def set_volume(self, level: int) -> bool:
        try:
            await self.bridge.set_volume(level)
            return True
        except Exception as exc:
            self.logger.error(f"set_volume failed: {exc}")
            return False

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> bool:
        try:
            await self.bridge.move_forward(distance, speed)
            return True
        except Exception as exc:
            self.logger.error(f"move_forward failed: {exc}")
            return False

    async def turn(self, angle: float) -> bool:
        try:
            await self.bridge.move_turn(angle)
            return True
        except Exception as exc:
            self.logger.error(f"turn failed: {exc}")
            return False

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> bool:
        try:
            await self.bridge.move_head(yaw, pitch, speed)
            return True
        except Exception as exc:
            self.logger.error(f"move_head failed: {exc}")
            return False

    async def move_to(self, x: float, y: float, theta: float = 0) -> bool:
        try:
            await self.bridge.move_to(x, y, theta)
            return True
        except Exception as exc:
            self.logger.error(f"move_to failed: {exc}")
            return False

    async def stop(self) -> bool:
        try:
            await self.bridge.stop()
            return True
        except Exception as exc:
            self.logger.error(f"stop failed: {exc}")
            return False

    async def emergency_stop(self) -> bool:
        try:
            await self.bridge.emergency_stop()
            return True
        except Exception as exc:
            self.logger.error(f"emergency_stop failed: {exc}")
            return False

    async def set_posture(self, posture: str, speed: float = 0.5) -> bool:
        try:
            await self.bridge.set_posture(posture, speed)
            return True
        except Exception as exc:
            self.logger.error(f"set_posture failed: {exc}")
            return False

    async def wake_up(self) -> bool:
        try:
            await self.bridge.wake_up()
            return True
        except Exception as exc:
            self.logger.error(f"wake_up failed: {exc}")
            return False

    async def rest(self) -> bool:
        try:
            await self.bridge.rest()
            return True
        except Exception as exc:
            self.logger.error(f"rest failed: {exc}")
            return False

    async def set_eye_color(self, color: str) -> bool:
        try:
            await self.bridge.set_eye_leds(color=color)
            return True
        except Exception as exc:
            self.logger.error(f"set_eye_color failed: {exc}")
            return False

    async def set_chest_led(self, color: str) -> bool:
        try:
            await self.bridge.set_chest_leds(color=color)
            return True
        except Exception as exc:
            self.logger.error(f"set_chest_led failed: {exc}")
            return False

    async def play_animation(self, name: str) -> bool:
        try:
            await self.bridge.play_animation(name)
            return True
        except Exception as exc:
            self.logger.error(f"play_animation failed: {exc}")
            return False

    async def set_awareness(self, enabled: bool) -> bool:
        try:
            await self.bridge.set_awareness(enabled)
            return True
        except Exception as exc:
            self.logger.error(f"set_awareness failed: {exc}")
            return False

    async def set_autonomous_life(self, state: str) -> bool:
        try:
            await self.bridge.set_autonomous_life(state)
            return True
        except Exception as exc:
            self.logger.error(f"set_autonomous_life failed: {exc}")
            return False

    async def take_picture(self, camera: int = 0):
        try:
            return await self.bridge.take_picture(camera=camera)
        except Exception as exc:
            self.logger.error(f"take_picture failed: {exc}")
            return None

    async def record_audio(self, duration: float = 3.0):
        try:
            return await self.bridge.record_audio(duration)
        except Exception as exc:
            self.logger.error(f"record_audio failed: {exc}")
            return None
