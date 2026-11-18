"""
Expectations: "expect, then check" for Pepper's moves (#30).

Before Pepper turns or drives towards someone, plain code notes what the action should change (an
:class:`Expectation`). Afterwards it compares that note with what the people detector reports; a mismatch becomes a
surprise in working memory, which the mind then sees. Everything here is pure math: no model calls, no network. The
whole thing sits behind the ``EXPECTATIONS`` switch, off by default, so when it is off nothing changes behaviour or
output.

This first slice covers two expectations:

- **face**: Pepper turns in place by about the bearing of a person it knew about; afterwards someone should be
  nearly straight ahead.
- **closer**: Pepper drives forward with a person nearly straight ahead at a known distance; afterwards their
  distance should have dropped by about what was driven.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence

from .frames import wrap_deg

FACE_WITHIN = 15.0  # degrees: someone this near straight ahead counts as faced
AIMED_WITHIN = 25.0  # degrees: a turn this near a known person's bearing was a turn to face them
FACE_DUE = 5.0  # seconds after the turn ends for the people detector to report them
TOWARD_WITHIN = 20.0  # degrees: a person this near straight ahead before a drive is the one being driven toward
CLOSER_DUE = 5.0  # seconds after the drive ends for the people detector to report them
MIN_DRIVE = 0.2  # metres: shorter drives expect nothing
CLOSER_TOLERANCE = 0.3  # metres
CLOSER_FRACTION = 0.35  # the tolerance is max(CLOSER_TOLERANCE, CLOSER_FRACTION * driven)
SURPRISE_FRESH = 60.0  # seconds a surprise stays in the "Around you" line


@dataclass(frozen=True)
class Target:
    """A person Pepper knew about just before a move. Bearing is in the body frame, degrees, left positive."""

    bearing: float
    distance: Optional[float]
    track_id: Optional[int] = None


@dataclass(frozen=True)
class Expectation:
    id: int
    kind: str  # "face" | "closer"
    made_at: float
    due_at: float
    action: str  # a short description of the move
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """An expectation judged: met, or a surprise. ``reason`` is plain words for the mind and the replay."""

    expectation: Expectation
    met: bool
    at: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


def plan_face(angle: float, targets: Sequence[Target], at: float, next_id: int) -> Optional[Expectation]:
    """Expect someone straight ahead after an ``angle`` degree turn, if it aimed at a known person. A turn this close
    to someone Pepper knew about is taken as a turn to face them; any other turn expects nothing."""
    best, best_gap = None, math.inf
    for t in targets:
        gap = abs(wrap_deg(t.bearing - angle))
        if gap < best_gap:
            best, best_gap = t, gap
    if best is None or best_gap > AIMED_WITHIN:
        return None
    data = {
        "angle": angle,
        "target_bearing": best.bearing,
        "track_id": best.track_id,
        "expected_bearing": wrap_deg(best.bearing - angle),
    }
    return Expectation(
        next_id,
        "face",
        made_at=at,
        due_at=at + FACE_DUE,
        action=f"turned {angle:+.0f} degrees to face someone",
        data=data,
    )


def plan_closer(distance: float, targets: Sequence[Target], at: float, next_id: int) -> Optional[Expectation]:
    """Expect the person straight ahead to be ``distance`` nearer after a drive of that length. Drives shorter than
    ``MIN_DRIVE`` are within the detector's distance noise, so they expect nothing."""
    if distance < MIN_DRIVE:
        return None
    considered = [t for t in targets if t.distance is not None and abs(t.bearing) <= TOWARD_WITHIN]
    if not considered:
        return None
    best = min(considered, key=lambda t: t.distance)
    tolerance = max(CLOSER_TOLERANCE, CLOSER_FRACTION * distance)
    data = {
        "driven": distance,
        "before": best.distance,
        "expected": max(0.0, best.distance - distance),
        "tolerance": tolerance,
        "track_id": best.track_id,
    }
    return Expectation(
        next_id,
        "closer",
        made_at=at,
        due_at=at + CLOSER_DUE,
        action=f"drove {distance:.2f} m towards someone {best.distance:.1f} m away",
        data=data,
    )


