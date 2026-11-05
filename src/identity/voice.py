"""
Whose voice is this? (#14, #25) Compares each utterance with the voices enrolled in the long-term store.

Runs on the host's CPU, one comparison at a time on its own thread, so the audio loop never waits for it. The audio and
the embeddings made from it live only for the length of a call: nothing is written anywhere, and someone who is not
enrolled leaves no trace. What comes out is a :class:`VoiceReading`, made of numbers and, when confident, a name.

How the numbers are used, thresholds included, is in docs/MEMORY.md ("Identity"), with what they were measured on.
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from .compare import Enrolled, best_match, closest_score, load_enrolled, unit

KIND = "voice"
DEFAULT_MODEL = "~/.local/share/pepper-models/3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx"
SAMPLE_RATE = 16000
MIN_CLIP_SECONDS = 1.5  # shorter clips say too little about a voice
WINDOW_SECONDS = 1.5
HOP_SECONDS = 0.75
MAX_WINDOWS = (
    8  # evenly over a long clip: about 0.09 s each on 4 desktop threads, and no worse on the training sessions
)
LOWEST_WINDOWS = 2  # the clip's "low" score is the mean of this many of its worst windows

# Set from the held-out evaluation, docs/MEMORY.md ("Identity"); both can be overridden in .env
CONFIDENT_SCORE = 0.40  # a whole clip this close to someone enrolled: "the voice sounds like them"
SECOND_VOICE_SCORE = 0.15  # a clip's low score under this: part of it is not the enrolled voice
PRESENT_SCORE = 0.40  # a window this close: the enrolled voice is in the clip, so another one is a second voice
ENROLLED_RECENT = 300.0  # seconds after the enrolled voice was last heard in which a voice unlike it is a second voice
SECOND_VOICE_WINDOW = 60.0  # seconds a second voice keeps the conversation bypass of the addressee gate off


def samples_from(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def window_starts(length: int, rate: int = SAMPLE_RATE) -> List[int]:
    """Where the 1.5 s windows of a clip of ``length`` samples start. A clip of one window has one."""
    window, hop = int(WINDOW_SECONDS * rate), int(HOP_SECONDS * rate)
    starts = list(range(0, max(1, length - window), hop))
    if len(starts) <= MAX_WINDOWS:
        return starts
    return [starts[round(i * (len(starts) - 1) / (MAX_WINDOWS - 1))] for i in range(MAX_WINDOWS)]


@dataclass(frozen=True)
class VoiceReading:
    """What one utterance's voice was like. ``score``, ``low`` and ``high`` are ``None`` with nobody to compare with
    or a clip too short to say."""

    seconds: float  # of audio
    windows: int = 0
    score: Optional[float] = None  # the whole clip against the closest enrolled voice
    low: Optional[float] = None  # the mean of its worst windows, each against the closest enrolled voice
    high: Optional[float] = None  # its best window
    person_id: Optional[int] = None  # the closest enrolled person, when ``name`` is set
    name: Optional[str] = None  # set only when confident: the whole clip is close and no part of it is far
    second_voice: bool = False  # the low score says part of this clip is not an enrolled voice
    spent: float = 0.0  # seconds of CPU thread time

    def record(self) -> Dict[str, Any]:
        """For the session record (turns.jsonl): numbers and, if confident, the name. Never an embedding."""
        out = {k: round(v, 3) if isinstance(v, float) else v for k, v in self.__dict__.items() if v is not None}
        if not self.second_voice:
            out.pop("second_voice", None)
        return out


def read_voice(
    embed: Callable[[np.ndarray], np.ndarray],
    samples: np.ndarray,
    enrolled: Sequence[Enrolled],
    confident: float = CONFIDENT_SCORE,
    second_voice: float = SECOND_VOICE_SCORE,
    rate: int = SAMPLE_RATE,
) -> VoiceReading:
    """Compare one clip with the enrolled voices. ``embed`` turns samples into a unit vector."""
    seconds = len(samples) / rate
    if not enrolled or seconds < MIN_CLIP_SECONDS:
        return VoiceReading(seconds)
    whole = best_match(embed(samples), enrolled)
    assert whole is not None
    span = int(WINDOW_SECONDS * rate)
    scores = sorted(closest_score(embed(samples[i : i + span]), enrolled) for i in window_starts(len(samples), rate))
    low = float(np.mean(scores[:LOWEST_WINDOWS]))
    mixed = low < second_voice
    known = whole.score >= confident and not mixed
    return VoiceReading(
        seconds,
        windows=len(scores),
        score=whole.score,
        low=low,
        high=scores[-1],
        person_id=whole.person_id if known else None,
        name=whole.name if known else None,
        second_voice=mixed,
    )


def merge_readings(readings: Sequence[VoiceReading]) -> VoiceReading:
    """One utterance joined across a pause (#26): the worst part decides whether another voice is in it, and the name
    stands only if every part that could tell agrees on it."""
    if len(readings) == 1:
        return readings[0]
    told = [r for r in readings if r.score is not None]
    people = {r.person_id for r in told}
    mixed = any(r.second_voice for r in told)
    only = next(iter(people)) if len(people) == 1 and not mixed else None
    return VoiceReading(
        sum(r.seconds for r in readings),
        windows=sum(r.windows for r in readings),
        score=max((r.score for r in told), default=None),
        low=min((r.low for r in told), default=None),
        high=max((r.high for r in told), default=None),
        person_id=only,
        name=next((r.name for r in told), None) if only is not None else None,
        second_voice=mixed,
        spent=sum(r.spent for r in readings),
    )


class SecondVoiceWatch:
    """Has someone other than the enrolled voice spoken lately? Clock-free: the caller passes the time.

    A low score alone does not mean a second voice. Everyone who is not enrolled scores low, so a visitor alone with
    Pepper would look like side talk from their first word. It counts only when the enrolled voice is in the same clip
    or was heard shortly before: then there are two people.
    """

    def __init__(
        self,
        window: float = SECOND_VOICE_WINDOW,
        present: float = PRESENT_SCORE,
        enrolled_recent: float = ENROLLED_RECENT,
    ):
        self.window = window
        self.present = present
        self.enrolled_recent = enrolled_recent
        self.heard_at: Optional[float] = None  # the last time another voice was noticed
        self.enrolled_at: Optional[float] = None

    def update(self, reading: VoiceReading, now: float) -> bool:
        """Take in one reading. True when it signals a second voice."""
        if reading.high is None:
            return False
        if reading.high >= self.present:
            self.enrolled_at = now
        signal = (
            reading.second_voice and self.enrolled_at is not None and now - self.enrolled_at <= self.enrolled_recent
        )
        if signal:
            self.heard_at = now
        return signal

    def active(self, now: float) -> bool:
        return self.heard_at is not None and now - self.heard_at < self.window


class SpeakerModel:
    """The speaker embedding model through sherpa-onnx. ``embed`` is not called from two threads at once."""

    def __init__(self, path: str = DEFAULT_MODEL, threads: int = 4):
        import sherpa_onnx

        self.path = os.path.expanduser(path)
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"speaker model not found: {self.path}")
        self.name = model_name(self.path)
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=self.path, num_threads=threads)
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        return unit(self._extractor.compute(stream))


def model_name(path: str) -> str:
    """The name an embedding is stored under: the file's, so moving the models folder changes nothing."""
    return os.path.splitext(os.path.basename(path))[0]


class VoiceIdentifier:
    """Reads voices on one worker thread. The enrolled voices are read from the store for each clip, on the caller's
    (event loop) thread, so "forget me" holds from the next word on."""

    def __init__(
        self,
        store: Any,
        model_path: str = DEFAULT_MODEL,
        confident: float = CONFIDENT_SCORE,
        second_voice: float = SECOND_VOICE_SCORE,
        threads: int = 4,  # faster than 2 (0.15 s a window against 0.19) and 8 (0.35 s) on the desktop, with other work
        model: Optional[Any] = None,
    ):
        self.store = store
        self.model_path = model_path
        self.confident = confident
        self.second_voice = second_voice
        self.threads = threads
        self.model = model
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-id")

    async def start(self) -> None:
        """Load the model (about a second; may fail: no file, no sherpa-onnx). The caller decides what failing means."""
        if self.model is None:
            loop = asyncio.get_running_loop()
            self.model = await loop.run_in_executor(self._pool, SpeakerModel, self.model_path, self.threads)

    def enrolled(self) -> List[Enrolled]:
        return load_enrolled(self.store, KIND, self.model.name)

    def read(self, pcm: bytes) -> "asyncio.Future[VoiceReading]":
        """Start reading a clip (16 kHz, 16-bit mono) and return at once; the future has the reading."""
        return asyncio.get_running_loop().run_in_executor(self._pool, self._read, pcm, self.enrolled())

    def _read(self, pcm: bytes, enrolled: List[Enrolled]) -> VoiceReading:
        started = time.monotonic()
        reading = read_voice(self.model.embed, samples_from(pcm), enrolled, self.confident, self.second_voice)
        return VoiceReading(**{**reading.__dict__, "spent": time.monotonic() - started})

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
