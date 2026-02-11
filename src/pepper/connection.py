"""
Pepper robot connection management via the bridge server.

Replaces direct NAOqi access with HTTP calls to the bridge.
"""

from dataclasses import dataclass
from typing import Any, Dict

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

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.bridge_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.ip}:{self.bridge_port}/ws/events"


class PepperConnection:
    """Manages the connection to Pepper via the bridge server."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.bridge = BridgeClient(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )
        self.events = EventStream(
            ws_url=config.ws_url,
            api_key=config.api_key,
        )
        self.connected = False
        self.logger = logger.bind(module="PepperConnection")

    async def connect(self) -> bool:
        """Connect to the bridge and verify it's alive."""
        try:
            self.logger.info(f"Connecting to bridge at {self.config.base_url}")
            await self.bridge.connect()
            health = await self.bridge.health()
            self.connected = True
            self.logger.success(f"Connected to bridge (version {health.get('version', '?')})")
            # Start event stream
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
            return {"status": "connected", **data}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
