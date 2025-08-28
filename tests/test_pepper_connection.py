"""
Tests for Pepper connection module
"""

import pytest
import asyncio
from unittest.mock import Mock, patch

# Import after the mock is set up in conftest.py
from src.pepper.connection import ConnectionConfig, PepperConnection


class TestConnectionConfig:
    """Test ConnectionConfig class"""
    
    def test_connection_config_defaults(self):
        """Test ConnectionConfig with default values"""
        config = ConnectionConfig(ip="192.168.1.100")
        
        assert config.ip == "192.168.1.100"
        assert config.port == 9559
        assert config.username == "nao"
        assert config.password == "nao"
        assert config.timeout == 30
    
    def test_connection_config_custom(self):
        """Test ConnectionConfig with custom values"""
        config = ConnectionConfig(
            ip="192.168.1.200",
            port=9559,
            username="custom_user",
            password="custom_pass",
            timeout=60
        )
        
        assert config.ip == "192.168.1.200"
        assert config.port == 9559
        assert config.username == "custom_user"
        assert config.password == "custom_pass"
        assert config.timeout == 60


class TestPepperConnection:
    """Test PepperConnection class"""
    
    @pytest.fixture
    def connection_config(self):
        """Create a test connection config"""
        return ConnectionConfig(ip="192.168.1.100")
    
    @pytest.fixture
    def connection(self, connection_config):
        """Create a test connection"""
        return PepperConnection(connection_config)
    
    def test_connection_initialization(self, connection):
        """Test connection initialization"""
        assert connection.config.ip == "192.168.1.100"
        assert connection.session is None
        assert connection.connected is False
    
    @pytest.mark.asyncio
    async def test_connection_success(self, connection):
        """Test successful connection"""
        # Mock qi.Session
        mock_session = Mock()
        mock_session.isConnected.return_value = True
        
        with patch('src.pepper.connection.qi.Session', return_value=mock_session):
            result = await connection.connect()
            
            assert result is True
            assert connection.connected is True
            assert connection.session == mock_session
    
    @pytest.mark.asyncio
    async def test_connection_failure(self, connection):
        """Test connection failure"""
        # Mock qi.Session that fails to connect
        mock_session = Mock()
        mock_session.isConnected.return_value = False
        
        with patch('src.pepper.connection.qi.Session', return_value=mock_session):
            result = await connection.connect()
            
            assert result is False
            assert connection.connected is False
    
    @pytest.mark.asyncio
    async def test_connection_exception(self, connection):
        """Test connection with exception"""
        with patch('src.pepper.connection.qi.Session', side_effect=Exception("Connection failed")):
            result = await connection.connect()
            
            assert result is False
            assert connection.connected is False
    
    @pytest.mark.asyncio
    async def test_disconnect(self, connection):
        """Test disconnection"""
        # Mock session
        mock_session = Mock()
        mock_session.isConnected.return_value = True
        connection.session = mock_session
        connection.connected = True
        
        await connection.disconnect()
        
        mock_session.close.assert_called_once()
        assert connection.connected is False
    
    def test_get_service_not_connected(self, connection):
        """Test getting service when not connected"""
        with pytest.raises(ConnectionError, match="Not connected to Pepper robot"):
            connection.get_service("ALMotion")
    
    def test_get_service_success(self, connection):
        """Test getting service successfully"""
        # Mock session and service
        mock_service = Mock()
        mock_session = Mock()
        mock_session.service.return_value = mock_service
        
        connection.session = mock_session
        connection.connected = True
        
        result = connection.get_service("ALMotion")
        
        assert result == mock_service
        mock_session.service.assert_called_once_with("ALMotion")
    
    def test_get_service_exception(self, connection):
        """Test getting service with exception"""
        # Mock session that raises exception
        mock_session = Mock()
        mock_session.service.side_effect = Exception("Service not found")
        
        connection.session = mock_session
        connection.connected = True
        
        with pytest.raises(Exception, match="Service not found"):
            connection.get_service("ALMotion")
    
    def test_is_connected_true(self, connection):
        """Test is_connected when connected"""
        mock_session = Mock()
        mock_session.isConnected.return_value = True
        
        connection.session = mock_session
        connection.connected = True
        
        assert connection.is_connected() is True
    
    def test_is_connected_false(self, connection):
        """Test is_connected when not connected"""
        assert connection.is_connected() is False
        
        # Test with session but not connected
        mock_session = Mock()
        mock_session.isConnected.return_value = False
        
        connection.session = mock_session
        connection.connected = True
        
        assert connection.is_connected() is False
    
    @pytest.mark.asyncio
    async def test_health_check_connected(self, connection):
        """Test health check when connected"""
        # Mock session and system service
        mock_system_service = Mock()
        mock_system_service.robotName.return_value = "Pepper"
        
        mock_session = Mock()
        mock_session.service.return_value = mock_system_service
        
        connection.session = mock_session
        connection.connected = True
        
        result = await connection.health_check()
        
        assert result["status"] == "connected"
        assert result["robot_name"] == "Pepper"
        assert result["ip"] == "192.168.1.100"
        assert result["port"] == 9559
    
    @pytest.mark.asyncio
    async def test_health_check_disconnected(self, connection):
        """Test health check when disconnected"""
        result = await connection.health_check()
        
        assert result["status"] == "disconnected"
        assert "error" in result
    
    @pytest.mark.asyncio
    async def test_health_check_error(self, connection):
        """Test health check with error"""
        # Mock session that raises exception
        mock_session = Mock()
        mock_session.service.side_effect = Exception("Service error")
        
        connection.session = mock_session
        connection.connected = True
        
        result = await connection.health_check()
        
        assert result["status"] == "error"
        assert "error" in result
