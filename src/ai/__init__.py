"""
AI model integrations for Pepper robot.
"""

from .manager import AIManager
from .models import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    SYSTEM_PROMPT,
    AIProvider,
    AIResponse,
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    ToolCall,
)
from .speech import SentenceSplitter, SpeechStreamer, clean_for_speech, strip_animation_tags
from .tool_executor import ToolExecutor, ToolOutcome
from .tools import KNOWN_ANIMATIONS, TOOLS

__all__ = [
    "AIManager",
    "AIProvider",
    "AnthropicProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "AIResponse",
    "ToolCall",
    "SYSTEM_PROMPT",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "TOOLS",
    "KNOWN_ANIMATIONS",
    "ToolExecutor",
    "ToolOutcome",
    "SpeechStreamer",
    "SentenceSplitter",
    "clean_for_speech",
    "strip_animation_tags",
]
