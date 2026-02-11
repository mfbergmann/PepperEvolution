"""
Sensor manager - delegates to the bridge for all sensor data.
"""

from typing import Any, Dict

from loguru import logger

from ..pepper.connection import PepperConnection


class SensorManager:
    """Reads sensor data via the bridge HTTP API."""

    def __init__(self, connection: PepperConnection):
        self.connection = connection
        self.logger = logger.bind(module="SensorManager")

    async def get_all(self) -> Dict[str, Any]:
        """Get aggregated sensor snapshot from the bridge."""
        try:
            data = await self.connection.bridge.get_sensors()
            return {
                "battery": data.get("battery"),
                "touch": data.get("touch", {}),
                "sonar": data.get("sonar", {}),
                "people_count": data.get("people_count"),
            }
        except Exception as exc:
            self.logger.error(f"Failed to get sensors: {exc}")
            return {"error": str(exc)}

    async def get_battery(self) -> float:
        try:
            data = await self.connection.bridge.get_sensors()
            return float(data.get("battery") or 0)
        except Exception:
            return 0.0

    async def get_touch(self) -> Dict[str, bool]:
        try:
            data = await self.connection.bridge.get_sensors()
            return data.get("touch", {})
        except Exception:
            return {}

    async def get_sonar(self) -> Dict[str, Any]:
        try:
            data = await self.connection.bridge.get_sensors()
            return data.get("sonar", {})
        except Exception:
            return {}
