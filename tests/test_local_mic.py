"""
Tests for LOCAL_MICROPHONE: the host's own microphone (src/audio/local_mic.py), muted while Pepper speaks.
"""

import asyncio
import sys
import time
from unittest.mock import MagicMock

import main
from src.audio.local_mic import FRAME_BYTES, LocalMicrophone, recorder_command
from src.pepper import AudioStream, ConnectionConfig, FakeBridgeClient
from src.pepper.bridge_client import SPEECH_TAIL, SpeechClock


def fake_recorder(frames: int, exit_code: int = 0):
    """A recorder that writes ``frames`` frames, frame i filled with byte i, then exits."""
    script = (
        "import sys\n"
        f"for i in range({frames}):\n"
        f"    sys.stdout.buffer.write(bytes([i]) * {FRAME_BYTES})\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('device unplugged\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, "-c", script]


async def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


class TestRecorderCommand:
    def test_default_and_named_source(self):
        command = recorder_command("default")
        assert command[0] == "parecord" and "--raw" in command and "--rate=16000" in command
        assert "--channels=1" in command and not any(c.startswith("--device") for c in command)
        assert recorder_command("alsa_input.usb-DJI")[-1] == "--device=alsa_input.usb-DJI"


class TestLocalMicrophone:
    async def test_frames_reach_callbacks(self):
        mic = LocalMicrophone(command=fake_recorder(3))
        heard, raw = [], []

        async def on_audio(pcm):
            heard.append(pcm)

        mic.on_audio(on_audio)
        mic.on_raw(raw.append)
        await mic.start()
        try:
            await wait_for(lambda: len(heard) == 3)
            await wait_for(lambda: mic.last_error is not None)  # the recorder exited: its last words are kept
        finally:
            await mic.stop()
        assert [p[0] for p in heard] == [0, 1, 2] and all(len(p) == FRAME_BYTES for p in heard)
        assert raw == heard and mic.frames == 3
        assert mic.last_error == "device unplugged" and mic.connected is False

    async def test_muted_frames_are_dropped_but_recorded(self):
        muted = iter([False, True, False])
        mic = LocalMicrophone(command=fake_recorder(3), muted=lambda: next(muted))
        heard, raw = [], []

        async def on_audio(pcm):
            heard.append(pcm[0])

        mic.on_audio(on_audio)
        mic.on_raw(lambda pcm: raw.append(pcm[0]))
        await mic.start()
        try:
            await wait_for(lambda: mic.frames == 3)
        finally:
            await mic.stop()
        assert heard == [0, 2] and raw == [0, 1, 2] and mic.dropped == 1

    async def test_restarts_after_the_recorder_exits(self):
        mic = LocalMicrophone(command=fake_recorder(1, exit_code=1))
        mic.RESTART_DELAY = 0.05
        raw = []
        mic.on_raw(raw.append)
        await mic.start()
        try:
            await wait_for(lambda: mic.frames >= 2)
        finally:
            await mic.stop()
        # the second run starts with silence for the time between the two, keeping the recording's timeline
        assert raw[1] == b"\x00\x00" * (len(raw[1]) // 2) and len(raw[1]) >= 2 * int(0.05 * 16000)

    async def test_missing_recorder(self):
        mic = LocalMicrophone(command=["no-such-recorder-for-pepper"])
        await mic.start()
        try:
            await wait_for(lambda: mic._task.done())
        finally:
            await mic.stop()
        assert "not found" in mic.last_error


class TestSpeechClock:
    def test_counts_speech_and_its_tail(self):
        now = [100.0]
        clock = SpeechClock(clock=lambda: now[0])
        assert not clock.active()
        clock.begin()
        assert clock.active()
        clock.end("Hello there", waited=True)
        assert clock.active()  # the tail: room echo, the last syllable
        now[0] += SPEECH_TAIL + 0.01
        assert not clock.active()

    def test_speech_not_waited_for_is_estimated(self):
        now = [0.0]
        clock = SpeechClock(clock=lambda: now[0])
        clock.begin()
        clock.end("one two three four five six seven eight nine ten", waited=False)
        now[0] = 3.0
        assert clock.active()  # ten words take a few seconds
        now[0] = 10.0
        assert not clock.active()

    async def test_fake_bridge_speech_counts(self):
        bridge = FakeBridgeClient()
        bridge.speech_delay = 0.05
        task = asyncio.ensure_future(bridge.speak("Hello"))
        await asyncio.sleep(0.01)
        assert bridge.speech.speaking == 1
        await task
        assert bridge.speech.speaking == 0 and bridge.speech.active()


class TestPepperSpeaking:
    def app(self):
        app = main.PepperEvolution.__new__(main.PepperEvolution)
        app.robot = MagicMock()
        app.robot.bridge.speech = SpeechClock()
        app.robot_microphone = AudioStream("ws://h/ws/audio")
        return app

    def test_host_speech_mutes(self):
        app = self.app()
        assert app._pepper_speaking() is False
        app.robot.bridge.speech.begin()
        assert app._pepper_speaking() is True

    def test_robot_stream_pause_mutes(self):
        app = self.app()
        stream = app.robot_microphone
        stream.connected = True
        stream.last_frame_at = time.monotonic()
        assert app._pepper_speaking() is False  # frames arriving: Pepper is quiet
        stream.last_frame_at = time.monotonic() - 1.0
        assert app._pepper_speaking() is True  # the bridge stopped sending: Pepper speaks (any source)
        stream.last_frame_at = time.monotonic() - 60.0
        assert app._pepper_speaking() is False  # stuck for a minute: not speech, do not go deaf
        stream.connected = False
        stream.last_frame_at = time.monotonic() - 1.0
        assert app._pepper_speaking() is False


def test_build_voice_with_a_local_microphone(monkeypatch):
    app = main.PepperEvolution.__new__(main.PepperEvolution)
    app.logger = MagicMock()
    app.ai_manager = MagicMock()
    app.recorder = None
    monkeypatch.setenv("STT_BACKEND", "fake")
    monkeypatch.setenv("VOICE_INPUT", "true")
    monkeypatch.setenv("LOCAL_MICROPHONE", "alsa_input.usb-DJI")
    monkeypatch.setenv("PEPPER_FAKE_BRIDGE", "false")
    monkeypatch.setenv("BEAMFORM", "false")
    app.settings = main.Settings.from_env()
    voice = app.build_voice(ConnectionConfig(ip="1.2.3.4"))
    assert isinstance(voice.source, LocalMicrophone) and voice.source.device == "alsa_input.usb-DJI"
    assert voice.source.muted == app._pepper_speaking
    assert isinstance(app.robot_microphone, AudioStream)  # still connected: it tells when Pepper speaks
    assert voice.status()["microphone"] == "local" and voice.status()["robot_microphone"] is False
    app.settings.fake_bridge = True  # desk tests: the local microphone works without a robot
    voice = app.build_voice(ConnectionConfig(ip="1.2.3.4"))
    assert isinstance(voice.source, LocalMicrophone) and app.robot_microphone is None
