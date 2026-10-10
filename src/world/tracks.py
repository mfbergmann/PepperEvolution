"""
Person tracks: one person as the host follows them, including after the detector loses them (docs/MEMORY.md).

NAOqi's detector loses people every few seconds and renumbers them, so the host keeps its own tracks:

- present: in the latest report;
- lost: missing for under LOST_FOR seconds (the detector's usual dropouts);
- remembered: a position under REMEMBER_FOR seconds old; people move, so older positions are worth little;
- gone: older; dropped.

Positions are stored in the odometry frame, so they stay true while Pepper turns. Matching is deliberately simple
(single hypothesis): a report goes to the nearest track within MATCH_DEGREES and MATCH_METRES, most recently seen
first on a tie. A report without a direction never erases a known position. Every match and new track is returned
as an association, so it can be logged and reviewed.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .frames import Pose, wrap

LOST_FOR = 5.0
REMEMBER_FOR = 120.0
MATCH_DEGREES = 20.0
MATCH_METRES = 0.7
VOICE_MATCH_DEGREES = 25.0  # sound direction is about 10 degrees accurate on Pepper (NAOqi docs)
CAMERA_MATCH_DEGREES = 30.0  # the camera fallback only says which third of the frame (about 19 degrees apart)


@dataclass
class PersonTrack:
    id: int
    first_seen: float
    last_seen: float
    present: bool = True
    x: Optional[float] = None  # odometry position when the distance was known
    y: Optional[float] = None
    heading: Optional[float] = None  # absolute odometry direction (radians) when only the bearing was known
    distance: Optional[float] = None  # at the last sighting
    looking: Optional[bool] = None
    pitch: Optional[float] = None
    lost_at: Optional[float] = None
    sources: List[str] = field(default_factory=lambda: ["people"])
    person_id: Optional[int] = None  # long-term memory, when known (opt-in identity, later)
    spoke_at: Optional[float] = None  # when a voice came from their direction

    @property
    def heard_only(self) -> bool:
        """Known only from a voice (sound direction), never seen by the people detector."""
        return self.sources == ["sound"]

    @property
    def placed(self) -> bool:
        return (self.x is not None and self.y is not None) or self.heading is not None

    def bearing(self, pose: Pose) -> Optional[float]:
        """Body-frame bearing (degrees) from Pepper as it stands now, or None if the track has no direction."""
        if self.x is not None and self.y is not None:
            return pose.bearing_to(self.x, self.y)
        if self.heading is not None:
            return pose.bearing_of(self.heading)
        return None

    def distance_from(self, pose: Pose) -> Optional[float]:
        if self.x is not None and self.y is not None:
            return pose.distance_to(self.x, self.y)
        return None

    def forget_position(self):
        self.x = self.y = self.heading = None


class Tracker:
    def __init__(self):
        self.tracks: List[PersonTrack] = []
        self._next_id = 1

    def update(self, people: List[Dict[str, Any]], pose: Pose, now: float) -> List[Dict[str, Any]]:
        """Apply one people report. Returns the associations made (for the timeline and the session records)."""
        self._expire(now)
        candidates = [t for t in self.tracks if now - t.last_seen <= REMEMBER_FOR]
        placed = []  # (report, odometry point or None, heading or None)
        for p in people:
            yaw = p.get("yaw")
            distance = p.get("distance")
            point = heading = None
            if yaw is not None:
                if distance is not None:
                    point = pose.to_odom(float(yaw), float(distance))
                else:
                    heading = pose.heading_of(float(yaw))
            placed.append((p, point, heading))

        pairs: List[Tuple[float, float, int, int]] = []
        for ri, (p, point, heading) in enumerate(placed):
            for ti, track in enumerate(candidates):
                cost = self._cost(track, point, heading, pose)
                if cost is not None:
                    pairs.append((cost, -track.last_seen, ri, ti))
        pairs.sort()
        used_reports, used_tracks, matches = set(), set(), {}
        for cost, _, ri, ti in pairs:
            if ri in used_reports or ti in used_tracks:
                continue
            used_reports.add(ri)
            used_tracks.add(ti)
            matches[ri] = candidates[ti]

        # reports without a direction: if exactly one present track is left, it is them (never erase a position)
        unplaced = [ri for ri, (_, point, heading) in enumerate(placed) if point is None and heading is None]
        free_present = [t for i, t in enumerate(candidates) if i not in used_tracks and t.present]
        if len(unplaced) == 1 and len(free_present) == 1 and unplaced[0] not in matches:
            matches[unplaced[0]] = free_present[0]
            used_tracks.add(candidates.index(free_present[0]))

        associations = []
        seen_ids = set()
        for ri, (p, point, heading) in enumerate(placed):
            track = matches.get(ri)
            if track is None:
                track = PersonTrack(id=self._next_id, first_seen=now, last_seen=now)
                self._next_id += 1
                self.tracks.append(track)
                associations.append({"track": track.id, "new": True, "bearing": p.get("yaw")})
            else:
                if not track.present:
                    associations.append(
                        {"track": track.id, "back_after": round(now - track.last_seen, 1), "bearing": p.get("yaw")}
                    )
            self._apply(track, p, point, heading, now)
            seen_ids.add(track.id)
        for track in self.tracks:
            if track.present and track.id not in seen_ids:
                track.present = False
                track.lost_at = now
        return associations

    def present(self) -> List[PersonTrack]:
        return [t for t in self.tracks if t.present]

    def remembered(self, now: float) -> List[PersonTrack]:
        """Tracks out of view with a position, most recently seen first."""
        out = [t for t in self.tracks if not t.present and t.placed and now - t.last_seen <= REMEMBER_FOR]
        return sorted(out, key=lambda t: -t.last_seen)

    def voice(self, heading: float, pose: Pose, now: float, within: float = VOICE_MATCH_DEGREES) -> PersonTrack:
        """A voice came from ``heading`` (absolute, radians): mark the person there as the speaker, or start a
        track known only by that voice (so "come to me" has a direction when the detector saw nobody)."""
        self._expire(now)
        best, best_gap = None, None
        for track in self.tracks:
            if now - track.last_seen > REMEMBER_FOR or not track.placed:
                continue
            bearing = track.bearing(pose)
            gap = abs(bearing - pose.bearing_of(heading)) if bearing is not None else None
            if gap is not None and gap <= within and (best_gap is None or gap < best_gap):
                best, best_gap = track, gap
        if best is None:
            best = PersonTrack(
                id=self._next_id, first_seen=now, last_seen=now, present=False, heading=heading, sources=["sound"]
            )
            self._next_id += 1
            self.tracks.append(best)
        elif "sound" not in best.sources:
            best.sources.append("sound")
        best.spoke_at = now
        if best.heard_only:
            best.heading, best.last_seen = heading, now
        return best

    def camera(self, heading: float, pose: Pose, now: float, within: float = CAMERA_MATCH_DEGREES) -> PersonTrack:
        """The camera saw someone in direction ``heading`` (absolute, radians) while the detector saw nobody (#24):
        refresh the person remembered there (keeping their distance), or start a track known only from the camera."""
        self._expire(now)
        best, best_gap = None, None
        for track in self.tracks:
            if now - track.last_seen > REMEMBER_FOR or not track.placed:
                continue
            bearing = track.bearing(pose)
            gap = abs(bearing - pose.bearing_of(heading)) if bearing is not None else None
            if gap is not None and gap <= within and (best_gap is None or gap < best_gap):
                best, best_gap = track, gap
        if best is None:
            best = PersonTrack(
                id=self._next_id, first_seen=now, last_seen=now, present=False, heading=heading, sources=["camera"]
            )
            self._next_id += 1
            self.tracks.append(best)
            return best
        distance = best.distance_from(pose)
        if distance is not None:
            best.x, best.y = pose.to_odom(pose.bearing_of(heading), distance)
        else:
            best.heading = heading
        best.last_seen = now
        if "camera" not in best.sources:
            best.sources.append("camera")
        return best

    def forget_positions(self):
        """After Pepper moved unexpectedly: positions are no longer known relative to it."""
        for track in self.tracks:
            track.forget_position()

    # -- internals ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _cost(track: PersonTrack, point, heading, pose: Pose) -> Optional[float]:
        if point is None and heading is None:
            return None
        if not track.placed:
            return None
        if point is not None and track.x is not None and track.y is not None:
            metres = math.hypot(point[0] - track.x, point[1] - track.y)
            degrees = abs(pose.bearing_to(*point) - pose.bearing_to(track.x, track.y))
            if metres <= MATCH_METRES or degrees <= MATCH_DEGREES and metres <= 2 * MATCH_METRES:
                return metres + degrees / 30.0
            return None
        # compare directions only (one of them has no distance)
        mine = math.atan2(track.y - pose.y, track.x - pose.x) if track.x is not None else track.heading
        theirs = math.atan2(point[1] - pose.y, point[0] - pose.x) if point is not None else heading
        degrees = abs(math.degrees(wrap(theirs - mine)))  # type: ignore[operator]
        return degrees / 30.0 + 0.5 if degrees <= MATCH_DEGREES else None

    @staticmethod
    def _apply(track: PersonTrack, p: Dict[str, Any], point, heading, now: float):
        track.present = True
        track.last_seen = now
        track.lost_at = None
        if point is not None:
            track.x, track.y = point
            track.heading = None
        elif heading is not None:
            track.heading = heading  # keep any earlier position's distance out of it: direction only now
            track.x = track.y = None
        for key in ("distance", "looking", "pitch"):
            if p.get(key) is not None:
                setattr(track, key, p.get(key))

    def _expire(self, now: float):
        self.tracks = [t for t in self.tracks if t.present or now - t.last_seen <= REMEMBER_FOR]
