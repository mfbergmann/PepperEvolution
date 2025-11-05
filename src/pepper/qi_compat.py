"""
Compatibility layer for NAOqi SDK
Handles cases where the SDK is not available (e.g., macOS without Linux binaries)
"""

import sys
from typing import Optional
from unittest.mock import MagicMock

# Try to import the real qi module
# First try the macOS SDK if available
qi: Optional[object] = None
_using_mock = False

# Check for macOS SDK in naoqi-mac directory
import os
import sys
from pathlib import Path

_naoqi_mac_path = Path(__file__).parent.parent.parent / "naoqi-mac" / "pynaoqi-python2.7-2.1.4.13-mac64"
if _naoqi_mac_path.exists():
    # Add to path if macOS SDK exists
    sys.path.insert(0, str(_naoqi_mac_path))
    os.environ.setdefault('DYLD_LIBRARY_PATH', '')
    if str(_naoqi_mac_path) not in os.environ.get('DYLD_LIBRARY_PATH', ''):
        os.environ['DYLD_LIBRARY_PATH'] = f"{_naoqi_mac_path}:{os.environ.get('DYLD_LIBRARY_PATH', '')}"

try:
    import qi as _qi_module
    qi = _qi_module
    _using_mock = False
except (ImportError, OSError) as e:
    # SDK not available - create a mock
    _using_mock = True
    
    class MockSession:
        """Mock qi.Session for development/testing"""
        def __init__(self):
            self._connected = False
            self._url = None
        
        def connect(self, url: str):
            """Connect to robot (mock)"""
            self._url = url
            self._connected = True
            return True
        
        def isConnected(self) -> bool:
            """Check if connected (mock)"""
            return self._connected
        
        def close(self):
            """Close connection (mock)"""
            self._connected = False
        
        def service(self, service_name: str):
            """Get a service (mock)"""
            mock_service = MagicMock()
            # Add common methods
            if service_name == "ALSystem":
                mock_service.robotName.return_value = "Pepper (Mock)"
            return mock_service
    
    class MockQi:
        """Mock qi module"""
        Session = MockSession
        
        @staticmethod
        def Application(*args, **kwargs):
            return MagicMock()
        
        @staticmethod
        def ApplicationSession(*args, **kwargs):
            return MagicMock()
    
    # Create mock module
    qi = MockQi()
    
    # Set it in sys.modules so other imports work
    sys.modules['qi'] = qi
    
    import warnings
    warnings.warn(
        "NAOqi SDK not available (likely Linux-only binaries on macOS). "
        "Using mock implementation. Robot control will be simulated.",
        UserWarning
    )

__all__ = ['qi', '_using_mock']

