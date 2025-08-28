"""
Pytest configuration and fixtures for PepperEvolution tests
"""

import pytest
import sys
from unittest.mock import MagicMock

# Mock NAOqi modules that aren't available in CI
class MockQi:
    class Session:
        def __init__(self):
            self.connected = False
        
        def connect(self, url):
            self.connected = True
            return True
        
        def isConnected(self):
            return self.connected
        
        def close(self):
            self.connected = False
        
        def service(self, service_name):
            return MagicMock()

# Add mock to sys.modules
sys.modules['qi'] = MockQi()

@pytest.fixture
def mock_pepper_connection():
    """Mock Pepper connection for testing"""
    from src.pepper.connection import ConnectionConfig, PepperConnection
    
    config = ConnectionConfig(ip="192.168.1.100")
    connection = PepperConnection(config)
    
    # Mock the session
    connection.session = MagicMock()
    connection.session.isConnected.return_value = True
    connection.connected = True
    
    return connection

@pytest.fixture
def mock_ai_provider():
    """Mock AI provider for testing"""
    from src.ai.models import OpenAIProvider
    
    provider = OpenAIProvider("mock_api_key", "gpt-4")
    provider.client = MagicMock()
    
    return provider
