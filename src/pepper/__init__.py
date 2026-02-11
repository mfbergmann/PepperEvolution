"""
Pepper robot interface module.
"""

from .robot import PepperRobot, RobotState
from .connection import PepperConnection, ConnectionConfig
from .bridge_client import BridgeClient, BridgeError
from .event_stream import EventStream

__all__ = [
    "PepperRobot",
    "RobotState",
    "PepperConnection",
    "ConnectionConfig",
    "BridgeClient",
    "BridgeError",
    "EventStream",
]
