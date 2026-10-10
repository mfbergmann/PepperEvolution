"""
World model: what Pepper currently believes about its surroundings (working memory, docs/MEMORY.md).

Everything enters through :meth:`WorldModel.observe` as an :class:`~src.world.observations.Observation`: the
bridge's debounced ``people`` events (and the ``sensors`` snapshot sent when the event stream connects), camera
judgements, what was heard and what Pepper said. The model keeps who is in view, a timeline of what just happened,
and turns it into one plain sentence for the mind's context on every turn, the "around you" line. It lives in our
code, not in any model, so every part of the host can read it and models can be swapped without losing it.
"""

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from loguru import logger

from .frames import Pose, pose_from
from .observations import CAMERA, MOTION, PEOPLE, POSE, SCENE, SOUND, Observation
from .timeline import Timeline
from .tracks import Tracker

ZONE_WORDS = {1: "close", 2: "a little way off", 3: "far away"}
SEEN_WORDS = {"waving": "someone waving at you", "showing": "someone holding something up to show you"}
RECENT_CHANGE_SECONDS = 60.0  # arrivals and departures this recent are mentioned
REMEMBERED_IN_SUMMARY = 120.0  # someone out of view is placed in the "Around you" line this long (tracks.py)
REMEMBERED_MAY_HAVE_MOVED = 30.0  # after this, the line says they may have moved
SOUND_MIN_CONFIDENCE = 0.3  # located sounds below this are ignored (to tune on the robot)
SCENE_FRESH = 90.0  # a scene note is part of the "Around you" line this long
SOUNDS_KEPT = 200  # about the last minute of located sounds
MOVED_UNEXPECTEDLY_DEGREES = 15.0  # a measured pose this far from the estimate with no move of ours running
MOVED_UNEXPECTEDLY_METRES = 0.3
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
        self.timeline = Timeline()
        self.pose = Pose()  # Pepper's own position and heading (odometry when the bridge reports it)
        self.tracker = Tracker()  # people, including those just out of view (#27)
        self._measured_once = False
        self._moving = 0  # commanded moves in flight (no "moved unexpectedly" while they run)
        self.sounds: Deque[Tuple[float, float, float]] = deque(maxlen=SOUNDS_KEPT)  # (time, heading, confidence)
        self.scene: Optional[Tuple[float, Dict[str, Any]]] = None  # the latest scene note (#11): (time, data)
        self.most_people = 0  # the most people in view at once since start-up (session episode)
        self._sinks: List[Callable[[Observation], None]] = []
        self.logger = logger.bind(module="WorldModel")

    def add_sink(self, sink: Callable[[Observation], None]):
        """Also hand every observation to ``sink`` (the session records), after it was applied."""
        self._sinks.append(sink)

    def now(self) -> float:
        return self._clock()

    def observe(self, obs: Observation) -> Optional["Arrival"]:
        """The one way into working memory. Returns an :class:`Arrival` when the observation was one."""
        arrival = None
        measured = pose_from(obs.data["pose"], obs.at) if obs.data.get("pose") is not None else None
        if measured is not None and obs.source != MOTION:
            self._apply_pose(measured)
        if obs.source == PEOPLE:
            people = obs.data.get("people") or []
            arrival = self.update_people(obs.data.get("count"), people, initial=bool(obs.data.get("initial")))
            self._update_tracks(people, obs.at, placed=measured is not None or not self._moving)
        elif obs.source == POSE:
            pass  # applied above
        elif obs.source == SCENE:
            self.scene = (obs.at, dict(obs.data))
            self.timeline.add("scene", obs.at, **obs.data)
        elif obs.source == SOUND:
            confidence = float(obs.data.get("confidence") or 0.0)
            if obs.data.get("azimuth") is not None and confidence >= SOUND_MIN_CONFIDENCE:
                self.sounds.append((obs.at, self.pose.heading_of(float(obs.data["azimuth"])), confidence))
        elif obs.source == MOTION:
            self._apply_motion(obs, measured)
        elif obs.source == CAMERA and obs.kind == "judgement":
            self.update_seen(obs.data.get("p") or {}, at=obs.at)
        elif obs.source == CAMERA and obs.kind == "person_seen" and obs.data.get("bearing") is not None:
            track = self.tracker.camera(self.pose.heading_of(float(obs.data["bearing"])), self.pose, obs.at)
            self.timeline.add("track", obs.at, track=track.id, camera=True, bearing=obs.data["bearing"])
        else:
            self.timeline.add(obs.kind, obs.at, **{**obs.data, "source": obs.source})
        for sink in self._sinks:
            try:
                sink(obs)
            except Exception as exc:  # noqa: BLE001 - a lost record must never break perception
                self.logger.debug(f"observation sink failed: {exc}")
        return arrival

    # -- pose and tracks (#27) -----------------------------------------------------------------------------------------

    def _apply_pose(self, measured: Pose):
        """A pose measured by the bridge. The first one re-bases what was dead-reckoned; a jump with no move
        running means Pepper was moved without a command from the host (pushed, carried, or NAOqi's own behaviour:
        the desktop NAOqi turns the base ±54° while waking up), and remembered positions are no longer valid."""
        if not self._measured_once:
            self._rebase(self.pose, measured)
            self._measured_once = True
        else:
            metres, degrees = measured.moved_from(self.pose)
            if (
                not self._moving
                and not self.pose.uncertain
                and (degrees > MOVED_UNEXPECTEDLY_DEGREES or metres > MOVED_UNEXPECTEDLY_METRES)
            ):
                self.logger.info(f"Pepper was moved ({degrees:.0f}°, {metres:.2f} m) with no move running")
                if any(t.placed for t in self.tracker.tracks):  # only worth telling when it costs remembered places
                    self.timeline.add(
                        "moved_unexpectedly", measured.at, degrees=round(degrees), metres=round(metres, 2)
                    )
                self.tracker.forget_positions()
        self.pose = measured

    def _rebase(self, old: Pose, new: Pose):
        """Move everything placed relative to the dead-reckoned pose into the measured odometry frame."""
        for track in self.tracker.tracks:
            if track.x is not None and track.y is not None:
                bearing, distance = old.bearing_to(track.x, track.y), old.distance_to(track.x, track.y)
                track.x, track.y = new.to_odom(bearing, distance)
            elif track.heading is not None:
                track.heading = new.heading_of(old.bearing_of(track.heading))

    def _apply_motion(self, obs: Observation, measured: Optional[Pose]):
        """A move Pepper made (PepperRobot): "<kind>_started", then "<kind>" with whether it completed."""
        kind = obs.kind
        if kind.endswith("_started"):
            self._moving += 1
            return
        self._moving = max(0, self._moving - 1)
        data = obs.data
        completed = data.get("completed") is not False
        if data.get("refused"):  # the bridge refused it (an obstacle, motors off): Pepper did not move
            self.timeline.add(kind, obs.at, description="refused", completed=False, source=MOTION)
            return
        if measured is not None:
            if not self._measured_once:
                self._rebase(self.pose, measured)
                self._measured_once = True
            self.pose = measured
        elif kind == "turn":
            self.pose = self.pose.turned(float(data.get("angle") or 0.0), obs.at, uncertain=not completed)
        elif kind == "drive":
            self.pose = self.pose.driven(float(data.get("distance") or 0.0), at=obs.at, uncertain=not completed)
        elif kind == "move":
            moved = self.pose.driven(float(data.get("x") or 0.0), float(data.get("y") or 0.0), at=obs.at)
            self.pose = moved.turned(float(data.get("theta") or 0.0), obs.at, uncertain=not completed)
        what = {
            "turn": f"{float(data.get('angle') or 0):+.0f} degrees",
            "drive": f"{float(data.get('distance') or 0):+.2f} m",
            "move": f"to x {data.get('x')}, y {data.get('y')}, turning {data.get('theta')} degrees",
        }.get(kind, "")
        self.timeline.add(
            kind,
            obs.at,
            description=what + ("" if completed else " (stopped early)"),
            completed=completed,
            source=MOTION,
        )

    def _update_tracks(self, people: List[Dict[str, Any]], at: float, placed: bool):
        """People into tracks. While a move runs and the report carries no pose of its own, directions are not
        trusted (the dead-reckoned pose lags the turn): presence is kept, positions are not changed."""
        if not placed:
            people = [{k: v for k, v in p.items() if k != "yaw"} for p in people]
        for association in self.tracker.update(people, self.pose, at):
            self.timeline.add("track", at, **association)

    def voice_direction(self, start: float, end: float) -> Optional[Dict[str, Any]]:
        """Where the voice in ``[start, end]`` came from: the confidence-weighted mean direction of the sounds
        located then, as a body-frame bearing now (degrees), or None if none were located (#12, #24)."""
        picked = [(heading, conf) for at, heading, conf in self.sounds if start <= at <= end]
        if not picked:
            return None
        sx = sum(conf * math.cos(h) for h, conf in picked)
        sy = sum(conf * math.sin(h) for h, conf in picked)
        if math.hypot(sx, sy) < 1e-6:
            return None
        heading = math.atan2(sy, sx)
        spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(math.hypot(sx, sy) / sum(c for _, c in picked)))))
        return {
            "bearing": round(self.pose.bearing_of(heading), 1),
            "heading": heading,
            "sounds": len(picked),
            "spread": round(spread, 1),
        }

    def attach_voice(self, heading: float, at: Optional[float] = None) -> Any:
        """The person a voice came from: an existing track in that direction, or a new one known by voice."""
        track = self.tracker.voice(heading, self.pose, self._clock() if at is None else at)
        self.timeline.add("voice", track.spoke_at or self._clock(), track=track.id, heard_only=track.heard_only)
        return track

    def remembered_line(self) -> str:
        """Where the most recently seen person was, if they are out of view now (#27); empty otherwise."""
        if self.pose.uncertain:
            return ""
        now = self._clock()
        for track in self.tracker.remembered(now):
            if now - track.last_seen > REMEMBERED_IN_SUMMARY:
                break
            bearing = track.bearing(self.pose)
            if bearing is None:
                continue
            degrees = int(round(abs(bearing) / 5.0) * 5)
            if degrees < 10:
                where = "straight ahead of your body"
            else:
                side = "left" if bearing > 0 else "right"
                turn = degrees if bearing > 0 else -degrees
                where = (
                    f"about {degrees}° to your {side} of where your body points now "
                    f"(turn {turn} to face where they were)"
                )
            distance = track.distance_from(self.pose)
            away = f"about {distance:.1f} m away and " if distance is not None else ""
            age = now - track.last_seen
            # people move: on 2026-10-08 someone said they were going to sit down, and was 136° from where they stood
            moved = "; they may have moved since" if age > REMEMBERED_MAY_HAVE_MOVED else ""
            verb = "heard someone" if track.heard_only else "last saw someone"
            return f"you {verb} {_ago(age)}, {away}{where}{moved}"
        return ""

    def recall(self) -> Dict[str, Any]:
        """Working memory for the mind's recall tool (``views.recall``)."""
        from .views import recall

        return recall(self)

    def note(self, source: str, kind: str, /, **data: Any) -> Optional["Arrival"]:
        """Shorthand for ``observe(Observation(source, kind, now, data))``."""
        return self.observe(Observation(source, kind, self._clock(), data))

    def handle_people(self, count: Optional[int], people: List[Dict[str, Any]], pose: Any = None) -> Optional[Arrival]:
        """A people report (as the bridge sends it) into working memory; ``pose`` is the bridge's measured pose."""
        extra = {"pose": pose} if pose is not None else {}
        return self.note(PEOPLE, "people", count=count, people=people, **extra)

    def on_arrival(self, callback: ArrivalCallback):
        """Register an async callback for arrivals (someone appearing after the room was empty)."""
        self._arrival_callbacks.append(callback)

    async def handle_event(self, event_type: str, data: Dict[str, Any]):
        """Robot event callback (``PepperRobot.on_event``)."""
        arrival = None
        if event_type == "people":
            arrival = self.handle_people(data.get("count"), data.get("people") or [], pose=data.get("pose"))
        elif event_type == "pose" and data.get("pose") is not None:
            self.note(POSE, "pose", pose=data.get("pose"))
        elif event_type == "sound":
            self.note(SOUND, "sound", **{k: data.get(k) for k in ("azimuth", "elevation", "confidence", "energy")})
        elif event_type == "sensors" and "people_count" in data:
            extra = {"pose": data["pose"]} if data.get("pose") is not None else {}
            self.note(
                PEOPLE, "people", count=data.get("people_count"), people=data.get("people") or [], initial=True, **extra
            )
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
        self.most_people = max(self.most_people, count)
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
            self.timeline.add("left", now, count=count)
            return None
        if previous > 0:
            self.changes.append((now, "arrived"))
            self.timeline.add("arrived", now, count=count)
            return None  # someone joined people already here: noted, not greeted (yet)
        empty_for = now - empty_since if empty_since is not None else math.inf
        if empty_for < self.arrival_absence:
            if self.changes and self.changes[-1][1] == "left":
                self.changes.pop()  # the "left" was the detector losing them, not a departure
            self.timeline.add("back", now, count=count, gone_for=round(empty_for, 1))
            return None
        self.changes.append((now, "arrived"))
        self.timeline.add("arrived", now, count=count, after_empty_for=None if math.isinf(empty_for) else empty_for)
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
            remembered = self.remembered_line()
            if remembered:
                text += f"; {remembered}"
                recent = []  # the line already says when
            elif self.last_seen_someone_at is not None and not recent:  # a recent "left" already says when
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
        note = self.scene_line()
        if note:
            text += f" {note}"
        return text

    def scene_line(self) -> str:
        """The latest place note, while fresh (#11): "Your last look around (12 s ago): a meeting room ..."."""
        if self.scene is None:
            return ""
        at, data = self.scene
        age = self._clock() - at
        note = str(data.get("note") or "").strip().rstrip(".")
        if not note or age > SCENE_FRESH:
            return ""
        return f"Your last look around ({_ago(age)}): {note}."

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
