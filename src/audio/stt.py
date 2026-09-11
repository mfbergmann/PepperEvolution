"""
Speech-to-text backends behind one small interface.

Two kinds of backend:

- **streaming** (``sherpa-onnx``): fed continuously, does its own endpointing
  and returns partial and final transcripts as they happen.
- **batch** (``faster-whisper``): given one utterance at a time (cut by the
  :class:`~src.audio.endpointer.Endpointer` or by push-to-talk).

Both are optional dependencies; :func:`make_transcriber` explains what to
install when one is missing. :class:`FakeTranscriber` is for tests.
"""

import asyncio
import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, List, Optional

from loguru import logger

from .pcm import SAMPLE_RATE, duration, to_float32


@dataclass
class Transcript:
    text: str
    final: bool
    duration: float = 0.0  # seconds of audio
    latency: float = 0.0  # seconds spent recognising
    backend: str = ""


class TranscriberUnavailable(RuntimeError):
    """The requested backend cannot run here (missing package or model)."""


class Transcriber:
    """Base class; subclasses set ``name`` and ``streaming`` and implement the methods they support."""

    name = "base"
    streaming = False
    sample_rate = SAMPLE_RATE

    async def start(self):
        """Load models (may take seconds; runs once)."""

    async def close(self):
        pass

    async def transcribe(self, pcm: bytes) -> Transcript:
        """Recognise one complete utterance."""
        raise NotImplementedError

    async def process(self, pcm: bytes) -> List[Transcript]:
        """Streaming backends: consume a chunk, return partial/final transcripts."""
        raise NotImplementedError

    async def flush(self) -> List[Transcript]:
        """Streaming backends: end the current utterance now."""
        return []

    def reset(self):
        pass


class FakeTranscriber(Transcriber):
    """Returns scripted texts. Streaming mode emits a final after ``endpoint_after`` seconds of audio."""

    name = "fake"

    def __init__(self, script: Optional[List[str]] = None, streaming: bool = False, endpoint_after: float = 1.0):
        self.script = list(script or [])
        self.streaming = streaming
        self.endpoint_after = endpoint_after
        self.received: List[bytes] = []
        self.started = False
        self._buffer = b""
        self._partial_sent = False

    async def start(self):
        self.started = True

    def _next_text(self) -> str:
        return self.script.pop(0) if self.script else ""

    async def transcribe(self, pcm: bytes) -> Transcript:
        self.received.append(pcm)
        return Transcript(text=self._next_text(), final=True, duration=duration(pcm), backend=self.name)

    async def process(self, pcm: bytes) -> List[Transcript]:
        self._buffer += pcm
        secs = duration(self._buffer)
        if secs >= self.endpoint_after:
            return await self.flush()
        if secs >= self.endpoint_after / 2 and not self._partial_sent and self.script:
            self._partial_sent = True
            return [Transcript(text=self.script[0].split(" ")[0], final=False, backend=self.name)]
        return []

    async def flush(self) -> List[Transcript]:
        if not self._buffer:
            return []
        pcm, self._buffer = self._buffer, b""
        self._partial_sent = False
        return [await self.transcribe(pcm)]

    def reset(self):
        self._buffer = b""
        self._partial_sent = False


class WhisperTranscriber(Transcriber):
    """faster-whisper (CTranslate2) on the CPU; one utterance per call, ~0.2-0.7 s for a short sentence."""

    name = "whisper"

    def __init__(
        self,
        model: str = "base",
        language: Optional[str] = "en",
        compute_type: str = "int8",
        device: str = "cpu",
        beam_size: int = 1,
        cpu_threads: int = 0,
    ):
        self.model_name = model or "base"
        self.language = language or None
        self.compute_type = compute_type
        self.device = device
        self.beam_size = beam_size
        self.cpu_threads = cpu_threads
        self._model: Any = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self.logger = logger.bind(module="Whisper")

    async def start(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise TranscriberUnavailable("faster-whisper is not installed: pip install faster-whisper") from exc
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        self._model = await loop.run_in_executor(
            self._executor,
            lambda: WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type, cpu_threads=self.cpu_threads
            ),
        )
        self.logger.info(
            f"Whisper model {self.model_name!r} ({self.compute_type}) ready in {time.monotonic() - started:.1f}s"
        )

    async def close(self):
        self._executor.shutdown(wait=False)

    async def transcribe(self, pcm: bytes) -> Transcript:
        if self._model is None:
            await self.start()
        audio = to_float32(pcm)
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        text = await loop.run_in_executor(self._executor, self._run, audio)
        return Transcript(
            text=text,
            final=True,
            duration=duration(pcm),
            latency=time.monotonic() - started,
            backend=self.name,
        )

    def _run(self, audio: Any) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        parts = []
        for seg in segments:
            # Whisper invents text for silence; skip segments it is unsure about.
            if getattr(seg, "no_speech_prob", 0.0) > 0.7 or getattr(seg, "compression_ratio", 1.0) > 2.4:
                continue
            parts.append(seg.text.strip())
        return " ".join(p for p in parts if p).strip()


SHERPA_TAIL_SECONDS = 0.66  # what sherpa-onnx's own examples feed before input_finished()


