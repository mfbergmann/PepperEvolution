"""
Utterance detection for batch speech-to-text backends.

Streams of PCM are cut into utterances: speech starts when a few consecutive
frames are above the noise floor, and ends after a stretch of silence. The
detector is deliberately simple (energy based, adaptive threshold) so the
host has no extra dependencies; the streaming backend (sherpa-onnx) has its
own, better endpointing and does not use this module.
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

from .pcm import BYTES_PER_SAMPLE, SAMPLE_RATE, rms


@dataclass
class Utterance:
    pcm: bytes
    started_at: float  # monotonic time of the first speech frame
    duration: float  # seconds of audio in ``pcm``
    sample_rate: int = SAMPLE_RATE


class EnergyVAD:
    """Speech when a frame's level exceeds the (slowly tracked) noise floor by a margin."""

    def __init__(self, min_rms: float = 400.0, ratio: float = 3.0, floor_alpha: float = 0.05):
        self.min_rms = min_rms  # absolute floor: quieter frames are never speech
        self.ratio = ratio  # speech must be this many times louder than the background
        self.floor_alpha = floor_alpha  # how fast the background estimate follows quiet frames
        self.noise_floor = min_rms
        self.last_level = 0.0

    def is_speech(self, frame: bytes) -> bool:
        level = rms(frame)
        self.last_level = level
        threshold = max(self.min_rms, self.noise_floor * self.ratio)
        speech = level > threshold
        if not speech:  # only quiet frames update the background estimate
            self.noise_floor += self.floor_alpha * (level - self.noise_floor)
        return speech

    def reset(self):
        self.noise_floor = self.min_rms


class Endpointer:
    """Turns a PCM stream into :class:`Utterance` objects."""

    def __init__(
        self,
        vad: Optional[EnergyVAD] = None,
        sample_rate: int = SAMPLE_RATE,
        frame_ms: int = 20,
        start_ms: int = 100,
        end_silence_ms: int = 800,
        pre_roll_ms: int = 300,
        min_utterance_ms: int = 300,
        max_utterance_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.vad = vad or EnergyVAD()
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = sample_rate * frame_ms // 1000 * BYTES_PER_SAMPLE
        self.start_frames = max(1, start_ms // frame_ms)
        self.end_silence_frames = max(1, end_silence_ms // frame_ms)
        self.pre_roll_frames = max(0, pre_roll_ms // frame_ms)
        self.min_utterance_frames = max(1, min_utterance_ms // frame_ms)
        self.max_utterance_frames = max(self.min_utterance_frames, int(max_utterance_s * 1000) // frame_ms)
        self._clock = clock
        self._pending = b""
        self._recent: Deque[bytes] = deque(maxlen=self.pre_roll_frames + self.start_frames)
        self._run = 0  # consecutive speech frames while idle
        self._current: List[bytes] = []
        self._silence = 0  # consecutive silent frames while in speech
        self._speech_frames = 0
        self._started_at = 0.0
        self.speaking = False
        self.utterances = 0
        self.dropped = 0  # blips shorter than min_utterance_ms

    def feed(self, pcm: bytes) -> List[Utterance]:
        """Consume audio; return the utterances that ended in this chunk."""
        out: List[Utterance] = []
        data = self._pending + pcm
        offset = 0
        while offset + self.frame_bytes <= len(data):
            frame = data[offset : offset + self.frame_bytes]
            offset += self.frame_bytes
            utterance = self._frame(frame)
            if utterance is not None:
                out.append(utterance)
        self._pending = data[offset:]
        return out

    def flush(self) -> Optional[Utterance]:
        """End the current utterance now (push-to-talk release, shutdown)."""
        finished = None
        if self._pending:
            padding = b"\x00" * (self.frame_bytes - len(self._pending))
            finished = self._frame(self._pending + padding)  # the padded frame may itself end the utterance
            self._pending = b""
        if finished is not None:
            return finished
        if not self.speaking:
            return None
        return self._finish()

    def reset(self):
        self._pending = b""
        self._recent.clear()
        self._run = 0
        self._current = []
        self._silence = 0
        self._speech_frames = 0
        self.speaking = False
        self.vad.reset()

    def _frame(self, frame: bytes) -> Optional[Utterance]:
        speech = self.vad.is_speech(frame)
        if not self.speaking:
            self._recent.append(frame)
            if speech:
                self._run += 1
                if self._run >= self.start_frames:
                    self._begin()
            else:
                self._run = 0
            return None
        self._current.append(frame)
        if speech:
            self._silence = 0
            self._speech_frames += 1
        else:
            self._silence += 1
        if self._silence >= self.end_silence_frames or len(self._current) >= self.max_utterance_frames:
            return self._finish()
        return None

    def _begin(self):
        self.speaking = True
        self._started_at = self._clock()
        self._current = list(self._recent)  # pre-roll plus the frames that triggered the start
        self._recent.clear()
        self._speech_frames = self._run
        self._run = 0
        self._silence = 0

    def _finish(self) -> Optional[Utterance]:
        frames, speech_frames = self._current, self._speech_frames
        started_at = self._started_at
        self.speaking = False
        self._current = []
        self._silence = 0
        self._speech_frames = 0
        self._run = 0
        if speech_frames < self.min_utterance_frames:
            self.dropped += 1
            return None
        self.utterances += 1
        pcm = b"".join(frames)
        return Utterance(pcm=pcm, started_at=started_at, duration=len(frames) * self.frame_ms / 1000.0)
