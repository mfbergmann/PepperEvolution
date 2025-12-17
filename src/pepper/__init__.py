"""
Pepper robot interface module
"""

from .connection import PepperConnection, ConnectionConfig
from .robot import PepperRobot

__all__ = ["PepperRobot", "PepperConnection", "ConnectionConfig"]
