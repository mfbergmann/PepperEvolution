"""
Pepper robot connection management using NAOqi 2.5
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
import qi

from loguru import logger


@dataclass
class ConnectionConfig:
    """Configuration for Pepper robot connection"""
    ip: str
    port: int = 9559
    username: str = "nao"
    password: str = "nao"
    timeout: int = 30


class PepperConnection:
    """Manages connection to Pepper robot via NAOqi"""
    
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.session: Optional[qi.Session] = None
        self.connected = False
        self.logger = logger.bind(module="PepperConnection")
        
    async def connect(self) -> bool:
        """Establish connection to Pepper robot"""
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
        if self.session and self.session.isConnected():
            self.session.close()
            self.connected = False
            self.logger.info("Disconnected from Pepper robot")
    
    def get_service(self, service_name: str):
        """Get a NAOqi service"""
        if not self.connected or not self.session:
            raise ConnectionError("Not connected to Pepper robot")
        
        try:
            return self.session.service(service_name)
        except Exception as e:
            self.logger.error(f"Failed to get service {service_name}: {e}")
            raise
    
    def is_connected(self) -> bool:
        """Check if connected to robot"""
        return self.connected and self.session and self.session.isConnected()
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on connection"""
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
