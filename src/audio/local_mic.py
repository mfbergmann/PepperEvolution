"""
A microphone on the host computer (LOCAL_MICROPHONE, issue #34): for example a wireless clip-on microphone worn on
Pepper's chest, whose receiver is plugged into the desktop. Away from the head, it hears much less of Pepper's fans.

Audio comes from ``parecord`` (PipeWire's or PulseAudio's recorder), which converts whatever the device gives
(often 48 kHz stereo) into 16 kHz mono 16-bit PCM, the format the robot's stream has. Frames are handed on in 85 ms
pieces, like the robot's. When the recorder exits (the receiver was unplugged), it is started again.

The robot's own microphones are muted by the bridge while Pepper speaks (it has no echo cancellation); a microphone
on its chest hears Pepper even louder. ``muted`` is asked for every frame, and frames are dropped while it says
Pepper is speaking, so speech recognition sees the same pauses as with the robot's stream. ``on_raw`` callbacks get
every frame, muted or not (for recording a session).
"""

import asyncio
import time
from typing import Awaitable, Callable, List, Optional, Sequence

from loguru import logger

SAMPLE_RATE = 16000
FRAME_BYTES = 2720  # 85 ms of 16 kHz mono 16-bit audio, as the robot sends

AudioCallback = Callable[[bytes], Awaitable[None]]
RawCallback = Callable[[bytes], None]


def recorder_command(device: str) -> List[str]:
    """``default`` records from the desktop's default input; any other value names a source (``pactl list short
    sources``)."""
    command = ["parecord", "--raw", "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1", "--latency-msec=40"]
    if device and device != "default":
        command.append(f"--device={device}")
    return command


class LocalMicrophone:
    """An audio source for :class:`~src.audio.voice.VoiceInput`, like the robot's microphone stream."""

    name = "local"
    RESTART_DELAY = 3.0

    def __init__(
        self,
        device: str = "default",
        muted: Callable[[], bool] = lambda: False,
        command: Optional[Sequence[str]] = None,
    ):
        self.device = device or "default"
        self.muted = muted
        self.command = list(command) if command else recorder_command(self.device)
        self.logger = logger.bind(module="LocalMicrophone")
        self.connected = False
        self.frames = 0
        self.dropped = 0  # frames dropped while Pepper spoke
        self.last_error: Optional[str] = None
        self._callbacks: List[AudioCallback] = []
        self._raw_callbacks: List[RawCallback] = []
        self._process: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stopped_at: Optional[float] = None

    def on_audio(self, callback: AudioCallback):
        self._callbacks.append(callback)

    def on_raw(self, callback: RawCallback):
        """Register a plain callback receiving every frame, also while muted (recording)."""
        self._raw_callbacks.append(callback)

    async def start(self):
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="local-microphone")

    async def stop(self):
        self._running = False
        self._kill()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.connected = False

    def _kill(self):
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    async def _loop(self):
        while self._running:
            try:
                await self._record()
            except asyncio.CancelledError:
                self._kill()
                break
            except FileNotFoundError:
                self.last_error = f"{self.command[0]} not found (install pipewire-pulse or pulseaudio-utils)"
                self.logger.error(f"Local microphone off: {self.last_error}")
                break
            except Exception as exc:  # noqa: BLE001 - keep trying, like the robot's stream
                self.last_error = str(exc)
                self.logger.warning(f"Local microphone failed: {exc}")
            self.connected = False
            self._stopped_at = time.monotonic()
            if self._running:
                await asyncio.sleep(self.RESTART_DELAY)

    async def _record(self):
        self._process = await asyncio.create_subprocess_exec(
            *self.command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert self._process.stdout is not None
        self.logger.info(f"Recording from the local microphone ({self.device})")
        first = True
        try:
            while True:
                try:
                    pcm = await self._process.stdout.readexactly(FRAME_BYTES)
                except asyncio.IncompleteReadError:
                    break
                if first:
                    first = False
                    self.connected = True
                    self._pad_recording()
                await self._dispatch(pcm)
        finally:
            self._kill()
            code = await self._process.wait()
            stderr = b""
            if self._process.stderr is not None:
                stderr = await self._process.stderr.read()
        if self._running:
            message = stderr.decode(errors="replace").strip().splitlines()
            self.last_error = message[-1] if message else f"recorder exited ({code})"
            self.logger.warning(
                f"Local microphone stopped: {self.last_error}. Trying again in {self.RESTART_DELAY:.0f}s..."
            )

    def _pad_recording(self):
        """After a restart, keep the recording's timeline: silence for the time nothing was recorded."""
        if self._stopped_at is None or not self._raw_callbacks:
            return
        silence = b"\x00\x00" * int((time.monotonic() - self._stopped_at) * SAMPLE_RATE)
        self._stopped_at = None
        self._raw(silence)

    def _raw(self, pcm: bytes):
        for callback in self._raw_callbacks:
            try:
                callback(pcm)
            except Exception as exc:  # noqa: BLE001 - recording must not stop the microphone
                self.logger.debug(f"raw audio callback failed: {exc}")

    async def _dispatch(self, pcm: bytes):
        self.frames += 1
        self._raw(pcm)
        try:
            if self.muted():
                self.dropped += 1
                return
        except Exception as exc:  # noqa: BLE001 - an unanswerable question mutes nothing
            self.logger.debug(f"mute check failed: {exc}")
        for callback in self._callbacks:
            try:
                await callback(pcm)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Audio callback error: {exc}")
