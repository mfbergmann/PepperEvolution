"""
Typed exception hierarchy for PepperEvolution.

``BridgeError`` (in bridge_client.py) is the exception the bridge client
raises; it is re-exported here so callers can catch a single base class.
"""

from .bridge_client import BridgeError


class PepperError(Exception):
    """Base exception for all PepperEvolution errors."""


class ToolExecutionError(PepperError):
    """AI tool execution failed."""


class AIProviderError(PepperError):
    """AI provider API call failed."""


__all__ = ["BridgeError", "PepperError", "ToolExecutionError", "AIProviderError"]
