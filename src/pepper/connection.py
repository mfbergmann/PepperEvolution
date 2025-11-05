"""
Pepper robot connection management using NAOqi 2.5
Supports both direct NAOqi connection and bridge-based connection
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any
from dataclasses import dataclass

from loguru import logger

# Import qi with compatibility layer
try:
    from .qi_compat import qi, _using_mock
except ImportError:
    # Fallback if qi_compat not available
    try:
        import qi
        _using_mock = False
    except ImportError:
        raise ImportError(
            "NAOqi SDK (qi module) not found. "
            "Please install NAOqi Python SDK or use the compatibility layer."
        )

# Bridge URL default - will be read from env at runtime
DEFAULT_BRIDGE_URL = "http://10.0.100.100:8888"


@dataclass
class ConnectionConfig:
    """Configuration for Pepper robot connection"""
    ip: str
    port: int = 9559
    username: str = "nao"
    password: str = "nao"
    timeout: int = 30


class PepperConnection:
    """Manages connection to Pepper robot via NAOqi or Bridge"""
    
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.session: Optional[qi.Session] = None
        self.bridge_client = None
        self.connected = False
        self.logger = logger.bind(module="PepperConnection")
        
        # Read bridge mode from environment at runtime (after load_dotenv() has been called)
        # Default to true if not explicitly set to false
        USE_BRIDGE_ENV = os.getenv("USE_PEPPER_BRIDGE", "true")
        self.use_bridge = USE_BRIDGE_ENV.lower() != "false"  # true if not "false"
        self.bridge_url = os.getenv("PEPPER_BRIDGE_URL", DEFAULT_BRIDGE_URL)
        
        self.logger.info(f"Connection initialized: use_bridge={self.use_bridge}, USE_BRIDGE_ENV={USE_BRIDGE_ENV}, bridge_url={self.bridge_url}")
        
        # If using bridge, import bridge client
        if self.use_bridge:
            try:
                from .bridge_client import BridgeClient
                self.bridge_client = BridgeClient(self.bridge_url)
                self.logger.info("Using bridge connection mode")
            except Exception as e:
                self.logger.warning(f"Bridge client not available: {e}, falling back to direct connection")
                # Don't disable bridge mode - keep it enabled even if bridge client fails
                # The error will be handled at connection time
                # self.use_bridge = False  # REMOVED: Don't disable bridge mode
        
    async def connect(self) -> bool:
        """Establish connection to Pepper robot"""
        if self.use_bridge:
            return await self._connect_via_bridge()
        else:
            return await self._connect_direct()
    
    async def _connect_via_bridge(self) -> bool:
        """Connect via bridge service"""
        try:
            self.logger.info(f"Connecting to Pepper via bridge at {self.bridge_url}")
            
            # Check bridge health
            health = self.bridge_client.health_check()
            
            if health.get("status") == "healthy" and health.get("connected", False):
                self.connected = True
                robot_name = health.get("robot", "Unknown")
                self.logger.success(f"Successfully connected to Pepper robot via bridge: {robot_name}")
                return True
            else:
                self.logger.error("Bridge is not healthy or robot is not connected")
                return False
                
        except Exception as e:
            self.logger.error(f"Bridge connection error: {e}")
            self.connected = False
            return False
    
    async def _connect_direct(self) -> bool:
        """Establish direct connection to Pepper robot via NAOqi"""
        try:
            self.logger.info(f"Connecting to Pepper at {self.config.ip}:{self.config.port}")
            
            # Create session
            self.session = qi.Session()
            
            # Connect to robot
            self.session.connect(f"tcp://{self.config.ip}:{self.config.port}")
            
            if self.session.isConnected():
                self.connected = True
                self.logger.success("Successfully connected to Pepper robot")
                return True
            else:
                self.logger.error("Failed to connect to Pepper robot")
                return False
                
        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from Pepper robot"""
        if self.use_bridge:
            self.connected = False
            self.logger.info("Disconnected from Pepper robot (bridge)")
        else:
            if self.session and self.session.isConnected():
                self.session.close()
                self.connected = False
                self.logger.info("Disconnected from Pepper robot")
    
    def get_service(self, service_name: str):
        """Get a NAOqi service (only works with direct connection)"""
        if self.use_bridge:
            raise NotImplementedError(
                "get_service() not available with bridge connection. "
                "Use bridge_client methods instead."
            )
        
        if not self.connected or not self.session:
            raise ConnectionError("Not connected to Pepper robot")
        
        try:
            return self.session.service(service_name)
        except Exception as e:
            self.logger.error(f"Failed to get service {service_name}: {e}")
            raise
    
    def is_connected(self) -> bool:
        """Check if connected to robot"""
        if self.use_bridge:
            if not self.connected:
                return False
            # Verify connection is still alive
            try:
                health = self.bridge_client.health_check()
                return health.get("status") == "healthy" and health.get("connected", False)
            except:
                return False
        else:
            return self.connected and self.session and self.session.isConnected()
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on connection"""
        if self.use_bridge:
            try:
                health = self.bridge_client.health_check()
                if health.get("status") == "healthy":
                    status = self.bridge_client.get_status()
                    return {
                        "status": "connected",
                        "robot_name": status.get("robot_name", "Unknown"),
                        "bridge_url": BRIDGE_URL,
                        "battery": status.get("battery", 0),
                        "system_version": status.get("system_version", "Unknown")
                    }
                else:
                    return {"status": "disconnected", "error": "Bridge not healthy"}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        else:
            if not self.is_connected():
                return {"status": "disconnected", "error": "Not connected"}
            
            try:
                # Try to get a basic service to test connection
                system_service = self.get_service("ALSystem")
                robot_name = system_service.robotName()
                
                return {
                    "status": "connected",
                    "robot_name": robot_name,
                    "ip": self.config.ip,
                    "port": self.config.port
                }
            except Exception as e:
                return {
                    "status": "error",
                    "error": str(e)
                }
