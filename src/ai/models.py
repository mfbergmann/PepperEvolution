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
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
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
    
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Generate response using OpenAI GPT"""
        try:
            # Build system message with context
            system_message = self._build_system_message(context)
            
            # Create messages
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ]
            
            # Generate response
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=1000,
                temperature=0.7
            )
            
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
            response = await self.client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=messages,
                max_tokens=500,
                temperature=0.3
            )
            
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
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an AI assistant analyzing robot sensor data."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.3
            )
            
            analysis = response.choices[0].message.content
            
            return {
                "analysis": analysis,
                "has_visual_data": False,
                "model": self.model
            }
            
        except Exception as e:
            self.logger.error(f"Text analysis failed: {e}")
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


class AnthropicProvider(AIProvider):
    """Anthropic Claude model provider"""
    
    def __init__(self, api_key: str, model: str = "claude-3-sonnet-20240229"):
        super().__init__(api_key, model)
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
    
    async def generate_response(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Generate response using Anthropic Claude"""
        try:
            # Build system message
            system_message = self._build_system_message(context)
            
            # Generate response
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=system_message,
                messages=[
                    {"role": "user", "content": prompt}
                ]
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
