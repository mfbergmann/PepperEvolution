"""
AI model providers for different services (OpenAI, Anthropic, etc.)
"""

import asyncio
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod
import json

from loguru import logger
import openai
import anthropic


class AIProvider(ABC):
    """Abstract base class for AI providers"""
    
    def __init__(self, api_key: str, model: str = "gpt-5"):
        self.api_key = api_key
        self.model = model
        self.logger = logger.bind(module=self.__class__.__name__)
    
    @abstractmethod
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
        """Generate response from AI model"""
        pass
    
    @abstractmethod
    async def analyze_sensor_data(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze sensor data and provide insights"""
        pass


class OpenAIProvider(AIProvider):
    """OpenAI GPT model provider"""
    
    def __init__(self, api_key: str, model: str = "gpt-5"):
        super().__init__(api_key, model)
        self.client = openai.AsyncOpenAI(api_key=api_key)
    
    def _get_token_param(self, default_tokens: int = 1000) -> Dict[str, Any]:
        """Get the correct token parameter based on model type"""
        # According to OpenAI API docs:
        # - gpt-5, gpt-5-mini, o1, o3, gpt-4o: Use max_completion_tokens
        # - gpt-3.5, gpt-4 (non-o versions): Use max_tokens
        model_lower = self.model.lower()
        
        # Check for newer models that require max_completion_tokens
        newer_models = ["gpt-5", "gpt-4o", "o1", "o3"]
        if any(newer_model in model_lower for newer_model in newer_models):
            result = {"max_completion_tokens": default_tokens}
            self.logger.info(f"Using max_completion_tokens for model {self.model}")
            return result
        
        # Legacy models use max_tokens
        if "gpt-3.5" in model_lower or ("gpt-4" in model_lower and "gpt-4o" not in model_lower):
            result = {"max_tokens": default_tokens}
            self.logger.info(f"Using max_tokens for model {self.model}")
            return result
        
        # Default to max_completion_tokens for unknown/newer models
        result = {"max_completion_tokens": default_tokens}
        self.logger.info(f"Using max_completion_tokens (default) for model {self.model}")
        return result
    
    def _build_system_message(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Build system message with context"""
        base_message = """You are Pepper, a friendly humanoid robot assistant developed by SoftBank Robotics. You are designed to interact naturally with humans in various environments.

Your capabilities include:
- Movement: You can move forward, backward, turn, and navigate your environment
- Speech: You can speak and listen to humans using natural language
- Vision: You have cameras that allow you to see your surroundings
- Sensors: You can sense touch, detect obstacles, and monitor your battery level
- Gestures: You can wave, nod, point, and perform other natural gestures
- LEDs: You can control colored LEDs on your face and chest
- Tablet: You have a tablet display that can show images and web content

Your personality:
- Friendly, helpful, and approachable
- Polite and respectful in all interactions
- Enthusiastic about helping humans
- Aware of your robotic nature but natural in conversation
- Safety-conscious and considerate

Guidelines for responses:
- Keep responses concise and natural (2-3 sentences typically)
- Use action tags like [MOVE:forward:0.5] or [GESTURE:wave] to control your body
- Your response text will automatically be spoken, so write as if speaking
- Remember previous conversations and context
- Be proactive in offering help when appropriate"""
        
        if context:
            # Add current context information
            if context.get("battery_level"):
                base_message += f"\n\nCurrent Status: Battery at {context.get('battery_level', 0):.0f}%"
            if context.get("is_connected"):
                base_message += "\nStatus: Connected and ready"
        
        return base_message
    
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
        """Generate response using OpenAI GPT with conversation history"""
        try:
            # Build system message with context
            system_message = self._build_system_message(context)
            
            # Build messages array with conversation history
            messages = [{"role": "system", "content": system_message}]
            
            # Add conversation history if provided
            if conversation_history:
                for msg in conversation_history:
                    # Convert to OpenAI format
                    role = msg.get("role", "user")
                    if role == "assistant":
                        messages.append({"role": "assistant", "content": msg.get("content", "")})
                    elif role == "user":
                        messages.append({"role": "user", "content": msg.get("content", "")})
            
            # Add current user prompt
            messages.append({"role": "user", "content": prompt})
            
            # Generate response
            params = {
                "model": self.model,
                "messages": messages
            }
            
            # gpt-5 models only support temperature=1 (default), so don't set it
            model_lower = self.model.lower()
            if "gpt-5" not in model_lower:
                params["temperature"] = 0.7
            
            params.update(self._get_token_param(1000))
            
            response = await self.client.chat.completions.create(**params)
            
            return response.choices[0].message.content
            
        except Exception as e:
            self.logger.error(f"OpenAI API error: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    async def analyze_sensor_data(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze sensor data using OpenAI Vision"""
        try:
            # Build analysis prompt
            prompt = self._build_sensor_analysis_prompt(sensor_data)
            
            # If we have visual data, use vision model
            if sensor_data.get("camera") is not None:
                return await self._analyze_with_vision(sensor_data, prompt)
            else:
                return await self._analyze_text_only(sensor_data, prompt)
                
        except Exception as e:
            self.logger.error(f"Failed to analyze sensor data: {e}")
            return {"error": str(e)}
    
    async def _analyze_with_vision(self, sensor_data: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Analyze sensor data with visual input"""
        try:
            # Convert image to base64 for API
            import base64
            import cv2
            
            image = sensor_data["camera"]
            _, buffer = cv2.imencode('.jpg', image)
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            
            # Create messages with image
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ]
            
            # Use GPT-4 Vision for analysis
            params = {
                "model": "gpt-4-vision-preview",
                "messages": messages,
                "temperature": 0.3
            }
            # Vision models still use max_tokens (not max_completion_tokens)
            params.update({"max_tokens": 500})
            response = await self.client.chat.completions.create(**params)
            
            analysis = response.choices[0].message.content
            
            return {
                "analysis": analysis,
                "has_visual_data": True,
                "model": "gpt-4-vision-preview"
            }
            
        except Exception as e:
            self.logger.error(f"Vision analysis failed: {e}")
            return await self._analyze_text_only(sensor_data, prompt)
    
    async def _analyze_text_only(self, sensor_data: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Analyze sensor data without visual input"""
        try:
            params = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are an AI assistant analyzing robot sensor data."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }
            params.update(self._get_token_param(500))
            response = await self.client.chat.completions.create(**params)
            
            analysis = response.choices[0].message.content
            
            return {
                "analysis": analysis,
                "has_visual_data": False,
                "model": self.model
            }
            
        except Exception as e:
            self.logger.error(f"Text analysis failed: {e}")
            return {"error": str(e)}
    
    def _build_sensor_analysis_prompt(self, sensor_data: Dict[str, Any]) -> str:
        """Build prompt for sensor data analysis"""
        prompt = "Analyze the following robot sensor data and provide insights:\n\n"
        
        # Add sensor data to prompt
        for key, value in sensor_data.items():
            if key != "camera":  # Skip image data for text prompt
                prompt += f"{key}: {value}\n"
        
        prompt += "\nPlease provide:\n"
        prompt += "1. What you observe about the environment\n"
        prompt += "2. Any potential hazards or interesting objects\n"
        prompt += "3. Recommended actions for the robot\n"
        prompt += "4. Human interaction opportunities\n"
        
        return prompt


class AnthropicProvider(AIProvider):
    """Anthropic Claude model provider"""
    
    def __init__(self, api_key: str, model: str = "claude-3-sonnet-20240229"):
        super().__init__(api_key, model)
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
    
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
        """Generate response using Anthropic Claude"""
        try:
            # Build system message
            system_message = self._build_system_message(context)
            
            # Build messages array with conversation history
            messages = []
            if conversation_history:
                for msg in conversation_history:
                    role = msg.get("role", "user")
                    if role == "assistant":
                        messages.append({"role": "assistant", "content": msg.get("content", "")})
                    elif role == "user":
                        messages.append({"role": "user", "content": msg.get("content", "")})
            
            # Add current user prompt
            messages.append({"role": "user", "content": prompt})
            
            # Generate response
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=system_message,
                messages=messages
            )
            
            return response.content[0].text
            
        except Exception as e:
            self.logger.error(f"Anthropic API error: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    async def analyze_sensor_data(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze sensor data using Claude"""
        try:
            # Build analysis prompt
            prompt = self._build_sensor_analysis_prompt(sensor_data)
            
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system="You are an AI assistant analyzing robot sensor data. Provide clear, actionable insights.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            analysis = response.content[0].text
            
            return {
                "analysis": analysis,
                "has_visual_data": False,
                "model": self.model
            }
            
        except Exception as e:
            self.logger.error(f"Failed to analyze sensor data: {e}")
            return {"error": str(e)}
    
    def _build_system_message(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Build system message with context"""
        base_message = """You are Pepper, a friendly humanoid robot assistant. You can:
- Move around and perform gestures
- Speak and listen to humans
- See through cameras and sense the environment
- Control LEDs and display content on tablet
- Respond naturally and helpfully to humans

Always be polite, helpful, and considerate of human safety."""
        
        if context:
            context_str = json.dumps(context, indent=2)
            base_message += f"\n\nCurrent context:\n{context_str}"
        
        return base_message
    
    def _build_sensor_analysis_prompt(self, sensor_data: Dict[str, Any]) -> str:
        """Build prompt for sensor data analysis"""
        prompt = "Analyze the following robot sensor data and provide insights:\n\n"
        
        # Add sensor data to prompt
        for key, value in sensor_data.items():
            if key != "camera":  # Skip image data for text prompt
                prompt += f"{key}: {value}\n"
        
        prompt += "\nPlease provide:\n"
        prompt += "1. What you observe about the environment\n"
        prompt += "2. Any potential hazards or interesting objects\n"
        prompt += "3. Recommended actions for the robot\n"
        prompt += "4. Human interaction opportunities\n"
        
        return prompt
