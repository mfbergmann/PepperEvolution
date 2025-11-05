"""
Pepper robot interface module
"""

from .robot import PepperRobot
from .connection import PepperConnection, ConnectionConfig

__all__ = ["PepperRobot", "PepperConnection", "ConnectionConfig"]
