"""
Session records: one folder per host run, for reviewing real interactions afterwards.

With ``SESSION_DIR`` set, each run gets ``<SESSION_DIR>/<YYYY-MM-DD_HHMMSS>/`` holding the host log, utterance audio,
photos, ``turns.jsonl`` (one line per turn: what was heard, the decisions taken, timings, tools, what Pepper said)
and ``events.jsonl`` (people, touches, greetings, reflexes, camera judgements). ``scripts/review_session.py`` turns a
folder into a readable, timed transcript. The records hold real people's voices and images: they stay on the host
machine and out of git (see CLAUDE.md, "Session records").
"""

import json
import os
import threading
import time
import wave
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger


class SessionRecorder:
    """Appends JSON lines to the session folder; never raises into the caller."""

    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self._wall0 = time.time()
        self._mono0 = time.monotonic()
        self._lock = threading.Lock()
        self.logger = logger.bind(module="SessionRecorder")

    @classmethod
    def start(cls, root: str, now: Optional[datetime] = None) -> "SessionRecorder":
        stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        return cls(os.path.join(root, stamp))

    def path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    def wall(self, mono: Optional[float]) -> Optional[str]:
        """A ``time.monotonic()`` reading as local wall-clock time (ISO, milliseconds)."""
        if mono is None:
            return None
        return datetime.fromtimestamp(self._wall0 + (mono - self._mono0)).isoformat(timespec="milliseconds")

    def record_turn(self, record: Dict[str, Any]):
        self._append("turns.jsonl", record)

    def record_event(self, kind: str, **data: Any):
        self._append("events.jsonl", {"kind": kind, **data})

    def _append(self, name: str, record: Dict[str, Any]):
        line = {"at": datetime.now().isoformat(timespec="milliseconds"), **record}
        try:
            text = json.dumps(line, ensure_ascii=False, default=str)
            with self._lock, open(self.path(name), "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception as exc:  # noqa: BLE001 - a lost record must never break a conversation
            self.logger.warning(f"Could not write {name}: {exc}")


class WavWriter:
    """A WAV file written as audio arrives (RECORD_MICROPHONES: the four microphones of a session, for tuning the
    beamformer offline). Opened on the first write; never raises into the caller."""

    def __init__(self, path: str, channels: int, rate: int):
        self.path = path
        self.channels = channels
        self.rate = rate
        self.frames = 0
        self._wav: Any = None
        self._lock = threading.Lock()

    def write(self, pcm: bytes):
        with self._lock:
            try:
                if self._wav is None:
                    self._wav = wave.open(self.path, "wb")
                    self._wav.setnchannels(self.channels)
                    self._wav.setsampwidth(2)
                    self._wav.setframerate(self.rate)
                self._wav.writeframes(pcm)
                self.frames += len(pcm) // (2 * self.channels)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"could not write {self.path}: {exc}")

    def close(self):
        with self._lock:
            if self._wav is not None:
                try:
                    self._wav.close()
                except Exception:  # noqa: BLE001
                    pass
                self._wav = None
