"""
Bridge-based connection to Pepper robot
Uses HTTP API instead of direct NAOqi connection
"""

import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass

from loguru import logger
from .bridge_client import BridgeClient


@dataclass
class BridgeConnectionConfig:
    """Configuration for bridge-based connection"""
    bridge_url: str = "http://10.0.100.100:8888"
    timeout: int = 30


class BridgeConnection:
    """Manages connection to Pepper robot via Bridge Service"""
    
    def __init__(self, config: BridgeConnectionConfig):
        self.config = config
        self.bridge_client = BridgeClient(config.bridge_url)
        self.connected = False
        self.logger = logger.bind(module="BridgeConnection")
        
    async def connect(self) -> bool:
        """Establish connection to Pepper robot via bridge"""
        try:
            self.logger.info(f"Connecting to Pepper via bridge at {self.config.bridge_url}")
            
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
            self.logger.error(f"Connection error: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from Pepper robot"""
        self.connected = False
        self.logger.info("Disconnected from Pepper robot")
    
    def get_service(self, service_name: str):
        """
        Get a service proxy (for compatibility with direct NAOqi interface)
        Note: Bridge connection doesn't expose services directly.
        Use bridge_client methods instead.
        """
        raise NotImplementedError(
            "Bridge connection doesn't expose services directly. "
            "Use bridge_client methods or wrap service calls."
        )
    
    def is_connected(self) -> bool:
        """Check if connected to robot"""
        if not self.connected:
            return False
        # Verify connection is still alive
        try:
            health = self.bridge_client.health_check()
            return health.get("status") == "healthy" and health.get("connected", False)
        except:
            return False
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on connection"""
        try:
            health = self.bridge_client.health_check()
            if health.get("status") == "healthy":
                status = self.bridge_client.get_status()
                return {
                    "status": "connected",
                    "robot_name": status.get("robot_name", "Unknown"),
                    "bridge_url": self.config.bridge_url,
                    "battery": status.get("battery", 0),
                    "system_version": status.get("system_version", "Unknown")
                }
            else:
                return {"status": "disconnected", "error": "Bridge not healthy"}
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