class SherpaTranscriber(Transcriber):
    """sherpa-onnx streaming transducer (zipformer / Nemotron streaming) with built-in endpointing."""

    name = "sherpa"
    streaming = True

    def __init__(
        self,
        model_dir: str,
        num_threads: int = 2,
        trailing_silence: float = 1.0,
        decoding_method: str = "greedy_search",
    ):
        self.model_dir = os.path.expanduser(model_dir or "")
        self.num_threads = num_threads
        self.trailing_silence = trailing_silence
        self.decoding_method = decoding_method
        self._recognizer: Any = None
        self._stream: Any = None
        self._last_partial = ""
        self._utterance_started: Optional[float] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sherpa")
        self.logger = logger.bind(module="Sherpa")

    @staticmethod
    def _pick(model_dir: str, stem: str) -> str:
        candidates = sorted(glob.glob(os.path.join(model_dir, f"{stem}*.onnx")))
        if not candidates:
            raise TranscriberUnavailable(f"no {stem}*.onnx in {model_dir}")
        int8 = [c for c in candidates if "int8" in os.path.basename(c)]
        return (int8 or candidates)[0]

    async def start(self):
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise TranscriberUnavailable("sherpa-onnx is not installed: pip install sherpa-onnx") from exc
        if not self.model_dir or not os.path.isdir(self.model_dir):
            raise TranscriberUnavailable(
                "STT_MODEL must point at a sherpa-onnx streaming transducer directory, e.g. "
                "sherpa-onnx-streaming-zipformer-en-2023-06-26 from "
                "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"
            )
        tokens = os.path.join(self.model_dir, "tokens.txt")
        if not os.path.exists(tokens):
            raise TranscriberUnavailable(f"no tokens.txt in {self.model_dir}")
        encoder, decoder, joiner = (self._pick(self.model_dir, s) for s in ("encoder", "decoder", "joiner"))
        loop = asyncio.get_running_loop()
        started = time.monotonic()

        def load():
            return sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tokens,
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=self.trailing_silence,
                rule3_min_utterance_length=300,
                decoding_method=self.decoding_method,
            )

        self._recognizer = await loop.run_in_executor(self._executor, load)
        self._stream = self._recognizer.create_stream()
        self.logger.info(f"sherpa-onnx model {os.path.basename(encoder)} ready in {time.monotonic() - started:.1f}s")

    async def close(self):
        self._executor.shutdown(wait=False)

    def _result_text(self, stream: Any) -> str:
        result = self._recognizer.get_result(stream)
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        return text.strip()

    def _decode(self, stream: Any):
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)

    def _finish_stream(self, stream: Any):
        """Pad with silence, then close: the streaming zipformer needs right context for the last word."""
        stream.accept_waveform(self.sample_rate, to_float32(b"\x00\x00" * int(SHERPA_TAIL_SECONDS * self.sample_rate)))
        stream.input_finished()
        self._decode(stream)

    async def process(self, pcm: bytes) -> List[Transcript]:
        if self._recognizer is None:
            await self.start()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._process_sync, pcm)

    def _process_sync(self, pcm: bytes) -> List[Transcript]:
        now = time.monotonic()
        self._stream.accept_waveform(self.sample_rate, to_float32(pcm))
        self._decode(self._stream)
        text = self._result_text(self._stream)
        out: List[Transcript] = []
        if text and self._utterance_started is None:
            self._utterance_started = now
        if self._recognizer.is_endpoint(self._stream):
            if text:
                out.append(self._final(text, now))
            self._recognizer.reset(self._stream)
            self._last_partial = ""
            self._utterance_started = None
        elif text and text != self._last_partial:
            self._last_partial = text
            out.append(Transcript(text=text, final=False, backend=self.name))
        return out

    def _final(self, text: str, now: float) -> Transcript:
        started = self._utterance_started or now
        return Transcript(text=text, final=True, duration=now - started, latency=0.0, backend=self.name)

    async def flush(self) -> List[Transcript]:
        if self._recognizer is None:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._flush_sync)

    def _flush_sync(self) -> List[Transcript]:
        now = time.monotonic()
        self._finish_stream(self._stream)
        text = self._result_text(self._stream)
        self._stream = self._recognizer.create_stream()
        self._last_partial = ""
        out = [self._final(text, now)] if text else []
        self._utterance_started = None
        return out

    async def transcribe(self, pcm: bytes) -> Transcript:
        """One-shot recognition of a complete utterance on a fresh stream (push-to-talk)."""
        if self._recognizer is None:
            await self.start()
        loop = asyncio.get_running_loop()
        started = time.monotonic()

        def run() -> str:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, to_float32(pcm))
            self._finish_stream(stream)
            return self._result_text(stream)

        text = await loop.run_in_executor(self._executor, run)
        return Transcript(
            text=text, final=True, duration=duration(pcm), latency=time.monotonic() - started, backend=self.name
        )

    def reset(self):
        if self._recognizer is not None:
            self._stream = self._recognizer.create_stream()
        self._last_partial = ""
        self._utterance_started = None


BACKENDS = ("none", "fake", "whisper", "sherpa")


def make_transcriber(backend: str, model: str = "", language: str = "en", **options: Any) -> Optional[Transcriber]:
    """Build a transcriber from settings; ``None`` for backend ``none``.

    Import errors surface here (not at first use) so a misconfigured host fails
    at startup with an installation hint.
    """
    backend = (backend or "none").strip().lower()
    if backend in ("none", "off", ""):
        return None
    if backend == "fake":
        return FakeTranscriber(**options)
    if backend == "whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise TranscriberUnavailable(
                "STT_BACKEND=whisper needs faster-whisper: pip install faster-whisper"
            ) from exc
        return WhisperTranscriber(model=model or "base", language=language, **options)
    if backend == "sherpa":
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError as exc:
            raise TranscriberUnavailable("STT_BACKEND=sherpa needs sherpa-onnx: pip install sherpa-onnx") from exc
        return SherpaTranscriber(model_dir=model, **options)
    raise ValueError(f"unknown STT_BACKEND {backend!r} (use one of {', '.join(BACKENDS)})")
