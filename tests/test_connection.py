"""
Tests for PepperConnection - bridge connection management.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.pepper.connection import ConnectionConfig, PepperConnection
from src.pepper.bridge_client import BridgeClient, BridgeError


class TestConnectionConfig:

    def test_defaults(self):
        config = ConnectionConfig(ip="10.0.100.100")
        assert config.ip == "10.0.100.100"
        assert config.bridge_port == 8888
        assert config.api_key == ""
        assert config.timeout == 15.0

    def test_custom(self):
        config = ConnectionConfig(ip="192.168.1.1", bridge_port=9999, api_key="key", timeout=30.0)
        assert config.bridge_port == 9999
        assert config.api_key == "key"

    def test_base_url(self):
        config = ConnectionConfig(ip="10.0.100.100", bridge_port=8888)
        assert config.base_url == "http://10.0.100.100:8888"

    def test_ws_url(self):
        config = ConnectionConfig(ip="10.0.100.100", bridge_port=8888)
        assert config.ws_url == "ws://10.0.100.100:8888/ws/events"


class TestPepperConnection:

    def test_initialization(self, connection_config):
        conn = PepperConnection(connection_config)
        assert conn.connected is False
        assert isinstance(conn.bridge, BridgeClient)

    @pytest.mark.asyncio
    async def test_health_check_disconnected(self, connection_config):
        conn = PepperConnection(connection_config)
        result = await conn.health_check()
        assert result["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_health_check_connected(self, mock_connection):
        mock_connection.bridge.health = AsyncMock(return_value={"ok": True, "version": "2.0.0"})
        result = await mock_connection.health_check()
        assert result["status"] == "connected"
        assert result["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_health_check_error(self, mock_connection):
        mock_connection.bridge.health = AsyncMock(side_effect=BridgeError("timeout"))
        result = await mock_connection.health_check()
        assert result["status"] == "error"
        assert "timeout" in result["error"]

    def test_is_connected(self, mock_connection):
        assert mock_connection.is_connected() is True
        mock_connection.connected = False
        assert mock_connection.is_connected() is False

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_connection):
        await mock_connection.disconnect()
        assert mock_connection.connected is False
        mock_connection.bridge.close.assert_called_once()
