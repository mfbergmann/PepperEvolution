"""
Main Pepper robot interface providing high-level control methods
"""

import asyncio
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
import numpy as np

from loguru import logger

from .connection import PepperConnection, ConnectionConfig
from ..sensors import SensorManager
from ..actuators import ActuatorManager


@dataclass
class RobotState:
    """Current state of the Pepper robot"""
    battery_level: float = 0.0
    temperature: float = 0.0
    is_autonomous: bool = False
    is_connected: bool = False
    current_pose: str = "unknown"
    language: str = "en"


class PepperRobot:
    """Main interface for controlling Pepper robot"""
    
    def __init__(self, connection_config: ConnectionConfig):
        self.connection = PepperConnection(connection_config)
        self.sensors = SensorManager(self.connection)
        self.actuators = ActuatorManager(self.connection)
        self.state = RobotState()
        self.logger = logger.bind(module="PepperRobot")
        
        # Event callbacks
        self._sensor_callbacks: List[Callable] = []
        self._touch_callbacks: List[Callable] = []
        self._speech_callbacks: List[Callable] = []
        
    async def initialize(self) -> bool:
        """Initialize the robot and all subsystems"""
        try:
            self.logger.info("Initializing Pepper robot...")
            
            # Connect to robot
            if not await self.connection.connect():
                return False
            
            # Disable autonomous mode if not using bridge (bridge handles this)
            if not (hasattr(self.connection, 'use_bridge') and self.connection.use_bridge):
                try:
                    await self.disable_autonomous_mode()
                except Exception as e:
                    self.logger.warning(f"Could not disable autonomous mode: {e}")
            
            # Initialize sensors and actuators
            await self.sensors.initialize()
            await self.actuators.initialize()
            
            # Update initial state
            await self._update_state()
            
            self.logger.success("Pepper robot initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize robot: {e}")
            return False
    
    async def shutdown(self):
        """Shutdown the robot and clean up resources"""
        self.logger.info("Shutting down Pepper robot...")
        
        # Stop all ongoing activities
        await self.actuators.stop_all()
        
        # Disconnect sensors
        await self.sensors.shutdown()
        
        # Disconnect from robot
        await self.connection.disconnect()
        
        self.logger.info("Pepper robot shutdown complete")
    
    async def _update_state(self):
        """Update robot state information"""
        try:
            # Get battery level
            self.state.battery_level = await self.sensors.get_battery_level()
            
            # Get temperature
            self.state.temperature = await self.sensors.get_temperature()
            
            # Get autonomy state
            self.state.is_autonomous = await self.sensors.get_autonomy_state()
            
            # Update connection state
            self.state.is_connected = self.connection.is_connected()
            
        except Exception as e:
            self.logger.warning(f"Failed to update state: {e}")
    
    # High-level control methods
    
    async def speak(self, text: str, language: str = "en") -> bool:
        """Make Pepper speak text"""
        return await self.actuators.speak(text, language)
    
    async def listen(self, timeout: float = 5.0) -> Optional[str]:
        """Listen for speech input"""
        return await self.sensors.listen_for_speech(timeout)
    
    async def move_forward(self, distance: float = 0.5) -> bool:
        """Move forward by specified distance"""
        return await self.actuators.move_forward(distance)
    
    async def turn(self, angle: float) -> bool:
        """Turn by specified angle (degrees)"""
        return await self.actuators.turn(angle)
    
    async def wave_hand(self) -> bool:
        """Perform a waving gesture"""
        return await self.actuators.wave_hand()
    
    async def point_at(self, x: float, y: float, z: float) -> bool:
        """Point at a specific location"""
        return await self.actuators.point_at(x, y, z)
    
    async def take_photo(self) -> Optional[np.ndarray]:
        """Take a photo and return the image"""
        return await self.sensors.take_photo()
    
    async def get_environment_info(self) -> Dict[str, Any]:
        """Get comprehensive environment information"""
        info = {}
        
        # Get camera data
        photo = await self.take_photo()
        if photo is not None:
            info["has_visual_data"] = True
            info["image_shape"] = photo.shape
        else:
            info["has_visual_data"] = False
        
        # Get sensor data
        info["battery_level"] = await self.sensors.get_battery_level()
        info["temperature"] = await self.sensors.get_temperature()
        info["touch_sensors"] = await self.sensors.get_touch_data()
        info["sonar_data"] = await self.sensors.get_sonar_data()
        
        # Get robot state
        info["is_autonomous"] = await self.sensors.get_autonomy_state()
        info["current_pose"] = await self.actuators.get_current_pose()
        
        return info
    
    # Event handling
    
    def on_sensor_data(self, callback: Callable):
        """Register callback for sensor data events"""
        self._sensor_callbacks.append(callback)
    
    def on_touch(self, callback: Callable):
        """Register callback for touch events"""
        self._touch_callbacks.append(callback)
    
    def on_speech(self, callback: Callable):
        """Register callback for speech events"""
        self._speech_callbacks.append(callback)
    
    async def start_event_loop(self):
        """Start the event processing loop"""
        self.logger.info("Starting event loop...")
        
        while self.connection.is_connected():
            try:
                # Process sensor events
                sensor_data = await self.sensors.get_all_sensor_data()
                for callback in self._sensor_callbacks:
                    await callback(sensor_data)
                
                # Process touch events
                touch_data = await self.sensors.get_touch_data()
                if touch_data.get("touched"):
                    for callback in self._touch_callbacks:
                        await callback(touch_data)
                
                # Update state
                await self._update_state()
                
                # Sleep to prevent excessive CPU usage
                await asyncio.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Error in event loop: {e}")
                await asyncio.sleep(1.0)
    
    # Utility methods
    
    def get_state(self) -> RobotState:
        """Get current robot state"""
        return self.state
    
    async def is_ready(self) -> bool:
        """Check if robot is ready for operation"""
        return (
            self.connection.is_connected() and
            self.state.battery_level > 20.0 and
            self.state.temperature < 60.0
        )
    
    async def emergency_stop(self):
        """Emergency stop all robot movements"""
        self.logger.warning("Emergency stop activated!")
        await self.actuators.stop_all()
        await self.actuators.set_stiffness(False)  # Disable motors
    
    async def disable_autonomous_mode(self):
        """Disable Pepper's autonomous mode"""
        try:
            if not (hasattr(self.connection, 'use_bridge') and self.connection.use_bridge):
                # Direct connection mode
                try:
                    autonomous_service = self.connection.get_service("ALAutonomousLife")
                    autonomous_service.setState("disabled")
                    self.logger.info("Autonomous mode disabled")
                except Exception as e:
                    self.logger.warning(f"Could not disable autonomous mode: {e}")
            # Bridge mode - autonomous mode should already be handled by bridge
            else:
                self.logger.info("Bridge mode - autonomous mode handled by bridge service")
        except Exception as e:
            self.logger.warning(f"Failed to disable autonomous mode: {e}")
    
    async def enable_autonomous_mode(self):
        """Enable Pepper's autonomous mode"""
        try:
            if not (hasattr(self.connection, 'use_bridge') and self.connection.use_bridge):
                # Direct connection mode
                try:
                    autonomous_service = self.connection.get_service("ALAutonomousLife")
                    autonomous_service.setState("solitary")
                    self.logger.info("Autonomous mode enabled")
                except Exception as e:
                    self.logger.warning(f"Could not enable autonomous mode: {e}")
        except Exception as e:
            self.logger.warning(f"Failed to enable autonomous mode: {e}")
