"""
Main Pepper robot interface providing high-level control methods.

Delegates all hardware interaction to the bridge via BridgeClient.
"""

import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional
from dataclasses import dataclass, field

from loguru import logger

from .connection import PepperConnection, ConnectionConfig
from ..sensors import SensorManager
from ..actuators import ActuatorManager


@dataclass
class RobotState:
    """Current state of the Pepper robot."""
    battery_level: float = 0.0
    posture: str = "unknown"
    robot_name: str = "Pepper"
    autonomous_life: str = "unknown"
    is_connected: bool = False


class PepperRobot:
    """Main interface for controlling Pepper robot."""

    def __init__(self, connection_config: ConnectionConfig):
        self.connection = PepperConnection(connection_config)
        self.sensors = SensorManager(self.connection)
        self.actuators = ActuatorManager(self.connection)
        self.state = RobotState()
        self.logger = logger.bind(module="PepperRobot")

        self._event_callbacks: List[Callable[[str, Dict[str, Any]], Coroutine]] = []

    async def initialize(self) -> bool:
        """Initialize the robot and all subsystems."""
        try:
            self.logger.info("Initializing Pepper robot...")
            if not await self.connection.connect():
                return False

            # Register for all bridge events
            self.connection.events.on_any(self._on_bridge_event)

            await self._update_state()
            self.logger.success("Pepper robot initialized successfully")
            return True
        except Exception as exc:
            self.logger.error(f"Failed to initialize robot: {exc}")
            return False

    async def shutdown(self):
        """Shutdown the robot and clean up resources."""
        self.logger.info("Shutting down Pepper robot...")
        await self.connection.disconnect()
        self.logger.info("Pepper robot shutdown complete")

    async def _update_state(self):
        """Update robot state from bridge."""
        try:
            data = await self.connection.bridge.status()
            self.state.battery_level = data.get("battery") or 0.0
            self.state.posture = data.get("posture", "unknown")
            self.state.robot_name = data.get("robot_name", "Pepper")
            self.state.autonomous_life = data.get("autonomous_life", "unknown")
            self.state.is_connected = self.connection.is_connected()
        except Exception as exc:
            self.logger.warning(f"Failed to update state: {exc}")

    # ------------------------------------------------------------------
    # High-level control
    # ------------------------------------------------------------------

    async def speak(self, text: str, language: Optional[str] = None, animated: bool = False) -> bool:
        try:
            await self.connection.bridge.speak(text, language=language, animated=animated)
            return True
        except Exception as exc:
            self.logger.error(f"speak failed: {exc}")
            return False

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> bool:
        try:
            await self.connection.bridge.move_forward(distance, speed)
            return True
        except Exception as exc:
            self.logger.error(f"move_forward failed: {exc}")
            return False

    async def turn(self, angle: float) -> bool:
        try:
            await self.connection.bridge.move_turn(angle)
            return True
        except Exception as exc:
            self.logger.error(f"turn failed: {exc}")
            return False

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> bool:
        try:
            await self.connection.bridge.move_head(yaw, pitch, speed)
            return True
        except Exception as exc:
            self.logger.error(f"move_head failed: {exc}")
            return False

    async def set_posture(self, posture: str, speed: float = 0.5) -> bool:
        try:
            await self.connection.bridge.set_posture(posture, speed)
            return True
        except Exception as exc:
            self.logger.error(f"set_posture failed: {exc}")
            return False

    async def take_picture(self, camera: int = 0) -> Optional[Dict[str, Any]]:
        """Take a photo. Returns dict with 'image' (base64), 'width', 'height'."""
        try:
            return await self.connection.bridge.take_picture(camera=camera)
        except Exception as exc:
            self.logger.error(f"take_picture failed: {exc}")
            return None

    async def play_animation(self, name: str) -> bool:
        try:
            await self.connection.bridge.play_animation(name)
            return True
        except Exception as exc:
            self.logger.error(f"play_animation failed: {exc}")
            return False

    async def set_eye_color(self, color: str) -> bool:
        try:
            await self.connection.bridge.set_eye_leds(color=color)
            return True
        except Exception as exc:
            self.logger.error(f"set_eye_color failed: {exc}")
            return False

    async def emergency_stop(self):
        self.logger.warning("Emergency stop activated!")
        try:
            await self.connection.bridge.emergency_stop()
        except Exception as exc:
            self.logger.error(f"emergency_stop failed: {exc}")

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

    async def start_event_loop(self):
        """Periodically refresh state while connected."""
        self.logger.info("Starting state refresh loop...")
        while self.connection.is_connected():
            try:
                await self._update_state()
            except Exception as exc:
                self.logger.error(f"State refresh error: {exc}")
            await asyncio.sleep(5)
