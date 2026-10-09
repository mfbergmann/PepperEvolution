"""
Timeline: what just happened, in order, for the readers of working memory (docs/MEMORY.md).

A bounded list of :class:`Event` entries: arrivals and departures, greetings, moves, what was heard (with the
addressee verdict) and what Pepper said, camera events, later scene notes and sounds. It lives in memory only and
is gone at a restart; the session records keep the raw inputs for replay.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional

MAX_EVENTS = 500  # a long conversation is a few hundred entries; older ones are not needed in working memory


@dataclass(frozen=True)
class Event:
    kind: str  # "heard", "said", "arrived", "left", "greeting", "turn", "drive", "camera", ...
    at: float  # time.monotonic()
    data: Dict[str, Any] = field(default_factory=dict)


class Timeline:
    def __init__(self, maxlen: int = MAX_EVENTS):
        self._events: Deque[Event] = deque(maxlen=maxlen)

    def add(self, kind: str, at: float, /, **data: Any) -> Event:
        event = Event(kind, at, dict(data))
        self._events.append(event)
        return event

    def __len__(self) -> int:
        return len(self._events)

    def recent(
        self, kinds: Optional[Iterable[str]] = None, within: Optional[float] = None, now: Optional[float] = None
    ) -> List[Event]:
        """Events oldest first, optionally only some kinds and only those within ``within`` seconds of ``now``."""
        wanted = set(kinds) if kinds is not None else None
        out = []
        for event in self._events:
            if wanted is not None and event.kind not in wanted:
                continue
            if within is not None and now is not None and now - event.at > within:
                continue
            out.append(event)
        return out

    def last(self, kind: str) -> Optional[Event]:
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None
