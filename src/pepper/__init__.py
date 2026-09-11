"""
Pepper robot interface module.
"""

from .audio_stream import AudioStream
from .bridge_client import BridgeClient, BridgeError
from .connection import ConnectionConfig, PepperConnection
from .event_stream import EventStream
from .fake_bridge import FakeBridgeClient
from .robot import PepperRobot, Photo, PrepareOptions, RobotState

__all__ = [
    "PepperRobot",
    "RobotState",
    "Photo",
    "PrepareOptions",
    "PepperConnection",
    "ConnectionConfig",
    "BridgeClient",
    "BridgeError",
    "FakeBridgeClient",
    "EventStream",
    "AudioStream",
]
