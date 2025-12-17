"""
Bridge client for connecting to Pepper Bridge Service
Replaces direct NAOqi connection with HTTP API (v1) and HTTP+WebSocket (v2)
"""

import os
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
        self.version: Optional[str] = None
        self.streams: Dict[str, str] = {}
    
    # --- Core endpoints ---
    def health_check(self) -> Dict[str, Any]:
        """Check bridge service health and cache version/streams if provided"""
        try:
            response = requests.get(f"{self.bridge_url}/health", timeout=5)
            response.raise_for_status()
            data = response.json()
            # Cache version and streams if available (v2)
            self.version = data.get("version", self.version)
            streams = data.get("streams")
            if isinstance(streams, dict):
                self.streams = streams
            return data
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
        """Get sensor data (v1 only)"""
        try:
            response = requests.get(f"{self.bridge_url}/sensors", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to get sensor data: {e}")
            return {"error": str(e)}
    
    def speak(self, text: str, language: str = "English", animated: bool = True) -> Dict[str, Any]:
        """Make robot speak"""
        try:
            # Bridge accepts both 'text' and 'message' - send both for compatibility
            response = requests.post(
                f"{self.bridge_url}/speak",
                json={"text": text, "message": text, "language": language, "animated": animated},
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
    
    def move_head(self, yaw: float, pitch: float, speed: float = 0.2) -> Dict[str, Any]:
        """Move head to angles (degrees) (v2)"""
        try:
            response = requests.post(
                f"{self.bridge_url}/move/head",
                json={"yaw": yaw, "pitch": pitch, "speed": speed},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to move head: {e}")
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
            connected = health.get("status") == "healthy"
            # v1 returns 'connected', v2 may not
            if "connected" in health:
                connected = connected and health.get("connected", False)
            return connected
        except Exception:
            return False
    
    # Speech (v1 only)
    def listen_for_speech(self, timeout: float = 5.0, language: str = "English") -> Dict[str, Any]:
        """Listen for speech and return transcribed text (v1)"""
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
    
    # Animations
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
    
    def list_animations(self) -> Dict[str, Any]:
        """List available animations (v2)"""
        try:
            response = requests.get(f"{self.bridge_url}/animations", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to list animations: {e}")
            return {"error": str(e)}
    
    # LEDs
    def set_eye_color(self, color: str) -> Dict[str, Any]:
        """Set eye LED color, compatible with v1 (/led/eyes) and v2 (/leds/eyes)"""
        try:
            # Prefer v2 path if we know it's v2
            if (self.version and str(self.version).startswith("2")):
                url = f"{self.bridge_url}/leds/eyes"
            else:
                url = f"{self.bridge_url}/led/eyes"
            
            response = requests.post(url, json={"color": color}, timeout=5)
            # If 404 on v1 path, retry v2 path
            if response.status_code == 404 and url.endswith("/led/eyes"):
                response = requests.post(f"{self.bridge_url}/leds/eyes", json={"color": color}, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to set eye color: {e}")
            return {"success": False, "error": str(e)}
    
    # Tablet (v2)
    def tablet_show_image(self, url: str) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/tablet/image", json={"url": url}, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to show image: {e}")
            return {"success": False, "error": str(e)}
    
    def tablet_show_web(self, url: str) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/tablet/web", json={"url": url}, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to show web: {e}")
            return {"success": False, "error": str(e)}
    
    def tablet_show_text(self, text: str, background: str = "#000000") -> Dict[str, Any]:
        try:
            resp = requests.post(
                f"{self.bridge_url}/tablet/text",
                json={"text": text, "background": background},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to show text: {e}")
            return {"success": False, "error": str(e)}
    
    def tablet_hide(self) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/tablet/hide", json={}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to hide tablet: {e}")
            return {"success": False, "error": str(e)}
    
    def tablet_set_brightness(self, brightness: int) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/tablet/brightness", json={"brightness": brightness}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to set tablet brightness: {e}")
            return {"success": False, "error": str(e)}
    
    # Awareness & system (v2)
    def set_awareness(self, enabled: bool) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/awareness", json={"enabled": bool(enabled)}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to set awareness: {e}")
            return {"success": False, "error": str(e)}
    
    def set_volume(self, volume: int) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/volume", json={"volume": int(volume)}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to set volume: {e}")
            return {"success": False, "error": str(e)}
    
    def wake_up(self) -> Dict[str, Any]:
        try:
            # v2 accepts /wake_up and v1 accepts /wakeup; try both
            resp = requests.post(f"{self.bridge_url}/wake_up", json={}, timeout=5)
            if resp.status_code == 404:
                resp = requests.post(f"{self.bridge_url}/wakeup", json={}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to wake up: {e}")
            return {"success": False, "error": str(e)}
    
    def rest(self) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.bridge_url}/rest", json={}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to rest: {e}")
            return {"success": False, "error": str(e)}
    
    # Camera (v2)
    def take_picture(self) -> Dict[str, Any]:
        """Take a single picture and return JSON (v2)"""
        try:
            resp = requests.get(f"{self.bridge_url}/picture", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to take picture: {e}")
            return {"success": False, "error": str(e)}

