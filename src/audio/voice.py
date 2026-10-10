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
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from loguru import logger

from .continuation import HOLD, MAX_HOLD, unfinished
from .endpointer import Endpointer
from .pcm import BYTES_PER_SAMPLE, SAMPLE_RATE, duration, resample, wav_bytes
from .stt import Transcriber, Transcript


class AudioSource(Protocol):
    connected: bool

    def on_audio(self, callback: Callable[[bytes], Awaitable[None]]) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


TranscriptCallback = Callable[[Dict[str, Any]], Awaitable[None]]

RECORD_WINDOW_SECONDS = 30.0  # streaming path: how much recent microphone audio is kept for saving
RECORD_PRE_ROLL_SECONDS = 1.5  # saved before the recogniser's first word, so onsets are not clipped


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
        continuation: bool = True,
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
        self._recent_audio = bytearray()  # streaming path: rolling window for VOICE_RECORD_DIR
        # #26: an utterance that clearly stops mid-sentence waits for the rest instead of being answered alone
        self.continuation = continuation
        self.joined = 0  # finals joined to the one before
        self._held: Optional[Transcript] = None
        self._hold_task: Optional[asyncio.Task] = None

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
                if self.record_dir:
                    self._keep_recent(pcm)
                for transcript in await self.transcriber.process(pcm):
                    if transcript.final:
                        self.utterances += 1
                        transcript.at = time.monotonic()
                        if self.record_dir:
                            self._spawn(self._record_streamed(transcript))
                        self._final(transcript)
                    else:
                        await self._set_listening(True)
                        if self._held is not None:
                            self._hold(MAX_HOLD)  # they are going on: wait for the end of this part
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

    # ------------------------------------------------------------------
    # Unfinished sentences (#26)
    # ------------------------------------------------------------------

    def _final(self, transcript: Transcript):
        """A final from the robot microphone: join it to a held fragment, hold it if it is unfinished itself, or
        hand it on. Delivery runs as its own task: the AI turn must not stall the audio."""
        if self._held is not None:
            transcript = self._join(self._held, transcript)
            self._held = None
            self._cancel_hold()
        if self.continuation and unfinished(transcript.text):
            self._held = transcript
            self.logger.info(f"[voice] {transcript.text!r} sounds unfinished: waiting for the rest")
            self._hold(HOLD)
            return
        self._spawn(self._deliver(transcript, "voice"))

    def _join(self, first: Transcript, second: Transcript) -> Transcript:
        rest = second.text.strip()
        if rest[:1].isupper() and not re.match(r"^(I\b|I'|Pepper\b)", rest):
            rest = rest[0].lower() + rest[1:]  # the recogniser starts every final with a capital
        gap = max(0.0, (second.at or 0.0) - (first.at or 0.0) - second.duration) if second.at and first.at else 0.0
        self.joined += 1
        self.logger.info(f"[voice] joined across a pause: {first.text!r} + {second.text!r}")
        return Transcript(
            text=f"{first.text.strip()} {rest}",
            final=True,
            duration=first.duration + gap + second.duration,
            latency=second.latency,
            backend=second.backend,
            at=second.at,
            parts=first.parts + second.parts,
        )

    def _hold(self, seconds: float):
        self._cancel_hold()
        self._hold_task = asyncio.ensure_future(self._release_after(seconds))

    def _cancel_hold(self):
        if self._hold_task is not None and not self._hold_task.done():
            self._hold_task.cancel()
        self._hold_task = None

    async def _release_after(self, seconds: float):
        """Nothing more came: answer what was said, as it was."""
        await asyncio.sleep(seconds)
        held, self._held = self._held, None
        if held is not None:
            self.logger.info(f"[voice] no more after {seconds:.1f}s: answering {held.text!r}")
            self._spawn(self._deliver(held, "voice"))

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
        try:
            transcript = await self.transcriber.transcribe(pcm)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.logger.error(f"Transcription failed: {exc}")
            if self.record_dir:
                await asyncio.to_thread(self._save, pcm, None)
            await self._set_listening(False)
            return None
        if self.record_dir:
            await asyncio.to_thread(self._save, pcm, transcript.text)
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
        # open_mic: heard by the robot's microphone, so it may not be meant for Pepper (push-to-talk always is)
        result = await self.manager.process_user_input(
            text,
            source="voice",
            client_id=client_id,
            heard_at=transcript.at or time.monotonic(),  # a held fragment counts from when it was heard
            open_mic=source == "voice",
            spoke_for=transcript.duration or None,
            **({"parts": transcript.parts} if transcript.parts > 1 else {}),
        )
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

    # ------------------------------------------------------------------
    # Recording (VOICE_RECORD_DIR), for tuning and comparing recognisers
    # ------------------------------------------------------------------

    def _keep_recent(self, pcm: bytes):
        self._recent_audio.extend(pcm)
        limit = int(RECORD_WINDOW_SECONDS * self.transcriber.sample_rate) * BYTES_PER_SAMPLE
        if len(self._recent_audio) > limit:
            del self._recent_audio[: len(self._recent_audio) - limit]

    async def _record_streamed(self, transcript: Transcript):
        """Save the audio a streaming final came from: its duration plus a pre-roll, from the rolling window."""
        seconds = (transcript.duration or RECORD_WINDOW_SECONDS) + RECORD_PRE_ROLL_SECONDS
        take = int(seconds * self.transcriber.sample_rate) * BYTES_PER_SAMPLE
        pcm = bytes(self._recent_audio[-take:])
        self._recent_audio.clear()  # the next utterance starts after this one
        if pcm:
            await asyncio.to_thread(self._save, pcm, transcript.text)

    def _save(self, pcm: bytes, text: Optional[str] = None) -> Optional[str]:
        """Write ``utterance_*.wav`` and, when known, the recogniser's transcript as ``utterance_*.hyp.txt``.

        A hand-corrected ``utterance_*.ref.txt`` next to it turns the folder into a test set for
        ``scripts/compare_stt.py``.
        """
        try:
            self._saved += 1
            stem = time.strftime("utterance_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}_{self._saved}"
            path = os.path.join(self.record_dir or ".", stem + ".wav")
            with open(path, "wb") as fh:
                fh.write(wav_bytes(pcm, self.transcriber.sample_rate))
            if text is not None:
                with open(os.path.join(self.record_dir or ".", stem + ".hyp.txt"), "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            return path
        except OSError as exc:
            self.logger.warning(f"could not save utterance: {exc}")
            return None
