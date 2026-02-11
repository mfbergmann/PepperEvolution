"""
WebSocket listener for real-time robot events from the bridge.

Connects to ws://<robot>:8888/ws/events and dispatches events
to registered async callbacks.
"""

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional

import httpx
from loguru import logger

# We use the httpx-ws or websockets library for async WS.
# Using websockets since it's already a dependency.
try:
    import websockets
    import websockets.client
except ImportError:
    websockets = None  # type: ignore[assignment]


EventCallback = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]


class EventStream:
    """Connects to the bridge WebSocket and dispatches events."""

    def __init__(self, ws_url: str, api_key: str = ""):
        self.ws_url = ws_url
        self.api_key = api_key
        self.logger = logger.bind(module="EventStream")
        self._callbacks: Dict[str, List[EventCallback]] = {}
        self._global_callbacks: List[EventCallback] = []
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def on(self, event_type: str, callback: EventCallback):
        """Register a callback for a specific event type (touch, sonar, battery, people)."""
        self._callbacks.setdefault(event_type, []).append(callback)

    def on_any(self, callback: EventCallback):
        """Register a callback for all events."""
        self._global_callbacks.append(callback)

    async def start(self):
        """Start listening for events in the background."""
        if websockets is None:
            self.logger.warning("websockets not installed, event stream disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """Stop the event stream."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(self):
        """Reconnecting listen loop."""
        url = self.ws_url
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={self.api_key}"

        while self._running:
            try:
                self.logger.info(f"Connecting to event stream: {self.ws_url}")
                async with websockets.client.connect(url) as ws:
                    self._ws = ws
                    self.logger.info("Event stream connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            event_type = msg.get("type", "unknown")
                            data = msg.get("data", {})
                            await self._dispatch(event_type, data)
                        except json.JSONDecodeError:
                            self.logger.warning("Non-JSON message on event stream")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._running:
                    self.logger.warning(f"Event stream disconnected: {exc}. Reconnecting in 3s...")
                    await asyncio.sleep(3)

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
