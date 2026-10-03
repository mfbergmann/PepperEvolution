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
