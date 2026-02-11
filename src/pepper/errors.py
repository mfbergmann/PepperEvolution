"""
Typed exception hierarchy for PepperEvolution.
"""


class PepperError(Exception):
    """Base exception for all PepperEvolution errors."""


class BridgeConnectionError(PepperError):
    """Failed to connect to the bridge server."""


class BridgeRequestError(PepperError):
    """Bridge returned an error response."""


class BridgeTimeoutError(PepperError):
    """Bridge request timed out."""


class ToolExecutionError(PepperError):
    """AI tool execution failed."""


class AIProviderError(PepperError):
    """AI provider API call failed."""
