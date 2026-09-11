"""
Microphone stream from the bridge (ws://<robot>:8888/ws/audio).

The bridge sends one JSON text frame first::

    {"type": "hello", "sample_rate": 16000, "channels": 1, "format": "pcm_s16le", "version": "2.2.0"}

and then binary frames of 16-bit little-endian PCM (about 170 ms each) while
the robot is not speaking. ``{"type": "state", ...}`` and ``{"type": "error", ...}``
text frames may follow. Reconnects automatically like :class:`EventStream`.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, List, Optional

from loguru import logger

try:  # websockets >= 13 ships the new asyncio implementation
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - very old websockets
    try:
        from websockets import connect as ws_connect  # type: ignore[no-redef]
    except ImportError:
        ws_connect = None  # type: ignore[assignment,misc]


AudioCallback = Callable[[bytes], Awaitable[None]]


class AudioStream:
    """Connects to the bridge microphone WebSocket and hands PCM frames to callbacks."""

    RECONNECT_DELAY = 3.0

    def __init__(self, ws_url: str, api_key: str = ""):
        self.ws_url = ws_url
        self.api_key = api_key
        self.logger = logger.bind(module="AudioStream")
        self.sample_rate = 16000
        self.channels = 1
        self.audio_format = "pcm_s16le"
        self.connected = False
        self.streaming = False  # the bridge confirmed the ALAudioDevice subscription
        self.frames = 0
        self.bytes_received = 0
        self.last_error: Optional[str] = None
        self._callbacks: List[AudioCallback] = []
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._backoff: Optional[float] = None

    def on_audio(self, callback: AudioCallback):
        """Register an async callback receiving each PCM frame (bytes)."""
        self._callbacks.append(callback)

    @property
    def url(self) -> str:
        url = self.ws_url
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={self.api_key}"
        return url

    async def start(self):
        if ws_connect is None:
            self.logger.warning("websockets not installed, microphone stream disabled")
            return
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop(), name="bridge-audio-stream")

    async def stop(self):
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
        self.streaming = False

    async def set_muted(self, muted: bool):
        """Ask the bridge to drop frames (e.g. while the host plays a sound itself)."""
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "mute", "muted": bool(muted)}))
        except Exception as exc:
            self.logger.debug(f"mute request failed: {exc}")

    async def _listen_loop(self):
        while self._running:
            try:
                self.logger.info(f"Connecting to microphone stream: {self.ws_url}")
                async with ws_connect(
                    self.url, open_timeout=10, ping_interval=20, ping_timeout=20, max_size=None
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    async for raw in ws:
                        if isinstance(raw, (bytes, bytearray, memoryview)):
                            await self._dispatch(bytes(raw))
                        else:
                            self._handle_text(raw)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._running:
                    self.last_error = str(exc)
                    self.logger.warning(
                        f"Microphone stream disconnected: {exc}. Reconnecting in {self.RECONNECT_DELAY:.0f}s..."
                    )
            finally:
                self.connected = False
                self.streaming = False
                self._ws = None
            if self._running:
                await asyncio.sleep(self._backoff or self.RECONNECT_DELAY)

    def _handle_text(self, raw: Any):
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "hello":
            self._backoff = None  # connected and accepted: back to the normal reconnect delay
            self.sample_rate = int(msg.get("sample_rate") or self.sample_rate)
            self.channels = int(msg.get("channels") or self.channels)
            self.audio_format = str(msg.get("format") or self.audio_format)
            self.logger.info(
                f"Microphone stream connected: {self.sample_rate} Hz, {self.channels} ch, {self.audio_format}"
            )
        elif kind == "state":
            if "streaming" in msg:
                self.streaming = bool(msg["streaming"])
        elif kind == "error":
            self.last_error = str(msg.get("error"))
            self.logger.error(f"Bridge microphone stream error: {self.last_error}")
            if self.last_error == "unauthorized":
                self._backoff = 30.0  # wrong BRIDGE_API_KEY; do not hammer the bridge
            else:
                self._backoff = 5.0  # e.g. NAOqi still booting: the bridge closes us, we retry a little later

    async def _dispatch(self, pcm: bytes):
        self.frames += 1
        self.bytes_received += len(pcm)
        for cb in self._callbacks:
            try:
                await cb(pcm)
            except Exception as exc:
                self.logger.error(f"Audio callback error: {exc}")
