"""
World model: what Pepper currently believes about its surroundings.

The first slice of Milestone 4. It is fed by the bridge's debounced ``people``
events (and the ``sensors`` snapshot sent when the event stream connects), and
turns them into one plain sentence for the model's context on every turn, the
"around you" line. It lives in our code, not in any model, so every part of the
host can read it and models can be swapped without losing it (see
docs/ARCHITECTURE.md).
"""

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from loguru import logger

ZONE_WORDS = {1: "close", 2: "a little way off", 3: "far away"}
SEEN_WORDS = {"waving": "someone waving at you", "showing": "someone holding something up to show you"}
RECENT_CHANGE_SECONDS = 60.0  # arrivals and departures this recent are mentioned
ARRIVAL_ABSENCE_SECONDS = 20.0  # someone "arrives" only after nobody was in view this long; shorter gaps are
# the detector losing a person who looked away (seen on the robot), not a new arrival
MAX_PEOPLE_DESCRIBED = 3


@dataclass
class Person:
    id: Any
    distance: Optional[float] = None  # metres
    looking: Optional[bool] = None  # looking at Pepper
    zone: Optional[int] = None  # 1 close, 2 middle, 3 far (NAOqi engagement zones)
    present_for: Optional[int] = None  # seconds tracked, from NAOqi
    yaw: Optional[float] = None  # head yaw (degrees, left positive) that would face them
    pitch: Optional[float] = None  # head pitch (degrees, down positive) that would face them

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Person":
        return cls(
            id=data.get("id"),
            distance=data.get("distance"),
            looking=data.get("looking"),
            zone=data.get("zone"),
            present_for=data.get("present_for"),
            yaw=data.get("yaw"),
            pitch=data.get("pitch"),
        )


@dataclass
class Arrival:
    """Someone came into view after the room had been empty for a while."""

    count: int
    nearest: Optional[Person]
    empty_for: float  # seconds nobody was in view before (inf when the room was empty since start-up)


ArrivalCallback = Callable[[Arrival], Awaitable[None]]


