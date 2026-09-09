"""
Tests for PepperConnection - bridge connection management.
"""

from unittest.mock import AsyncMock


from src.pepper.bridge_client import BridgeClient, BridgeError
from src.pepper.connection import ConnectionConfig, PepperConnection
from src.pepper.fake_bridge import FakeBridgeClient


class TestConnectionConfig:

    def test_defaults(self):
        config = ConnectionConfig(ip="10.0.100.100")
        assert config.bridge_port == 8888
        assert config.api_key == ""
        assert config.timeout == 15.0
        assert config.action_timeout == 120.0

    def test_urls(self):
        config = ConnectionConfig(ip="10.0.100.100", bridge_port=8888)
        assert config.base_url == "http://10.0.100.100:8888"
        assert config.ws_url == "ws://10.0.100.100:8888/ws/events"


class TestPepperConnection:

    def test_initialization(self, connection_config):
        conn = PepperConnection(connection_config)
        assert conn.connected is False
        assert isinstance(conn.bridge, BridgeClient)
        assert conn.bridge.action_timeout == 120.0

    def test_injected_bridge(self, connection_config):
        fake = FakeBridgeClient()
        conn = PepperConnection(connection_config, bridge=fake)
        assert conn.bridge is fake

    async def test_connect_with_fake_bridge_skips_event_stream(self, connection_config):
        conn = PepperConnection(connection_config, bridge=FakeBridgeClient())
        assert await conn.connect() is True
        assert conn.connected and conn.bridge_info["robot_name"] == "FakePepper"
        assert conn.events._task is None
        await conn.disconnect()
        assert conn.connected is False

    async def test_connect_failure(self, connection_config):
        conn = PepperConnection(connection_config)
        conn.bridge.connect = AsyncMock()
        conn.bridge.health = AsyncMock(side_effect=BridgeError("unreachable"))
        assert await conn.connect() is False
        assert conn.connected is False

    async def test_health_check_disconnected(self, connection_config):
        conn = PepperConnection(connection_config)
        assert (await conn.health_check())["status"] == "disconnected"

    async def test_health_check_connected(self, mock_connection):
        result = await mock_connection.health_check()
        assert result["status"] == "connected" and result["version"] == "2.1.0"

    async def test_health_check_error(self, mock_connection):
        mock_connection.bridge.health = AsyncMock(side_effect=BridgeError("timeout"))
        result = await mock_connection.health_check()
        assert result["status"] == "error" and "timeout" in result["error"]

    async def test_disconnect(self, mock_connection):
        await mock_connection.disconnect()
        assert mock_connection.connected is False
        mock_connection.bridge.close.assert_called_once()
