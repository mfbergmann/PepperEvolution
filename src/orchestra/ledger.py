"""
The ledger (docs/ORCHESTRATOR.md): stable turn ids, what the mind was shown and what it cost, and outcomes.

Turn lines stay in ``turns.jsonl`` (src/session.py); this adds to them. Outcomes become known only after a turn's
line is written (the person says "stop", or the same words again), so they go to ``outcomes.jsonl``, keyed by turn
id. Nothing here waits or raises into a conversation.
"""

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

STOPPING_INTENTS = ("stop", "emergency_stop", "quiet")


def digest(value: Any) -> str:
    """A short, stable hash of a prompt or a tool list: which version the mind saw."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class TurnIds:
    """``<session>#<n>``, counted from 1 within a host run."""

    def __init__(self, session: str = ""):
        self.session = session or "nosession"
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"{self.session}#{self._n}"


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def similar(a: str, b: str, threshold: float = 0.8) -> bool:
    a, b = _words(a), _words(b)
    if not a or not b:
        return False
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= threshold


@dataclass
class _Finished:
    turn_id: str
    text: str
    path: str
    ended_at: float
    spoke: bool


class OutcomeWatch:
    """Outcomes that the next things heard reveal about the turns before them.

    - ``stopped_during``: stop or quiet while the turn was still running.
    - ``stopped_after``: stop or quiet within ``STOP_WINDOW`` seconds of the turn's end.
    - ``repeated``: much the same words within ``REPEAT_WINDOW`` seconds after Pepper answered them.
    - ``repeated_after_ignored``: the same, after the addressee gate let them pass unanswered (a likely miss).
    - ``error`` and ``tool_failed``: from the finished turn itself.
    """

    STOP_WINDOW = 10.0
    REPEAT_WINDOW = 30.0

    def __init__(self, emit: Callable[..., None], clock: Callable[[], float]):
        self._emit = emit
        self._clock = clock
        self._running: Optional[str] = None
        self._last: Optional[_Finished] = None

    def started(self, turn_id: str):
        self._running = turn_id

    def heard(self, text: str, intent: Optional[str] = None):
        """Something was heard from a person (before any decision about it)."""
        now = self._clock()
        if intent in STOPPING_INTENTS:
            if self._running is not None:
                self._emit(self._running, "stopped_during", intent=intent)
            elif self._last is not None and self._last.spoke and now - self._last.ended_at < self.STOP_WINDOW:
                self._emit(
                    self._last.turn_id, "stopped_after", intent=intent, after_s=round(now - self._last.ended_at, 2)
                )
            return
        last = self._last
        if last is None or now - last.ended_at >= self.REPEAT_WINDOW or not similar(text, last.text):
            return
        after = round(now - last.ended_at, 2)
        if last.path == "not_addressed":
            self._emit(last.turn_id, "repeated_after_ignored", after_s=after)
        elif last.spoke:
            self._emit(last.turn_id, "repeated", after_s=after)

    def finished(
        self,
        turn_id: str,
        text: str,
        path: str,
        result: Dict[str, Any],
        tools: List[Dict[str, Any]],
        person: bool = True,
    ):
        """A turn ended. ``person``: it answered someone (not a sensor event or an initiative)."""
        if self._running == turn_id:
            self._running = None
        if result.get("stop_reason") == "error":
            self._emit(turn_id, "error", error=str((result.get("usage") or {}).get("error", ""))[:200])
        for tool in tools:
            if not tool.get("ok", True):
                self._emit(turn_id, "tool_failed", tool=tool.get("name"))
        if not person or path in ("intent", "routed_intent", "cancelled"):
            return  # control phrases are not answers to judge by what comes next
        answered = bool(result.get("spoken") or result.get("text"))  # typed sessions answer without speaking
        self._last = _Finished(turn_id, text, path, self._clock(), answered)
