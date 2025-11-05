"""
Sensor manager for Pepper robot - handles all sensor data collection
"""

import asyncio
from typing import Optional, Dict, Any, List
import numpy as np
import cv2

from loguru import logger

from ..pepper.connection import PepperConnection


class SensorManager:
    """Manages all Pepper robot sensors"""
    
    def __init__(self, connection: PepperConnection):
        self.connection = connection
        self.logger = logger.bind(module="SensorManager")
        
        # NAOqi services
        self.memory_service = None
        self.camera_service = None
        self.audio_service = None
        self.sonar_service = None
        self.touch_service = None
        self.battery_service = None
        self.system_service = None
        
        # Sensor data storage
        self._latest_camera_frame = None
        self._latest_audio_data = None
        self._latest_touch_data = {}
        self._latest_sonar_data = {}
        
    async def initialize(self):
        """Initialize all sensor services"""
        try:
            self.logger.info("Initializing sensor services...")
            
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                self.logger.info("Using bridge mode - sensors will be accessed via bridge")
                # Bridge mode doesn't need service initialization
                self.logger.success("Sensor services initialized (bridge mode)")
                return
            
            # Get NAOqi services (direct connection)
            self.memory_service = self.connection.get_service("ALMemory")
            self.camera_service = self.connection.get_service("ALVideoDevice")
            self.audio_service = self.connection.get_service("ALAudioDevice")
            self.sonar_service = self.connection.get_service("ALSonar")
            self.touch_service = self.connection.get_service("ALTouch")
            self.battery_service = self.connection.get_service("ALBattery")
            self.system_service = self.connection.get_service("ALSystem")
            
            # Subscribe to sensor events
            await self._subscribe_to_events()
            
            self.logger.success("Sensor services initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize sensors: {e}")
            raise
    
    async def shutdown(self):
        """Shutdown sensor services"""
        try:
            # Unsubscribe from events
            if self.memory_service:
                self.memory_service.unsubscribeToEvent("TouchChanged", "SensorManager")
                self.memory_service.unsubscribeToEvent("SonarLeftDetected", "SensorManager")
                self.memory_service.unsubscribeToEvent("SonarRightDetected", "SensorManager")
            
            self.logger.info("Sensor services shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Error during sensor shutdown: {e}")
    
    async def _subscribe_to_events(self):
        """Subscribe to sensor events"""
        try:
            # Subscribe to touch events
            self.memory_service.subscribeToEvent("TouchChanged", "SensorManager", self._on_touch_event)
            
            # Subscribe to sonar events
            self.memory_service.subscribeToEvent("SonarLeftDetected", "SensorManager", self._on_sonar_event)
            self.memory_service.subscribeToEvent("SonarRightDetected", "SensorManager", self._on_sonar_event)
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to events: {e}")
    
    def _on_touch_event(self, event_name: str, value: Any, subscriber_identifier: str):
        """Handle touch sensor events"""
        try:
            self._latest_touch_data = {
                "event": event_name,
                "value": value,
                "timestamp": asyncio.get_event_loop().time()
            }
        except Exception as e:
            self.logger.error(f"Error handling touch event: {e}")
    
    def _on_sonar_event(self, event_name: str, value: Any, subscriber_identifier: str):
        """Handle sonar sensor events"""
        try:
            self._latest_sonar_data = {
                "event": event_name,
                "value": value,
                "timestamp": asyncio.get_event_loop().time()
            }
        except Exception as e:
            self.logger.error(f"Error handling sonar event: {e}")
    
    # Camera methods
    
    async def take_photo(self) -> Optional[np.ndarray]:
        """Take a photo using Pepper's camera"""
        try:
            # Get image from camera
            image_data = self.camera_service.getImageRemote("pepper_camera")
            
            if image_data:
                # Convert to numpy array
                width = image_data[0]
                height = image_data[1]
                channels = image_data[2]
                image_array = np.frombuffer(image_data[6], dtype=np.uint8)
                image_array = image_array.reshape((height, width, channels))
                
                # Convert BGR to RGB
                image_rgb = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
                
                self._latest_camera_frame = image_rgb
                return image_rgb
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to take photo: {e}")
            return None
    
    async def get_camera_frame(self) -> Optional[np.ndarray]:
        """Get the latest camera frame"""
        return self._latest_camera_frame
    
    # Audio methods
    
    async def listen_for_speech(self, timeout: float = 5.0) -> Optional[str]:
        """Listen for speech input and return transcribed text"""
        try:
            self.logger.info(f"Listening for speech (timeout: {timeout}s)")
            
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                if self.connection.bridge_client:
                    result = self.connection.bridge_client.listen_for_speech(timeout, "English")
                    if result.get("success") and result.get("text"):
                        return result.get("text")
                    else:
                        self.logger.warning(f"Speech recognition failed: {result.get('error', 'No speech detected')}")
                        return None
                else:
                    self.logger.error("Bridge client not available")
                    return None
            
            # Direct connection mode (would use NAOqi service directly)
            # This is not implemented yet for direct mode
            self.logger.warning("Speech recognition not implemented for direct connection mode")
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to listen for speech: {e}")
            return None
    
    async def get_audio_level(self) -> float:
        """Get current audio input level"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                # Audio level not available via bridge yet - return default
                return 0.0
            
            # Direct connection mode
            if not self.audio_service:
                return 0.0
            
            # Get audio level from NAOqi
            audio_level = self.audio_service.getFrontMicEnergy()
            return float(audio_level)
            
        except Exception as e:
            self.logger.warning(f"Failed to get audio level: {e}")
            return 0.0
    
    # Touch sensor methods
    
    async def get_touch_data(self) -> Dict[str, Any]:
        """Get current touch sensor data"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                # Touch sensors not available via bridge yet - return default
                return {"touched": False, "head_touched": False, "hand_touched": False, "timestamp": asyncio.get_event_loop().time()}
            
            # Direct connection mode
            if not self.memory_service:
                return {"touched": False, "error": "Memory service not available"}
            
            # Get touch sensor values
            head_touch = self.memory_service.getData("Device/SubDeviceList/Head/Touch/Sensor/Value")
            hand_touch = self.memory_service.getData("Device/SubDeviceList/Hand/Left/Touch/Sensor/Value")
            
            touch_data = {
                "head_touched": bool(head_touch),
                "hand_touched": bool(hand_touch),
                "touched": bool(head_touch or hand_touch),
                "timestamp": asyncio.get_event_loop().time()
            }
            
            # Update latest data
            self._latest_touch_data.update(touch_data)
            
            return self._latest_touch_data
            
        except Exception as e:
            self.logger.warning(f"Failed to get touch data: {e}")
            return {"touched": False, "error": str(e)}
    
    # Sonar methods
    
    async def get_sonar_data(self) -> Dict[str, Any]:
        """Get current sonar sensor data"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                # Sonar not available via bridge yet - return default
                return {"obstacle_detected": False, "left_distance": 1.0, "right_distance": 1.0, "timestamp": asyncio.get_event_loop().time()}
            
            # Direct connection mode
            if not self.memory_service:
                return {"obstacle_detected": False, "error": "Memory service not available"}
            
            # Get sonar distances
            left_distance = self.memory_service.getData("Device/SubDeviceList/US/Left/Sensor/Value")
            right_distance = self.memory_service.getData("Device/SubDeviceList/US/Right/Sensor/Value")
            
            sonar_data = {
                "left_distance": float(left_distance),
                "right_distance": float(right_distance),
                "obstacle_detected": float(left_distance) < 0.5 or float(right_distance) < 0.5,
                "timestamp": asyncio.get_event_loop().time()
            }
            
            # Update latest data
            self._latest_sonar_data.update(sonar_data)
            
            return self._latest_sonar_data
            
        except Exception as e:
            self.logger.warning(f"Failed to get sonar data: {e}")
            return {"obstacle_detected": False, "error": str(e)}
    
    # System sensors
    
    async def get_battery_level(self) -> float:
        """Get current battery level percentage"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                if self.connection.bridge_client:
                    status = self.connection.bridge_client.get_status()
                    return float(status.get("battery", 0.0))
                else:
                    return 0.0
            
            # Direct connection mode
            battery_level = self.battery_service.getBatteryCharge()
            return float(battery_level)
            
        except Exception as e:
            self.logger.error(f"Failed to get battery level: {e}")
            return 0.0
    
    async def get_temperature(self) -> float:
        """Get current robot temperature"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                # Temperature not available via bridge yet - return default
                return 25.0  # Default room temperature
            
            # Direct connection mode
            if not self.memory_service:
                return 0.0
            
            cpu_temp = self.memory_service.getData("Device/SubDeviceList/ChestBoard/Button/Sensor/Value")
            return float(cpu_temp)
            
        except Exception as e:
            self.logger.warning(f"Failed to get temperature: {e}")
            return 0.0
    
    async def get_autonomy_state(self) -> bool:
        """Get current autonomy state"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                # Autonomy state not available via bridge - assume disabled
                return False
            
            # Direct connection mode
            if not self.memory_service:
                return False
            
            autonomy_state = self.memory_service.getData("AutonomousLife/State")
            return autonomy_state == "solitary"
            
        except Exception as e:
            self.logger.warning(f"Failed to get autonomy state: {e}")
            return False
    
    # Combined sensor data
    
    async def get_all_sensor_data(self) -> Dict[str, Any]:
        """Get all sensor data in one call"""
        try:
            return {
                "camera": await self.get_camera_frame(),
                "touch": await self.get_touch_data(),
                "sonar": await self.get_sonar_data(),
                "battery": await self.get_battery_level(),
                "temperature": await self.get_temperature(),
                "audio_level": await self.get_audio_level(),
                "autonomy_state": await self.get_autonomy_state(),
                "timestamp": asyncio.get_event_loop().time()
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get all sensor data: {e}")
            return {"error": str(e)}