def _ago(seconds: float) -> str:
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    minutes = int(seconds // 60)
    return "a minute ago" if minutes == 1 else f"{minutes} minutes ago"


def _direction(yaw: Optional[float]) -> Optional[str]:
    """Where a person is relative to Pepper's body, in the terms of the turn tool (left = positive degrees).

    The turn to face them is spelled out: on the robot (2026-10-08), with someone "about 75° to your right" just after
    a 90° turn left, the model turned -165, adding the earlier turn back on top.
    """
    if yaw is None:
        return None
    if abs(yaw) < 10:
        return "straight ahead of your body"
    degrees = int(round(abs(yaw) / 5.0) * 5)
    side = "left" if yaw > 0 else "right"
    turn = degrees if yaw > 0 else -degrees
    return f"about {degrees}° to your {side} of where your body points now (turn {turn} to face them)"


def _duration(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    if seconds < 60:
        return "under a minute"
    minutes = seconds // 60
    return "about a minute" if minutes == 1 else f"about {minutes} minutes"


class WorldModel:
    """Who is around, updated from bridge events; summarised for the model."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, arrival_absence: float = ARRIVAL_ABSENCE_SECONDS):
        self._clock = clock
        self.arrival_absence = arrival_absence
        self.people: List[Person] = []
        self.seen: Dict[str, Tuple[float, float]] = {}  # camera judgements: what -> (probability, time)
        self.count: Optional[int] = None  # None until the first report
        self.updated_at: Optional[float] = None
        self.last_seen_someone_at: Optional[float] = None
        self.empty_since: Optional[float] = None  # when the view last became empty; -inf if empty since start-up
        self.changes: Deque[Tuple[float, str]] = deque(maxlen=20)  # (time, "arrived" | "left")
        self._crowd: Deque[Tuple[float, int]] = deque(maxlen=50)  # (time, count) when two or more were in view
        self._arrival_callbacks: List[ArrivalCallback] = []
        self.logger = logger.bind(module="WorldModel")

    def on_arrival(self, callback: ArrivalCallback):
        """Register an async callback for arrivals (someone appearing after the room was empty)."""
        self._arrival_callbacks.append(callback)

    async def handle_event(self, event_type: str, data: Dict[str, Any]):
        """Robot event callback (``PepperRobot.on_event``)."""
        arrival = None
        if event_type == "people":
            arrival = self.update_people(data.get("count"), data.get("people") or [])
        elif event_type == "sensors" and "people_count" in data:
            self.update_people(data.get("people_count"), data.get("people") or [], initial=True)
        if arrival is not None:
            for cb in self._arrival_callbacks:
                try:
                    await cb(arrival)
                except Exception as exc:  # noqa: BLE001 - a listener must not break perception
                    self.logger.error(f"arrival callback failed: {exc}")

    def update_people(
        self, count: Optional[int], people: List[Dict[str, Any]], initial: bool = False
    ) -> Optional[Arrival]:
        """Apply a people report; return an Arrival when someone appeared after the room was empty long enough.

        Someone already present when the host connects (``initial``, the bridge's snapshot) is not an arrival,
        and neither is a person reappearing after a short dropout (the detector loses faces that turn away).
        """
        now = self._clock()
        if count is None:
            return None
        previous = self.count
        empty_since = self.empty_since  # captured before anything below overwrites it
        self.count = count
        self.people = [Person.from_dict(p) for p in people]
        self.updated_at = now
        if count >= 2:
            self._crowd.append((now, count))
        if count > 0:
            self.last_seen_someone_at = now
            self.empty_since = None
        elif previous is None:
            self.empty_since = -math.inf  # nobody there when we started: treat as long empty
        elif previous > 0:
            self.empty_since = now  # the moment the last person left
            self.last_seen_someone_at = now
        if initial or previous is None or count == previous:
            return None
        if count < previous:
            self.changes.append((now, "left"))
            return None
        if previous > 0:
            self.changes.append((now, "arrived"))
            return None  # someone joined people already here: noted, not greeted (yet)
        empty_for = now - empty_since if empty_since is not None else math.inf
        if empty_for < self.arrival_absence:
            if self.changes and self.changes[-1][1] == "left":
                self.changes.pop()  # the "left" was the detector losing them, not a departure
            return None
        self.changes.append((now, "arrived"))
        return Arrival(count=count, nearest=self.people[0] if self.people else None, empty_for=empty_for)

    def most_in_view(self, within: float) -> int:
        """The most people in view at once over the last ``within`` seconds (0 before the first report)."""
        now = self._clock()
        crowd = [n for t, n in self._crowd if now - t <= within]
        return max([self.count or 0] + crowd)

    SEEN_FRESH = 5.0  # seconds a camera judgement counts as "now"

    def update_seen(self, probabilities: Dict[str, float], at: Optional[float] = None):
        """Latest answers from the camera judgements (src/perception/vision.py)."""
        at = self._clock() if at is None else at
        for what, p in probabilities.items():
            self.seen[what] = (p, at)

    def sees(self, what: str, threshold: float = 0.7) -> bool:
        """True if a recent camera judgement says ``what`` (e.g. "facing") with at least ``threshold``."""
        p, at = self.seen.get(what, (0.0, -1e9))
        return p >= threshold and self._clock() - at <= self.SEEN_FRESH

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
        camera = [words for what, words in SEEN_WORDS.items() if self.count and self.sees(what)]
        if camera:
            text += f" Your camera shows {' and '.join(camera)}."
        return text

    @staticmethod
    def _describe(person: Person, index: int) -> str:
        words = ["one person" if index == 0 else "another person"]
        if person.distance is not None:
            words.append(f"about {person.distance:.1f} m away")
        elif person.zone in ZONE_WORDS:
            words.append(ZONE_WORDS[person.zone])
        direction = _direction(person.yaw)
        if direction:
            words.append(direction)
        if person.looking is True:
            words.append("looking at you")
        elif person.looking is False:
            words.append("not looking at you")
        here = _duration(person.present_for)
        if here:
            words.append(f"here for {here}")
        return ", ".join(words)
