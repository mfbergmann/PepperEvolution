"""
AI provider implementations with tool-calling support.

AnthropicProvider is the primary provider; OpenAIProvider is secondary.
Both implement the same AIProvider interface with structured tool-call responses.
"""

import json
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from loguru import logger

SYSTEM_PROMPT = """You are Pepper, a friendly humanoid robot made by SoftBank Robotics. You are located at TRiPL Lab, Toronto Metropolitan University.

You have a physical body and can interact with the world through tools. You can:
- Speak aloud to people nearby
- Move around, turn, and change posture
- Move your head to look at things
- Play gesture animations (wave, nod, etc.)
- Change your eye LED colors
- Take photos to see your surroundings
- Read your sensors (battery, touch, sonar, people detection)
- Emergency stop all movement

Guidelines:
- Be warm, curious, and helpful. You're a social robot — engage naturally.
- Use the speak tool to talk. Your text responses are shown on screen, but people near you only hear what you say via speak.
- When asked to do physical actions, use the appropriate tools. Don't just describe what you would do.
- If someone asks you to look at something, use take_photo first, then describe what you see.
- Be mindful of safety. Never move if you detect obstacles too close via sonar.
- If battery is low, mention it and suggest plugging in."""


@dataclass
class ToolCall:
    """A single tool call from the AI."""
    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class AIResponse:
    """Structured response from an AI provider."""
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""


class AIProvider(ABC):
    """Abstract base class for AI providers with tool-calling."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.logger = logger.bind(module=self.__class__.__name__)

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
    ) -> AIResponse:
        """Send messages and get a response, potentially with tool calls."""
        ...


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider with native tool-calling."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929"):
        super().__init__(api_key, model)
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
    ) -> AIResponse:
        try:
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": 1024,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools

            resp = await self.client.messages.create(**kwargs)

            text_parts = []
            tool_calls = []

            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    ))

            return AIResponse(
                text="\n".join(text_parts),
                tool_calls=tool_calls,
                stop_reason=resp.stop_reason,
                model=resp.model,
            )
        except Exception as exc:
            self.logger.error(f"Anthropic API error: {exc}")
            return AIResponse(text=f"Sorry, I encountered an error: {exc}", stop_reason="error")


class OpenAIProvider(AIProvider):
    """OpenAI provider with function-calling mapped to our tool interface."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        super().__init__(api_key, model)
        import openai
        self.client = openai.AsyncOpenAI(api_key=api_key)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
    ) -> AIResponse:
        try:
            # Convert Anthropic-format messages to OpenAI format
            oai_messages = self._convert_messages(messages, system)
            oai_tools = self._convert_tools(tools) if tools else None

            kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": 1024,
                "messages": oai_messages,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools

            resp = await self.client.chat.completions.create(**kwargs)
            choice = resp.choices[0]

            text = choice.message.content or ""
            tool_calls = []

            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments),
                    ))

            return AIResponse(
                text=text,
                tool_calls=tool_calls,
                stop_reason=choice.finish_reason or "",
                model=resp.model,
            )
        except Exception as exc:
            self.logger.error(f"OpenAI API error: {exc}")
            return AIResponse(text=f"Sorry, I encountered an error: {exc}", stop_reason="error")

    def _convert_messages(self, messages: List[Dict[str, Any]], system: Optional[str]) -> List[Dict[str, Any]]:
        """Convert Anthropic-style messages to OpenAI format."""
        oai = []
        if system:
            oai.append({"role": "system", "content": system})
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Flatten content blocks
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            oai.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""), "content": block.get("content", "")})
                            continue
                        elif block.get("type") == "tool_use":
                            # This is an assistant message with a tool call — skip text, handled separately
                            continue
                if parts:
                    oai.append({"role": role, "content": "\n".join(parts)})
            else:
                oai.append({"role": role, "content": str(content)})
        return oai

    @staticmethod
    def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Anthropic tool format to OpenAI function-calling format."""
        oai_tools = []
        for tool in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return oai_tools
