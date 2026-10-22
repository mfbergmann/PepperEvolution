"""
Tests for AudioStream - the bridge microphone WebSocket client.
"""

import json
import struct
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


def four_channel_pcm(n, value=1000):
    return struct.pack("<%dh" % (4 * n), *([value, value, value, value] * n))


class TestBeamformingStream:
    def test_asks_for_four_channels_only_when_beamforming(self):
        assert AudioStream("ws://h/ws/audio", beamform=True).url == "ws://h/ws/audio?channels=4"
        assert AudioStream("ws://h/ws/audio", api_key="k", beamform=True).url == "ws://h/ws/audio?channels=4&api_key=k"

    async def test_four_channels_become_one_beamformed_channel(self):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 4}))
        assert stream.beamformer is not None and stream._downsampler is None
        cb, raw = AsyncMock(), []
        stream.on_audio(cb)
        stream.on_raw(lambda pcm, channels: raw.append((len(pcm), channels)))
        await stream._dispatch(four_channel_pcm(1024))  # four hops of 256
        pcm = cb.await_args.args[0]
        assert len(pcm) == 1024 * 2  # mono, 16-bit
        assert raw == [(1024 * 4 * 2, 4)]

    async def test_48k_from_a_bridge_without_audioop_is_resampled(self):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 48000, "channels": 4}))
        assert stream._downsampler is not None
        cb = AsyncMock()
        stream.on_audio(cb)
        await stream._dispatch(four_channel_pcm(3 * 1024))
        assert len(cb.await_args.args[0]) == 1024 * 2

    async def test_an_older_bridge_with_one_channel_passes_through(self):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 1}))
        assert stream.beamformer is None
        cb = AsyncMock()
        stream.on_audio(cb)
        await stream._dispatch(b"\x01\x00\x02\x00")
        cb.assert_awaited_once_with(b"\x01\x00\x02\x00")

    async def test_a_new_hello_resets_the_beamformer(self):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 4}))
        first = stream.beamformer
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 4}))
        assert stream.beamformer is not first

    async def test_a_pause_in_the_stream_restarts_the_buffers_and_pads_the_recording(self, monkeypatch):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 4}))
        stream.on_audio(AsyncMock())
        raw = []
        stream.on_raw(lambda pcm, channels: raw.append(len(pcm)))
        clock = [100.0]
        monkeypatch.setattr("src.pepper.audio_stream.time.monotonic", lambda: clock[0])
        gaps = []
        monkeypatch.setattr(stream.beamformer, "gap", lambda: gaps.append(1))
        await stream._dispatch(four_channel_pcm(512))
        clock[0] += 0.085
        await stream._dispatch(four_channel_pcm(512))
        assert gaps == [] and raw == [512 * 8, 512 * 8]
        clock[0] += 2.0  # Pepper spoke for two seconds
        await stream._dispatch(four_channel_pcm(512))
        assert gaps == [1] and raw[-1] == 512 * 8 + 8 * 32000

    def test_head_turns_reach_the_beamformer(self):
        stream = AudioStream("ws://h/ws/audio", beamform=True)
        stream.viewpoint_changed()  # no beamformer yet: nothing happens
        stream._handle_text(json.dumps({"type": "hello", "sample_rate": 16000, "channels": 4}))
        stream.beamformer.adapted = True
        stream.viewpoint_changed()
        assert stream.beamformer.adapted is False
