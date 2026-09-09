"""
Tests for SensorManager.
"""

from unittest.mock import AsyncMock

import pytest

from src.pepper.bridge_client import BridgeError
from src.sensors.manager import SensorManager


class TestSensorManager:

    @pytest.fixture
    def sensor_manager(self, mock_connection):
        return SensorManager(mock_connection)

    async def test_get_all(self, sensor_manager):
        result = await sensor_manager.get_all()
        assert result["battery"] == 80
        assert result["sonar"] == {"front": 1.5, "back": 1.2}
        assert result["obstacle"] is False
        assert "bumpers" in result and "touch" in result

    async def test_get_battery(self, sensor_manager):
        assert await sensor_manager.get_battery() == 80.0

    async def test_get_touch_and_sonar(self, sensor_manager):
        assert isinstance(await sensor_manager.get_touch(), dict)
        assert "front" in await sensor_manager.get_sonar()

    async def test_errors_are_soft(self, sensor_manager):
        sensor_manager.connection.bridge.get_sensors = AsyncMock(side_effect=BridgeError("fail"))
        assert "error" in await sensor_manager.get_all()
        assert await sensor_manager.get_battery() == 0.0
        assert await sensor_manager.get_touch() == {}
