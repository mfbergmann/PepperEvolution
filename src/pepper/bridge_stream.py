"""
Asynchronous client for Pepper Bridge v2 WebSocket streams (video/audio/sensors).
This module is optional and not yet integrated into the event loop by default.
"""

import asyncio
import struct
import json
from typing import AsyncIterator, Optional

import websockets
from loguru import logger


class BridgeStreamClient:
    """Client for Pepper Bridge v2 WebSocket streams"""

    def __init__(self, ws_base_url: str):
        """
        Args:
            ws_base_url: Base WS URL such as ws://10.0.100.100:8889
        """
        self.ws_base_url = ws_base_url.rstrip('/')
        self.log = logger.bind(module="BridgeStreamClient")

    async def iter_video_frames(self) -> AsyncIterator[dict]:
        """Yield video frames as dicts: {width, height, format, data (bytes), ts}.
        Packet layout (binary): [4-byte big-endian header length][header JSON][raw RGB bytes]
        """
        url = f"{self.ws_base_url}/video"
        async for pkt in self._iter_packets(url):
            header, payload = pkt
            if header.get("type") == "video":
                yield {
                    "width": header.get("width"),
                    "height": header.get("height"),
                    "format": header.get("format", "RGB"),
                    "data": payload,
                    "timestamp": header.get("timestamp"),
                }

    async def iter_audio_chunks(self) -> AsyncIterator[dict]:
        """Yield audio chunks as dicts: {format, sample_rate, channels, data (bytes), ts}."""
        url = f"{self.ws_base_url}/audio"
        async for pkt in self._iter_packets(url):
            header, payload = pkt
            if header.get("type") == "audio":
                yield {
                    "format": header.get("format", "wav"),
                    "sample_rate": header.get("sample_rate"),
                    "channels": header.get("channels"),
                    "data": payload,
                    "timestamp": header.get("timestamp"),
                }

    async def iter_sensor_packets(self) -> AsyncIterator[dict]:
        """Yield sensor JSON packets as Python dicts."""
        url = f"{self.ws_base_url}/sensors"
        async with websockets.connect(url, max_size=None) as ws:
            async for msg in ws:
                try:
                    if isinstance(msg, (bytes, bytearray)):
                        # v2 sensors uses plain JSON; handle bytes too
                        yield json.loads(msg.decode("utf-8"))
                    else:
                        yield json.loads(msg)
                except Exception as e:
                    self.log.warning(f"Failed to parse sensor packet: {e}")

    async def _iter_packets(self, url: str):
        """Internal: iterate packets with header length + header JSON + payload."""
        async with websockets.connect(url, max_size=None) as ws:
            async for msg in ws:
                if not isinstance(msg, (bytes, bytearray)):
                    continue
                buf = memoryview(msg)
                if len(buf) < 4:
                    continue
                header_len = struct.unpack(">I", buf[:4])[0]
                header_start = 4
                header_end = header_start + header_len
                if header_end > len(buf):
                    continue
                header_bytes = bytes(buf[header_start:header_end])
                payload = bytes(buf[header_end:])
                try:
                    header = json.loads(header_bytes.decode("utf-8"))
                except Exception:
                    header = {}
                yield header, payload