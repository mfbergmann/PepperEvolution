"""
World model: what Pepper currently believes about its surroundings.

The first slice of Milestone 4. It is fed by the bridge's debounced ``people``
events (and the ``sensors`` snapshot sent when the event stream connects), and
turns them into one plain sentence for the model's context on every turn, the
"around you" line. It lives in our code, not in any model, so every part of the
host can read it and models can be swapped without losing it (see
docs/ARCHITECTURE.md).
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

ZONE_WORDS = {1: "close", 2: "a little way off", 3: "far away"}
RECENT_CHANGE_SECONDS = 60.0  # arrivals and departures this recent are mentioned
MAX_PEOPLE_DESCRIBED = 3


@dataclass
class Person:
    id: Any
    distance: Optional[float] = None  # metres
    looking: Optional[bool] = None  # looking at Pepper
    zone: Optional[int] = None  # 1 close, 2 middle, 3 far (NAOqi engagement zones)
    present_for: Optional[int] = None  # seconds tracked, from NAOqi

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Person":
        return cls(
            id=data.get("id"),
            distance=data.get("distance"),
            looking=data.get("looking"),
            zone=data.get("zone"),
            present_for=data.get("present_for"),
        )


def _ago(seconds: float) -> str:
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    minutes = int(seconds // 60)
    return "a minute ago" if minutes == 1 else f"{minutes} minutes ago"


def _duration(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    if seconds < 60:
        return "under a minute"
    minutes = seconds // 60
    return "about a minute" if minutes == 1 else f"about {minutes} minutes"


class WorldModel:
    """Who is around, updated from bridge events; summarised for the model."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.people: List[Person] = []
        self.count: Optional[int] = None  # None until the first report
        self.updated_at: Optional[float] = None
        self.last_seen_someone_at: Optional[float] = None
        self.changes: Deque[Tuple[float, str]] = deque(maxlen=20)  # (time, "arrived" | "left")

    async def handle_event(self, event_type: str, data: Dict[str, Any]):
        """Robot event callback (``PepperRobot.on_event``)."""
        if event_type == "people":
            self.update_people(data.get("count"), data.get("people") or [])
        elif event_type == "sensors" and "people_count" in data:
            self.update_people(data.get("people_count"), data.get("people") or [], initial=True)

    def update_people(self, count: Optional[int], people: List[Dict[str, Any]], initial: bool = False):
        now = self._clock()
        if count is None:
            return
        previous = self.count
        self.count = count
        self.people = [Person.from_dict(p) for p in people]
        self.updated_at = now
        if count > 0:
            self.last_seen_someone_at = now
        if previous is not None and previous > 0 and count == 0:
            self.last_seen_someone_at = now  # the moment they left
        if not initial and previous is not None and count != previous:
            self.changes.append((now, "arrived" if count > previous else "left"))

    def summary(self) -> str:
        """One sentence for the model's context; empty until the first report."""
        if self.count is None:
            return ""
        now = self._clock()
        recent = [(t, kind) for t, kind in self.changes if now - t <= RECENT_CHANGE_SECONDS]
        if self.count == 0:
            text = "Around you: nobody in view"
            if self.last_seen_someone_at is not None and not recent:  # a recent "left" already says when
                text += f" (you last saw someone {_ago(now - self.last_seen_someone_at)})"
            text += "."
        else:
            parts = [self._describe(p, i) for i, p in enumerate(self.people[:MAX_PEOPLE_DESCRIBED])]
            if not parts:  # a count without details
                parts = [f"{self.count} {'person' if self.count == 1 else 'people'} in view"]
            text = "Around you: " + "; ".join(parts)
            extra = self.count - min(len(self.people), MAX_PEOPLE_DESCRIBED)
            if extra > 0 and self.people:
                text += f"; and {extra} more"
            text += "."
        if recent:
            t, kind = recent[-1]
            text += f" Someone {kind} {_ago(now - t)}."
        return text

    @staticmethod
    def _describe(person: Person, index: int) -> str:
        words = ["one person" if index == 0 else "another person"]
        if person.distance is not None:
            words.append(f"about {person.distance:.1f} m away")
        elif person.zone in ZONE_WORDS:
            words.append(ZONE_WORDS[person.zone])
        if person.looking is True:
            words.append("looking at you")
        elif person.looking is False:
            words.append("not looking at you")
        here = _duration(person.present_for)
        if here:
            words.append(f"here for {here}")
        return ", ".join(words)
