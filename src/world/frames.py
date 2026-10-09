"""
Frames and Pepper's pose: keeping directions true while Pepper turns and drives (docs/MEMORY.md, "Frames").

- Body frame: x forward, y left, angles positive to the left (the ``turn`` tool's convention). NAOqi's people
  positions arrive in the torso frame, which turns with the base, so their yaw is already body-relative.
- Odometry frame: NAOqi's FRAME_WORLD (``ALMotion.getRobotPosition(True)``), fixed from NAOqi start-up.

A person seen at body bearing ``b`` and distance ``d`` is stored at ``pose ⊕ (d cos b, d sin b)``; their bearing
later is ``atan2(y - y_r, x - x_r) - theta``. A turn changes only ``theta``; a drive moves the robot and every
stored bearing follows from the geometry.
"""

import math
from dataclasses import dataclass, replace
from typing import Optional, Tuple

MEASURED = "measured"  # from the bridge (odometry)
DEAD_RECKONED = "dead_reckoned"  # from the host's own completed moves


def wrap(angle: float) -> float:
    """An angle in radians, wrapped to (-pi, pi]."""
    a = math.fmod(angle + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


def wrap_deg(degrees: float) -> float:
    return math.degrees(wrap(math.radians(degrees)))


@dataclass(frozen=True)
class Pose:
    """Pepper's position (metres) and heading (radians, left positive) in the odometry frame."""

    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    at: float = 0.0
    source: str = DEAD_RECKONED
    uncertain: bool = False

    def to_odom(self, bearing_deg: float, distance: float) -> Tuple[float, float]:
        """A point at ``bearing_deg`` (body frame) and ``distance``, in odometry coordinates."""
        a = self.theta + math.radians(bearing_deg)
        return self.x + distance * math.cos(a), self.y + distance * math.sin(a)

    def heading_of(self, bearing_deg: float) -> float:
        """A body-frame bearing as an absolute direction in the odometry frame (radians)."""
        return wrap(self.theta + math.radians(bearing_deg))

    def bearing_to(self, x: float, y: float) -> float:
        """Body-frame bearing (degrees, left positive) to an odometry point."""
        return math.degrees(wrap(math.atan2(y - self.y, x - self.x) - self.theta))

    def bearing_of(self, heading: float) -> float:
        """Body-frame bearing (degrees) of an absolute odometry direction (radians)."""
        return math.degrees(wrap(heading - self.theta))

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.x, y - self.y)

    def turned(self, degrees: float, at: float, uncertain: bool = False) -> "Pose":
        return replace(
            self,
            theta=wrap(self.theta + math.radians(degrees)),
            at=at,
            source=DEAD_RECKONED,
            uncertain=self.uncertain or uncertain,
        )

    def driven(self, forward: float, left: float = 0.0, at: float = 0.0, uncertain: bool = False) -> "Pose":
        c, s = math.cos(self.theta), math.sin(self.theta)
        return replace(
            self,
            x=self.x + forward * c - left * s,
            y=self.y + forward * s + left * c,
            at=at,
            source=DEAD_RECKONED,
            uncertain=self.uncertain or uncertain,
        )

    def moved_from(self, other: "Pose") -> Tuple[float, float]:
        """How far (metres) and how much (degrees) this pose differs from ``other``."""
        return math.hypot(self.x - other.x, self.y - other.y), abs(math.degrees(wrap(self.theta - other.theta)))


def pose_from(data, at: float) -> Optional[Pose]:
    """A measured pose from the bridge's ``[x, y, theta]`` (metres, radians), or None if unusable."""
    try:
        x, y, theta = (float(v) for v in list(data)[:3])
    except (TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in (x, y, theta)):
        return None
    return Pose(x, y, wrap(theta), at=at, source=MEASURED)
