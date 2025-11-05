"""
Bridge client for connecting to Pepper Bridge Service
Replaces direct NAOqi connection with HTTP API
"""

import requests
from typing import Optional, Dict, Any
from loguru import logger


class BridgeClient:
    """Client for Pepper Bridge Service running on robot"""
    
    def __init__(self, bridge_url: str = "http://10.0.100.100:8888"):
        """
        Initialize bridge client
        
        Args:
            bridge_url: URL of the bridge service (default: http://10.0.100.100:8888)
        """
        self.bridge_url = bridge_url.rstrip('/')
        self.logger = logger.bind(module="BridgeClient")
    
    def health_check(self) -> Dict[str, Any]:
        """Check bridge service health"""
        try:
            response = requests.get(f"{self.bridge_url}/health", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """Get robot status"""
        try:
            response = requests.get(f"{self.bridge_url}/status", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to get status: {e}")
            return {"error": str(e)}
    
    def get_sensor_data(self) -> Dict[str, Any]:
        """Get sensor data"""
        try:
            response = requests.get(f"{self.bridge_url}/sensors", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to get sensor data: {e}")
            return {"error": str(e)}
    
    def speak(self, text: str, language: str = "English") -> Dict[str, Any]:
        """Make robot speak"""
        try:
            # Bridge accepts both 'text' and 'message' - send both for compatibility
            response = requests.post(
                f"{self.bridge_url}/speak",
                json={"text": text, "message": text, "language": language},
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            self.logger.debug(f"Bridge speak result: {result}")
            return result
        except Exception as e:
            self.logger.error(f"Failed to make robot speak: {e}")
            return {"success": False, "error": str(e)}
    
    def move_forward(self, distance: float = 0.5) -> Dict[str, Any]:
        """Move robot forward"""
        try:
            response = requests.post(
                f"{self.bridge_url}/move/forward",
                json={"distance": distance},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to move robot: {e}")
            return {"success": False, "error": str(e)}
    
    def turn(self, angle: float = 90) -> Dict[str, Any]:
        """Turn robot"""
        try:
            response = requests.post(
                f"{self.bridge_url}/move/turn",
                json={"angle": angle},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to turn robot: {e}")
            return {"success": False, "error": str(e)}
    
    def stop_motion(self) -> Dict[str, Any]:
        """Stop all motion"""
        try:
            response = requests.post(
                f"{self.bridge_url}/stop",
                json={},
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to stop motion: {e}")
            return {"success": False, "error": str(e)}
    
    def is_connected(self) -> bool:
        """Check if bridge is accessible"""
        try:
            health = self.health_check()
            return health.get("status") == "healthy" and health.get("connected", False)
        except:
            return False
    
    def listen_for_speech(self, timeout: float = 5.0, language: str = "English") -> Dict[str, Any]:
        """Listen for speech and return transcribed text"""
        try:
            response = requests.post(
                f"{self.bridge_url}/listen",
                json={"timeout": timeout, "language": language},
                timeout=timeout + 2
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to listen for speech: {e}")
            return {"success": False, "error": str(e)}
    
    def play_animation(self, animation_name: str) -> Dict[str, Any]:
        """Play an animation on the robot"""
        try:
            response = requests.post(
                f"{self.bridge_url}/animation",
                json={"animation": animation_name},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to play animation: {e}")
            return {"success": False, "error": str(e)}
    
    def set_eye_color(self, color: str) -> Dict[str, Any]:
        """Set eye LED color"""
        try:
            response = requests.post(
                f"{self.bridge_url}/led/eyes",
                json={"color": color},
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to set eye color: {e}")
            return {"success": False, "error": str(e)}

