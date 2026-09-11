"""
Voice input: microphone audio -> transcript -> ``AIManager.process_user_input``.

Two ways in:

- the robot's own microphone, streamed by the bridge (:class:`~src.pepper.audio_stream.AudioStream`),
  cut into utterances here (batch backends) or by the recogniser itself (streaming backends);
- push-to-talk from the web UI, which posts one complete utterance to ``/voice/utterance``.

Control phrases ("stop", "be quiet") take the same path and are handled by
the manager's local intents without a model call.
"""

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from loguru import logger

from .endpointer import Endpointer
from .pcm import SAMPLE_RATE, duration, resample, wav_bytes
from .stt import Transcriber, Transcript


class AudioSource(Protocol):
    connected: bool

    def on_audio(self, callback: Callable[[bytes], Awaitable[None]]) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


TranscriptCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class VoiceInput:
    """Feeds what people say to the AI manager."""

    def __init__(
        self,
        manager: Any,
        transcriber: Transcriber,
        source: Optional[AudioSource] = None,
        endpointer: Optional[Endpointer] = None,
        record_dir: Optional[str] = None,
        min_chars: int = 2,
    ):
        self.manager = manager
        self.transcriber = transcriber
        self.source = source
        self.endpointer = endpointer or Endpointer()
        self.record_dir = record_dir
        self.min_chars = min_chars
        self.logger = logger.bind(module="VoiceInput")
        self.started = False
        self.utterances = 0
        self.transcripts = 0  # finals handed to the manager
        self.ignored = 0  # finals with nothing usable in them
        self.last_transcript: Optional[str] = None
        self.last_error: Optional[str] = None
        self._callbacks: List[TranscriptCallback] = []
        self._tasks: set = set()
        self._listening = False
        self._loaded = False
        self._saved = 0

    def on_transcript(self, callback: TranscriptCallback):
        """Register an async callback for partial and final transcripts (dict payloads)."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def load(self):
        """Load the speech model (slow, may fail: bad model path, no download). Safe to call twice."""
        if self._loaded:
            return
        started = time.monotonic()
        await self.transcriber.start()
        self.logger.info(
            f"Speech recognition ready: {self.transcriber.name} "
            f"({'streaming' if self.transcriber.streaming else 'per utterance'}) in {time.monotonic() - started:.1f}s"
        )
        if self.record_dir:
            os.makedirs(self.record_dir, exist_ok=True)
        self._loaded = True

    async def start(self):
        await self.load()
        if self.source is not None:
            self.source.on_audio(self._on_audio)
            await self.source.start()
            self.logger.info("Listening to the robot microphone")
        self.started = True

    async def stop(self):
        self.started = False
        if self.source is not None:
            await self.source.stop()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        await self.transcriber.close()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "backend": self.transcriber.name,
            "streaming": self.transcriber.streaming,
            "started": self.started,
            "robot_microphone": self.source is not None,
            "source_connected": bool(self.source is not None and getattr(self.source, "connected", False)),
            "listening": self._listening,
            "utterances": self.utterances,
            "transcripts": self.transcripts,
            "ignored": self.ignored,
            "last_transcript": self.last_transcript,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # Robot microphone path
    # ------------------------------------------------------------------

    async def _on_audio(self, pcm: bytes):
        try:
            if self.transcriber.streaming:
                for transcript in await self.transcriber.process(pcm):
                    if transcript.final:
                        self.utterances += 1
                        self._spawn(self._deliver(transcript, "voice"))  # the AI turn must not stall the audio
                    else:
                        await self._set_listening(True)
                        await self._deliver(transcript, "voice")
                return
            was_speaking = self.endpointer.speaking
            utterances = self.endpointer.feed(pcm)
            if not was_speaking and self.endpointer.speaking:
                await self._set_listening(True)
            for utterance in utterances:
                self.utterances += 1
                self._spawn(self._transcribe_and_deliver(utterance.pcm, "voice", None))
        except Exception as exc:  # noqa: BLE001 - never kill the audio stream
            self.last_error = str(exc)
            self.logger.error(f"Voice input error: {exc}")

    def _spawn(self, coro: Awaitable[Any]):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # Push-to-talk path (one complete utterance)
    # ------------------------------------------------------------------

    async def handle_utterance(
        self, pcm: bytes, sample_rate: int = SAMPLE_RATE, client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if sample_rate != self.transcriber.sample_rate:
            loop = asyncio.get_running_loop()  # pure-Python resampling; keep it off the event loop
            pcm = await loop.run_in_executor(None, resample, pcm, sample_rate, self.transcriber.sample_rate)
        self.utterances += 1
        await self._set_listening(True)
        result = await self._transcribe_and_deliver(pcm, "ptt", client_id)
        return result or {"text": "", "ignored": "error", "error": self.last_error}

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    async def _transcribe_and_deliver(self, pcm: bytes, source: str, client_id: Optional[str]) -> Optional[Dict]:
        if self.record_dir:
            self._save(pcm)
        try:
            transcript = await self.transcriber.transcribe(pcm)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.logger.error(f"Transcription failed: {exc}")
            await self._set_listening(False)
            return None
        self.logger.info(
            f"[{source}] {transcript.text!r} ({duration(pcm):.1f}s audio, {transcript.latency:.2f}s recognition)"
        )
        return await self._deliver(transcript, source, client_id)

    async def _deliver(self, transcript: Transcript, source: str, client_id: Optional[str] = None) -> Optional[Dict]:
        text = transcript.text.strip()
        payload: Dict[str, Any] = {
            "text": text,
            "final": transcript.final,
            "source": source,
            "client_id": client_id,
            "delivered": False,
        }
        if not transcript.final:
            await self._notify(payload)
            return None
        usable = len(text) >= self.min_chars and any(ch.isalnum() for ch in text)
        if not usable:
            self.ignored += 1
            payload["ignored"] = "empty"
            await self._notify(payload)
            await self._set_listening(False)
            return {"text": text, "ignored": "empty"}
        self.transcripts += 1
        self.last_transcript = text
        payload["delivered"] = True
        await self._notify(payload)
        self._listening = False  # the manager's own state signals take over from here
        result = await self.manager.process_user_input(text, source="voice", client_id=client_id)
        return {"text": text, "response": result}

    async def _notify(self, payload: Dict[str, Any]):
        for cb in self._callbacks:
            try:
                await cb(payload)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(f"transcript callback failed: {exc}")

    async def _set_listening(self, listening: bool):
        if listening == self._listening:
            return
        self._listening = listening
        signal = getattr(self.manager, "signal_state", None)
        if signal is not None:
            await signal("listening" if listening else "idle")

    def _save(self, pcm: bytes):
        try:
            self._saved += 1
            name = time.strftime("utterance_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}_{self._saved}.wav"
            path = os.path.join(self.record_dir or ".", name)
            with open(path, "wb") as fh:
                fh.write(wav_bytes(pcm, self.transcriber.sample_rate))
        except OSError as exc:
            self.logger.warning(f"could not save utterance: {exc}")
