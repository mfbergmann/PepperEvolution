"""
Tests for AudioStream - the bridge microphone WebSocket client.
"""

import json
from unittest.mock import AsyncMock

from src.pepper.audio_stream import AudioStream
from src.pepper.connection import ConnectionConfig


class TestAudioStream:
    def test_urls(self):
        assert AudioStream("ws://h:8888/ws/audio", api_key="k").url == "ws://h:8888/ws/audio?api_key=k"
        assert AudioStream("ws://h:8888/ws/audio").url == "ws://h:8888/ws/audio"
        assert ConnectionConfig(ip="1.2.3.4").audio_ws_url == "ws://1.2.3.4:8888/ws/audio"

    async def test_dispatch_counts_frames(self):
        stream = AudioStream("ws://h/ws/audio")
        cb = AsyncMock()
        stream.on_audio(cb)
        await stream._dispatch(b"\x00\x01\x02\x03")
        cb.assert_awaited_once_with(b"\x00\x01\x02\x03")
        assert stream.frames == 1 and stream.bytes_received == 4

    async def test_callback_errors_are_contained(self):
        stream = AudioStream("ws://h/ws/audio")
        stream.on_audio(AsyncMock(side_effect=ValueError("boom")))
        await stream._dispatch(b"\x00\x00")
        assert stream.frames == 1

    def test_text_frames(self):
        stream = AudioStream("ws://h/ws/audio")
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 1, "format": "pcm_s16le"}))
        assert stream.sample_rate == 16000 and stream.channels == 1 and stream.audio_format == "pcm_s16le"
        stream._handle_text(json.dumps({"type": "state", "streaming": True}))
        assert stream.streaming is True
        stream._handle_text(json.dumps({"type": "error", "error": "unauthorized"}))
        assert stream.last_error == "unauthorized" and stream._backoff == 30.0
        stream._handle_text(json.dumps({"type": "error", "error": "audio service is not registered"}))
        assert stream._backoff == 5.0
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000}))
        assert stream._backoff is None
        stream._handle_text("not json")
        stream._handle_text(json.dumps([1, 2]))
        assert stream.streaming is True

    async def test_stop_without_start(self):
        stream = AudioStream("ws://h/ws/audio")
        await stream.stop()
        assert stream.connected is False