def check(exp: Expectation, people: Sequence[Dict[str, Any]], at: float) -> Optional[Verdict]:
    """Judge an expectation against people-detector reports. None while there is no verdict yet.

    ``people`` are reports that may hold ``yaw`` (degrees, body frame, left positive) and ``distance`` (metres);
    either can be missing. A face is met as soon as anyone is nearly straight ahead; anything else keeps waiting.
    A closer is met when the nearest straight-ahead person is within tolerance of where the drive should have put
    them, and a surprise when they are plainly further away.
    """
    if exp.kind == "face":
        for p in people:
            yaw = p.get("yaw")
            if yaw is not None and abs(yaw) <= FACE_WITHIN:
                return Verdict(exp, True, at, "someone was in front of you", {"yaw": yaw, "person": p.get("id")})
        return None
    closer = [
        p
        for p in people
        if p.get("distance") is not None and p.get("yaw") is not None and abs(p["yaw"]) <= TOWARD_WITHIN + 10
    ]
    if not closer:
        return None
    p = min(closer, key=lambda x: x["distance"])
    expected = exp.data["expected"]
    tolerance = exp.data["tolerance"]
    evidence = {"distance": p["distance"], "yaw": p.get("yaw"), "person": p.get("id")}
    if p["distance"] <= expected + tolerance:
        return Verdict(exp, True, at, "they were where you expected", evidence)
    return Verdict(
        exp,
        False,
        at,
        f"you drove {exp.data['driven']:.2f} m towards someone {exp.data['before']:.1f} m away, "
        f"but they were still {p['distance']:.1f} m away",
        evidence,
    )


def expire(exp: Expectation, at: float) -> Optional[Verdict]:
    """At or after ``due_at`` an unjudged expectation is a surprise; before that, None (keep waiting)."""
    if at < exp.due_at:
        return None
    if exp.kind == "face":
        reason = f"you {exp.action}, but nobody was in front of you"
    else:
        reason = f"you {exp.action}, but could not tell how far they were afterwards"
    return Verdict(exp, False, at, reason, {})


class ExpectationWatch:
    """Open expectations, judged as people reports and the clock arrive; a running tally per kind."""

    def __init__(self):
        self.open: List[Expectation] = []
        self.history: Deque[Verdict] = deque(maxlen=50)
        self.stats: Dict[str, Dict[str, int]] = {
            k: {"made": 0, "met": 0, "surprised": 0, "cancelled": 0} for k in ("face", "closer")
        }
        self._next_id = 1

    def next_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def add(self, exp: Expectation):
        self.open.append(exp)
        self.stats[exp.kind]["made"] += 1

    def _record(self, verdict: Verdict):
        self.history.append(verdict)
        key = "met" if verdict.met else "surprised"
        self.stats[verdict.expectation.kind][key] += 1

    def on_people(self, people: Sequence[Dict[str, Any]], at: float) -> List[Verdict]:
        """Judge every open expectation against a people report; return the ones it settled."""
        judged, still = [], []
        for exp in self.open:
            verdict = check(exp, people, at)
            if verdict is None:
                still.append(exp)
            else:
                self._record(verdict)
                judged.append(verdict)
        self.open = still
        return judged

    def tick(self, at: float, people: Optional[Sequence[Dict[str, Any]]] = None) -> List[Verdict]:
        """Expire what is due; with ``people``, give each a last check first so a settled report is not a surprise."""
        judged, still = [], []
        for exp in self.open:
            verdict = check(exp, people, at) if people is not None else None
            verdict = verdict or expire(exp, at)
            if verdict is None:
                still.append(exp)
            else:
                self._record(verdict)
                judged.append(verdict)
        self.open = still
        return judged

    def cancel(self, reason: str):
        """Drop every open expectation, counted as cancelled (a new move started before they could be judged)."""
        for exp in self.open:
            self.stats[exp.kind]["cancelled"] += 1
        self.open = []

    def latest_surprise(self, now: float) -> Optional[Verdict]:
        """The newest fresh surprise, or one from an open expectation past its due time. Pure: nothing changes."""
        for verdict in reversed(self.history):
            if not verdict.met and now - verdict.at <= SURPRISE_FRESH:
                return verdict
        for exp in self.open:
            if now >= exp.due_at:
                return expire(exp, now)
        return None
