"""
WebSocket listener for real-time robot events from the bridge.

Connects to ws://<robot>:8888/ws/events and dispatches events to registered
async callbacks. Reconnects automatically if the bridge restarts.

Event payloads pushed by the bridge (see docs/BRIDGE_API.md):
    touch   {"sensor": "head_front", "touched": true}
    bumper  {"sensor": "front_left", "pressed": true}
    sonar   {"front": 0.3, "back": 1.9, "obstacle": true}
    battery {"level": 75, "charging": false}
    people  {"count": 1, "ids": [3]}
    speech  {"state": "start"|"end", "text": "..."}
"""

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

try:  # websockets >= 13 ships the new asyncio implementation
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - very old websockets
    try:
        from websockets import connect as ws_connect  # type: ignore[no-redef]
    except ImportError:
        ws_connect = None  # type: ignore[assignment,misc]


EventCallback = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]


class EventStream:
    """Connects to the bridge WebSocket and dispatches events."""

    RECONNECT_DELAY = 3.0

    def __init__(self, ws_url: str, api_key: str = ""):
        self.ws_url = ws_url
        self.api_key = api_key
        self.logger = logger.bind(module="EventStream")
        self._callbacks: Dict[str, List[EventCallback]] = {}
        self._global_callbacks: List[EventCallback] = []
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.connected = False
        self.last_event_at: Optional[float] = None
        self._backoff: Optional[float] = None

    def on(self, event_type: str, callback: EventCallback):
        """Register a callback for a specific event type (touch, sonar, battery, people, ...)."""
        self._callbacks.setdefault(event_type, []).append(callback)

    def on_any(self, callback: EventCallback):
        """Register a callback for all events."""
        self._global_callbacks.append(callback)

    @property
    def url(self) -> str:
        url = self.ws_url
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={self.api_key}"
        return url

    async def start(self):
        """Start listening for events in the background."""
        if ws_connect is None:
            self.logger.warning("websockets not installed, event stream disabled")
            return
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop(), name="bridge-event-stream")

    async def stop(self):
        """Stop the event stream."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.connected = False

    async def _listen_loop(self):
        """Reconnecting listen loop."""
        while self._running:
            try:
                self.logger.info(f"Connecting to event stream: {self.ws_url}")
                async with ws_connect(self.url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self.connected = True
                    self.logger.info("Event stream connected")
                    async for raw in ws:
                        await self._handle_raw(raw)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._running:
                    self.logger.warning(
                        f"Event stream disconnected: {exc}. Reconnecting in {self.RECONNECT_DELAY:.0f}s..."
                    )
            finally:
                self.connected = False
                self._ws = None
            if self._running:
                await asyncio.sleep(self._backoff or self.RECONNECT_DELAY)

    async def _handle_raw(self, raw: Any):
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self.logger.warning("Non-JSON message on event stream")
            return
        if not isinstance(msg, dict):
            return
        event_type = msg.get("type", "unknown")
        if event_type in ("pong", "hello"):
            return
        if event_type == "error":
            self.logger.error(f"Bridge event stream error: {msg.get('data')}")
            if (msg.get("data") or {}).get("error") == "unauthorized":
                self._backoff = 30.0  # wrong BRIDGE_API_KEY; do not hammer the bridge
            return
        data = msg.get("data") or {}
        self.last_event_at = msg.get("timestamp")
        await self._dispatch(event_type, data)

    async def _dispatch(self, event_type: str, data: Dict[str, Any]):
        for cb in self._global_callbacks:
            try:
                await cb(event_type, data)
            except Exception as exc:
                self.logger.error(f"Global callback error: {exc}")

        for cb in self._callbacks.get(event_type, []):
            try:
                await cb(event_type, data)
            except Exception as exc:
                self.logger.error(f"Callback error for {event_type}: {exc}")
