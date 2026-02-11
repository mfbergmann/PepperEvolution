"""
Tests for SensorManager.
"""

import pytest
from unittest.mock import AsyncMock

from src.sensors.manager import SensorManager
from src.pepper.bridge_client import BridgeError


class TestSensorManager:

    @pytest.fixture
    def sensor_manager(self, mock_connection):
        return SensorManager(mock_connection)

    @pytest.mark.asyncio
    async def test_get_all(self, sensor_manager):
        result = await sensor_manager.get_all()
        assert result["battery"] == 80
        assert "touch" in result
        assert "sonar" in result

    @pytest.mark.asyncio
    async def test_get_battery(self, sensor_manager):
        result = await sensor_manager.get_battery()
        assert result == 80.0

    @pytest.mark.asyncio
    async def test_get_touch(self, sensor_manager):
        result = await sensor_manager.get_touch()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_sonar(self, sensor_manager):
        result = await sensor_manager.get_sonar()
        assert "left" in result
        assert "right" in result

    @pytest.mark.asyncio
    async def test_get_all_error(self, sensor_manager):
        sensor_manager.connection.bridge.get_sensors = AsyncMock(side_effect=BridgeError("fail"))
        result = await sensor_manager.get_all()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_battery_error(self, sensor_manager):
        sensor_manager.connection.bridge.get_sensors = AsyncMock(side_effect=BridgeError("fail"))
        result = await sensor_manager.get_battery()
        assert result == 0.0
