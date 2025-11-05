"""
Actuator manager for Pepper robot - handles all robot control commands
"""

import asyncio
from typing import Optional, Dict, Any, List, Tuple
import math

from loguru import logger

from ..pepper.connection import PepperConnection


class ActuatorManager:
    """Manages all Pepper robot actuators and movement"""
    
    def __init__(self, connection: PepperConnection):
        self.connection = connection
        self.logger = logger.bind(module="ActuatorManager")
        
        # NAOqi services
        self.motion_service = None
        self.posture_service = None
        self.tts_service = None
        self.led_service = None
        self.tablet_service = None
        self.animation_service = None
        
        # Movement state
        self._is_moving = False
        self._current_pose = "unknown"
        
    async def initialize(self):
        """Initialize all actuator services"""
        try:
            self.logger.info("Initializing actuator services...")
            
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                self.logger.info("Using bridge mode - services will be accessed via bridge")
                # Bridge mode doesn't need service initialization
                self.logger.success("Actuator services initialized (bridge mode)")
                return
            
            # Get NAOqi services (direct connection)
            self.motion_service = self.connection.get_service("ALMotion")
            self.posture_service = self.connection.get_service("ALRobotPosture")
            self.tts_service = self.connection.get_service("ALTextToSpeech")
            self.led_service = self.connection.get_service("ALLeds")
            self.tablet_service = self.connection.get_service("ALTabletService")
            self.animation_service = self.connection.get_service("ALAnimationPlayer")
            
            # Enable motion
            self.motion_service.wakeUp()
            
            # Set initial posture
            await self.set_posture("Stand")
            
            self.logger.success("Actuator services initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize actuators: {e}")
            raise
    
    # Movement methods
    
    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> bool:
        """Move forward by specified distance"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                if self.connection.bridge_client:
                    result = self.connection.bridge_client.move_forward(distance)
                    return result.get("success", False)
                else:
                    self.logger.error("Bridge client not available")
                    return False
            
            # Direct connection mode
            if self._is_moving:
                self.logger.warning("Robot is already moving")
                return False
            
            self._is_moving = True
            self.logger.info(f"Moving forward {distance}m at speed {speed}")
            
            # Calculate movement time based on distance and speed
            movement_time = distance / speed
            
            # Move forward
            self.motion_service.moveTo(distance, 0, 0, [["MaxVelXY", speed]])
            
            # Wait for movement to complete
            await asyncio.sleep(movement_time)
            
            # Stop movement
            self.motion_service.stopMove()
            self._is_moving = False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to move forward: {e}")
            self._is_moving = False
            return False
    
    async def turn(self, angle: float, speed: float = 0.5) -> bool:
        """Turn by specified angle (degrees)"""
        try:
            # Check if using bridge mode
            if hasattr(self.connection, 'use_bridge') and self.connection.use_bridge:
                if self.connection.bridge_client:
                    result = self.connection.bridge_client.turn(angle)
                    return result.get("success", False)
                else:
                    self.logger.error("Bridge client not available")
                    return False
            
            # Direct connection mode
            if self._is_moving:
                self.logger.warning("Robot is already moving")
                return False
            
            self._is_moving = True
            self.logger.info(f"Turning {angle} degrees at speed {speed}")
            
            # Convert degrees to radians
            angle_rad = math.radians(angle)
            
            # Calculate movement time
            movement_time = abs(angle_rad) / speed
            
            # Turn
            self.motion_service.moveTo(0, 0, angle_rad, [["MaxVelTheta", speed]])
            
            # Wait for movement to complete
            await asyncio.sleep(movement_time)
            
            # Stop movement
            self.motion_service.stopMove()
            self._is_moving = False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to turn: {e}")
            self._is_moving = False
            return False
    
    async def move_to(self, x: float, y: float, theta: float = 0.0, speed: float = 0.3) -> bool:
        """Move to specific coordinates"""
        try:
            if self._is_moving:
                self.logger.warning("Robot is already moving")
                return False
            
            self._is_moving = True
            self.logger.info(f"Moving to position ({x}, {y}, {theta})")
            
            # Calculate distance for timing
            distance = math.sqrt(x*x + y*y)
            movement_time = distance / speed
            
            # Move to position
            self.motion_service.moveTo(x, y, theta, [["MaxVelXY", speed]])
            
            # Wait for movement to complete
            await asyncio.sleep(movement_time)
            
            # Stop movement
            self.motion_service.stopMove()
            self._is_moving = False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to move to position: {e}")
            self._is_moving = False
            return False
    
    async def stop_movement(self):
        """Stop all movement"""
        try:
            self.motion_service.stopMove()
            self._is_moving = False
            self.logger.info("Movement stopped")
            
        except Exception as e:
            self.logger.error(f"Failed to stop movement: {e}")
    
    # Posture and pose methods
    
    async def set_posture(self, posture: str) -> bool:
        """Set robot posture (Stand, Sit, Crouch, etc.)"""
        try:
            self.logger.info(f"Setting posture to {posture}")
            
            # Go to posture
            self.posture_service.goToPosture(posture, 0.5)
            
            # Update current pose
            self._current_pose = posture
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to set posture: {e}")
            return False
    
    async def get_current_pose(self) -> str:
        """Get current robot pose"""
        try:
            return self.posture_service.getPosture()
        except Exception as e:
            self.logger.error(f"Failed to get current pose: {e}")
            return self._current_pose
    
    # Speech methods
    
    async def speak(self, text: str, language: str = "en") -> bool:
        """Make Pepper speak text"""
        try:
            self.logger.info(f"Speaking: {text}")
            
            # Always check bridge mode first
            if getattr(self.connection, 'use_bridge', False):
                bridge_client = getattr(self.connection, 'bridge_client', None)
                if bridge_client:
                    self.logger.info("Using bridge for speak")
                    result = bridge_client.speak(text, language)
                    success = result.get("success", False)
                    if success:
                        self.logger.info(f"Speak successful via bridge: {result.get('message', '')}")
                    else:
                        self.logger.error(f"Speak failed via bridge: {result.get('error', 'Unknown error')}")
                    return success
                else:
                    self.logger.error("Bridge client not available (use_bridge=True but bridge_client=None)")
                    return False
            
            # Direct connection mode (should not be used if bridge is enabled)
            self.logger.warning("Using direct connection mode for speak (bridge should be enabled)")
            if not self.tts_service:
                self.logger.error("TTS service not initialized")
                return False
            
            # Set language if different
            if language != "en":
                self.tts_service.setLanguage(language)
            
            # Speak the text
            self.tts_service.say(text)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to speak: {e}", exc_info=True)
            return False
    
    async def set_speech_rate(self, rate: float):
        """Set speech rate (0.5 to 2.0)"""
        try:
            self.tts_service.setParameter("speed", rate)
            self.logger.info(f"Speech rate set to {rate}")
            
        except Exception as e:
            self.logger.error(f"Failed to set speech rate: {e}")
    
    async def set_voice(self, voice: str):
        """Set TTS voice"""
        try:
            self.tts_service.setVoice(voice)
            self.logger.info(f"Voice set to {voice}")
            
        except Exception as e:
            self.logger.error(f"Failed to set voice: {e}")
    
    # Gesture methods
    
    async def wave_hand(self) -> bool:
        """Perform a waving gesture"""
        try:
            self.logger.info("Performing wave gesture")
            
            # Always check bridge mode first
            use_bridge = getattr(self.connection, 'use_bridge', False)
            bridge_client = getattr(self.connection, 'bridge_client', None)
            self.logger.info(f"wave_hand: use_bridge={use_bridge}, bridge_client={bridge_client is not None}, connection id={id(self.connection)}, connection.use_bridge={self.connection.use_bridge if hasattr(self.connection, 'use_bridge') else 'NO ATTR'}")
            
            if use_bridge:
                bridge_client = getattr(self.connection, 'bridge_client', None)
                self.logger.info(f"wave_hand: bridge_client={bridge_client is not None}")
                if bridge_client:
                    self.logger.info("Using bridge for wave gesture")
                    result = bridge_client.play_animation("animations/Stand/Gestures/Hey_1")
                    success = result.get("success", False)
                    if success:
                        self.logger.info(f"Wave gesture successful via bridge: {result.get('message', '')}")
                    else:
                        self.logger.error(f"Wave gesture failed via bridge: {result.get('error', 'Unknown error')}")
                    return success
                else:
                    self.logger.error("Bridge client not available (use_bridge=True but bridge_client=None)")
                    return False
            
            # Direct connection mode (should not be used if bridge is enabled)
            self.logger.warning("Using direct connection mode for wave (bridge should be enabled)")
            if not self.animation_service:
                self.logger.error("Animation service not initialized")
                return False
            
            # Play waving animation
            self.animation_service.run("animations/Stand/Gestures/Hey_1")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to wave hand: {e}", exc_info=True)
            return False
    
    async def point_at(self, x: float, y: float, z: float) -> bool:
        """Point at a specific location"""
        try:
            self.logger.info(f"Pointing at ({x}, {y}, {z})")
            
            # Calculate joint angles for pointing
            # This is a simplified version - would need proper inverse kinematics
            self.motion_service.setAngles("RArm", [x, y, z, 0, 0, 0], 0.3)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to point: {e}")
            return False
    
    async def nod_head(self) -> bool:
        """Nod head up and down"""
        try:
            self.logger.info("Nodding head")
            
            # Always check bridge mode first
            if getattr(self.connection, 'use_bridge', False):
                bridge_client = getattr(self.connection, 'bridge_client', None)
                if bridge_client:
                    self.logger.info("Using bridge for nod gesture")
                    result = bridge_client.play_animation("animations/Stand/Gestures/Enthusiastic_4")
                    success = result.get("success", False)
                    if success:
                        self.logger.info(f"Nod gesture successful via bridge: {result.get('message', '')}")
                    else:
                        self.logger.error(f"Nod gesture failed via bridge: {result.get('error', 'Unknown error')}")
                    return success
                else:
                    self.logger.error("Bridge client not available (use_bridge=True but bridge_client=None)")
                    return False
            
            # Direct connection mode (should not be used if bridge is enabled)
            self.logger.warning("Using direct connection mode for nod (bridge should be enabled)")
            if not self.animation_service:
                self.logger.error("Animation service not initialized")
                return False
            
            # Nod animation
            self.animation_service.run("animations/Stand/Gestures/Enthusiastic_4")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to nod head: {e}", exc_info=True)
            return False
    
    # LED control
    
    async def set_eye_color(self, color: str):
        """Set eye LED color"""
        try:
            self.logger.info(f"Setting eye color to {color}")
            
            # Always check bridge mode first
            if getattr(self.connection, 'use_bridge', False):
                bridge_client = getattr(self.connection, 'bridge_client', None)
                if bridge_client:
                    self.logger.info("Using bridge for set_eye_color")
                    result = bridge_client.set_eye_color(color)
                    success = result.get("success", False)
                    if success:
                        self.logger.info(f"Eye color set successfully via bridge: {result.get('message', '')}")
                    else:
                        self.logger.error(f"Eye color failed via bridge: {result.get('error', 'Unknown error')}")
                    return success
                else:
                    self.logger.error("Bridge client not available (use_bridge=True but bridge_client=None)")
                    return False
            
            # Direct connection mode (should not be used if bridge is enabled)
            self.logger.warning("Using direct connection mode for set_eye_color (bridge should be enabled)")
            if not self.led_service:
                self.logger.error("LED service not initialized")
                return False
            
            color_map = {
                "red": 0xFF0000,
                "green": 0x00FF00,
                "blue": 0x0000FF,
                "yellow": 0xFFFF00,
                "purple": 0xFF00FF,
                "cyan": 0x00FFFF,
                "white": 0xFFFFFF,
                "off": 0x000000
            }
            
            if color in color_map:
                self.led_service.fadeRGB("FaceLeds", color_map[color], 0.5)
                self.logger.info(f"Eye color set to {color}")
                return True
            else:
                self.logger.warning(f"Unknown color: {color}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to set eye color: {e}", exc_info=True)
            return False
    
    async def set_chest_led(self, color: str):
        """Set chest LED color"""
        try:
            color_map = {
                "red": 0xFF0000,
                "green": 0x00FF00,
                "blue": 0x0000FF,
                "yellow": 0xFFFF00,
                "purple": 0xFF00FF,
                "cyan": 0x00FFFF,
                "white": 0xFFFFFF,
                "off": 0x000000
            }
            
            if color in color_map:
                self.led_service.fadeRGB("ChestLeds", color_map[color], 0.5)
                self.logger.info(f"Chest LED set to {color}")
            else:
                self.logger.warning(f"Unknown color: {color}")
                
        except Exception as e:
            self.logger.error(f"Failed to set chest LED: {e}")
    
    # Tablet control
    
    async def show_image(self, image_path: str):
        """Show image on tablet"""
        try:
            self.tablet_service.showImage(image_path)
            self.logger.info(f"Showing image: {image_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to show image: {e}")
    
    async def show_webpage(self, url: str):
        """Show webpage on tablet"""
        try:
            self.tablet_service.showWebview(url)
            self.logger.info(f"Showing webpage: {url}")
            
        except Exception as e:
            self.logger.error(f"Failed to show webpage: {e}")
    
    # Utility methods
    
    async def set_stiffness(self, enabled: bool):
        """Enable or disable motor stiffness"""
        try:
            if enabled:
                self.motion_service.wakeUp()
                self.logger.info("Motors enabled")
            else:
                self.motion_service.rest()
                self.logger.info("Motors disabled")
                
        except Exception as e:
            self.logger.error(f"Failed to set stiffness: {e}")
    
    async def stop_all(self):
        """Stop all ongoing activities"""
        try:
            # Stop movement
            self.motion_service.stopMove()
            self._is_moving = False
            
            # Stop speech
            self.tts_service.stopAll()
            
            # Stop animations
            self.animation_service.stopAll()
            
            self.logger.info("All activities stopped")
            
        except Exception as e:
            self.logger.error(f"Failed to stop all activities: {e}")
    
    def is_moving(self) -> bool:
        """Check if robot is currently moving"""
        return self._is_moving
