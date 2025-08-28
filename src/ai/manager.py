"""
AI manager for coordinating between Pepper robot and AI models
"""

import asyncio
import json
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from .models import AIProvider, OpenAIProvider, AnthropicProvider
from ..pepper.robot import PepperRobot


@dataclass
class ConversationContext:
    """Context for ongoing conversation"""
    user_input: str
    robot_state: Dict[str, Any]
    sensor_data: Dict[str, Any]
    conversation_history: List[Dict[str, str]]
    timestamp: datetime


class AIManager:
    """Manages AI interactions with Pepper robot"""
    
    def __init__(self, robot: PepperRobot, provider: AIProvider):
        self.robot = robot
        self.provider = provider
        self.logger = logger.bind(module="AIManager")
        
        # Conversation state
        self.conversation_history: List[Dict[str, str]] = []
        self.context_window = 10  # Number of recent messages to keep
        
        # Event callbacks
        self._response_callbacks: List[Callable] = []
        self._analysis_callbacks: List[Callable] = []
        
    async def process_user_input(self, user_input: str) -> str:
        """Process user input and generate robot response"""
        try:
            self.logger.info(f"Processing user input: {user_input}")
            
            # Get current robot state and sensor data
            robot_state = self.robot.get_state()
            sensor_data = await self.robot.get_environment_info()
            
            # Create conversation context
            context = ConversationContext(
                user_input=user_input,
                robot_state=robot_state.__dict__,
                sensor_data=sensor_data,
                conversation_history=self.conversation_history[-self.context_window:],
                timestamp=datetime.now()
            )
            
            # Generate AI response
            response = await self._generate_response(context)
            
            # Update conversation history
            self._update_conversation_history(user_input, response)
            
            # Execute robot actions based on response
            await self._execute_robot_actions(response, context)
            
            # Notify callbacks
            for callback in self._response_callbacks:
                await callback(response, context)
            
            return response
            
        except Exception as e:
            self.logger.error(f"Failed to process user input: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    async def analyze_environment(self) -> Dict[str, Any]:
        """Analyze current environment using sensor data"""
        try:
            self.logger.info("Analyzing environment...")
            
            # Get comprehensive sensor data
            sensor_data = await self.robot.get_environment_info()
            
            # Analyze with AI
            analysis = await self.provider.analyze_sensor_data(sensor_data)
            
            # Notify callbacks
            for callback in self._analysis_callbacks:
                await callback(analysis, sensor_data)
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"Failed to analyze environment: {e}")
            return {"error": str(e)}
    
    async def start_conversation(self):
        """Start an interactive conversation with the robot"""
        try:
            self.logger.info("Starting conversation mode...")
            
            # Initial greeting
            greeting = await self.process_user_input("Hello, I'm ready to interact!")
            await self.robot.speak(greeting)
            
            # Main conversation loop
            while True:
                # Listen for user input
                user_input = await self.robot.listen(timeout=30.0)
                
                if user_input:
                    # Process input and get response
                    response = await self.process_user_input(user_input)
                    
                    # Speak response
                    await self.robot.speak(response)
                else:
                    # No input received, analyze environment
                    analysis = await self.analyze_environment()
                    if analysis.get("analysis"):
                        await self.robot.speak("I notice some activity in my environment. " + analysis["analysis"])
                
                # Brief pause
                await asyncio.sleep(1.0)
                
        except KeyboardInterrupt:
            self.logger.info("Conversation ended by user")
        except Exception as e:
            self.logger.error(f"Conversation error: {e}")
    
    async def _generate_response(self, context: ConversationContext) -> str:
        """Generate AI response based on context"""
        try:
            # Build prompt with context
            prompt = self._build_contextual_prompt(context)
            
            # Generate response
            response = await self.provider.generate_response(prompt, context.robot_state)
            
            return response
            
        except Exception as e:
            self.logger.error(f"Failed to generate response: {e}")
            return "I'm sorry, I'm having trouble processing that right now."
    
    async def _execute_robot_actions(self, response: str, context: ConversationContext):
        """Execute robot actions based on AI response"""
        try:
            # Parse response for action commands
            actions = self._parse_actions(response)
            
            for action in actions:
                await self._execute_action(action)
                
        except Exception as e:
            self.logger.error(f"Failed to execute robot actions: {e}")
    
    def _build_contextual_prompt(self, context: ConversationContext) -> str:
        """Build prompt with conversation context"""
        prompt = f"User input: {context.user_input}\n\n"
        
        # Add sensor data context
        if context.sensor_data:
            prompt += "Current environment:\n"
            for key, value in context.sensor_data.items():
                if key != "camera":  # Skip image data
                    prompt += f"- {key}: {value}\n"
            prompt += "\n"
        
        # Add robot state context
        if context.robot_state:
            prompt += "Robot state:\n"
            for key, value in context.robot_state.items():
                prompt += f"- {key}: {value}\n"
            prompt += "\n"
        
        # Add conversation history
        if context.conversation_history:
            prompt += "Recent conversation:\n"
            for msg in context.conversation_history[-3:]:  # Last 3 messages
                prompt += f"{msg['role']}: {msg['content']}\n"
            prompt += "\n"
        
        prompt += "Please respond naturally as Pepper the robot. If you want me to perform an action, include it in your response using action tags like [MOVE:forward:0.5] or [SPEAK:Hello!] or [GESTURE:wave]."
        
        return prompt
    
    def _parse_actions(self, response: str) -> List[Dict[str, Any]]:
        """Parse action commands from AI response"""
        actions = []
        
        # Look for action tags in response
        import re
        
        # Movement actions
        move_pattern = r'\[MOVE:(\w+):([\d.]+)\]'
        for match in re.finditer(move_pattern, response):
            action_type = match.group(1)
            value = float(match.group(2))
            actions.append({
                "type": "move",
                "action": action_type,
                "value": value
            })
        
        # Speech actions
        speak_pattern = r'\[SPEAK:(.+?)\]'
        for match in re.finditer(speak_pattern, response):
            text = match.group(1)
            actions.append({
                "type": "speak",
                "text": text
            })
        
        # Gesture actions
        gesture_pattern = r'\[GESTURE:(\w+)\]'
        for match in re.finditer(gesture_pattern, response):
            gesture = match.group(1)
            actions.append({
                "type": "gesture",
                "gesture": gesture
            })
        
        # LED actions
        led_pattern = r'\[LED:(\w+):(\w+)\]'
        for match in re.finditer(led_pattern, response):
            location = match.group(1)
            color = match.group(2)
            actions.append({
                "type": "led",
                "location": location,
                "color": color
            })
        
        return actions
    
    async def _execute_action(self, action: Dict[str, Any]):
        """Execute a single robot action"""
        try:
            action_type = action["type"]
            
            if action_type == "move":
                if action["action"] == "forward":
                    await self.robot.move_forward(action["value"])
                elif action["action"] == "turn":
                    await self.robot.turn(action["value"])
                    
            elif action_type == "speak":
                await self.robot.speak(action["text"])
                
            elif action_type == "gesture":
                if action["gesture"] == "wave":
                    await self.robot.wave_hand()
                elif action["gesture"] == "nod":
                    await self.robot.nod_head()
                    
            elif action_type == "led":
                if action["location"] == "eyes":
                    await self.robot.actuators.set_eye_color(action["color"])
                elif action["location"] == "chest":
                    await self.robot.actuators.set_chest_led(action["color"])
                    
        except Exception as e:
            self.logger.error(f"Failed to execute action {action}: {e}")
    
    def _update_conversation_history(self, user_input: str, response: str):
        """Update conversation history"""
        self.conversation_history.append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        })
        
        self.conversation_history.append({
            "role": "assistant",
            "content": response,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep only recent messages
        if len(self.conversation_history) > self.context_window * 2:
            self.conversation_history = self.conversation_history[-self.context_window * 2:]
    
    # Event handling
    
    def on_response(self, callback: Callable):
        """Register callback for response events"""
        self._response_callbacks.append(callback)
    
    def on_analysis(self, callback: Callable):
        """Register callback for analysis events"""
        self._analysis_callbacks.append(callback)
    
    # Utility methods
    
    def get_conversation_history(self) -> List[Dict[str, str]]:
        """Get conversation history"""
        return self.conversation_history.copy()
    
    def clear_conversation_history(self):
        """Clear conversation history"""
        self.conversation_history.clear()
    
    def set_context_window(self, window_size: int):
        """Set conversation context window size"""
        self.context_window = window_size
