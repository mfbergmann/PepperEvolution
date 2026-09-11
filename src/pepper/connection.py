"""
Pepper robot connection management via the bridge server.

Owns the HTTP client and the WebSocket event stream. A different bridge
implementation (e.g. :class:`FakeBridgeClient`) can be injected for tests
and robot-less development.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from .bridge_client import BridgeClient
from .event_stream import EventStream


@dataclass
class ConnectionConfig:
    """Configuration for connecting to the Pepper bridge."""

    ip: str
    bridge_port: int = 8888
    api_key: str = ""
    timeout: float = 15.0
    action_timeout: float = 120.0

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.bridge_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.ip}:{self.bridge_port}/ws/events"

    @property
    def audio_ws_url(self) -> str:
        return f"ws://{self.ip}:{self.bridge_port}/ws/audio"


class PepperConnection:
    """Manages the connection to Pepper via the bridge server."""

    def __init__(self, config: ConnectionConfig, bridge: Optional[Any] = None, events: bool = True):
        self.config = config
        self.bridge = bridge or BridgeClient(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
            action_timeout=config.action_timeout,
        )
        self.events = EventStream(ws_url=config.ws_url, api_key=config.api_key)
        self._events_enabled = events and bridge is None
        self.connected = False
        self.bridge_info: Dict[str, Any] = {}
        self.logger = logger.bind(module="PepperConnection")

    async def connect(self) -> bool:
        """Connect to the bridge and verify it's alive."""
        try:
            self.logger.info(f"Connecting to bridge at {self.config.base_url}")
            await self.bridge.connect()
            health = await self.bridge.health()
            self.bridge_info = health
            self.connected = True
            self.logger.success(
                f"Connected to bridge {health.get('bridge', '?')} v{health.get('version', '?')} "
                f"(robot {health.get('robot_name', '?')}, NAOqi {health.get('naoqi', '?')})"
            )
            if self._events_enabled:
                await self.events.start()
            return True
        except Exception as exc:
            self.logger.error(f"Connection failed: {exc}")
            self.connected = False
            return False

    async def disconnect(self):
        """Disconnect from the bridge."""
        await self.events.stop()
        await self.bridge.close()
        self.connected = False
        self.logger.info("Disconnected from bridge")

    def is_connected(self) -> bool:
        return self.connected

    async def health_check(self) -> Dict[str, Any]:
        """Check bridge health."""
        if not self.connected:
            return {"status": "disconnected", "error": "Not connected"}
        try:
            data = await self.bridge.health()
            return {"status": "connected", "events": self.events.connected, **data}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
