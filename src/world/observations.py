"""
Observations: the one shape in which anything Pepper senses or does enters its memory (docs/MEMORY.md).

A source (the people detector, odometry, a move, the camera judgements, what was heard or said, later sound,
scene notes and identity) turns what it got into an :class:`Observation` and hands it to
``WorldModel.observe()``. Nothing else writes to the world model, so a new sense is a new source with its own
``source`` tag, not a change to the readers.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# where an observation came from
PEOPLE = "people"  # NAOqi's people detector, through the bridge's debounced events
POSE = "pose"  # Pepper's own position and heading (odometry)
MOTION = "motion"  # a move Pepper made: turn, drive (asked and measured)
CAMERA = "camera"  # judgements on camera frames (waving, showing, facing)
SOUND = "sound"  # direction of a sound (ALSoundLocalization)
SCENE = "scene"  # a short scene note from a vision model
HEARD = "heard"  # something said near Pepper (a final transcript), with the addressee verdict
SAID = "said"  # a sentence Pepper said
IDENTITY = "identity"  # who someone is (opt-in)
MIND = "mind"  # something the mind decided to note

# frames for anything with a direction (docs/MEMORY.md, "Frames")
BODY = "body"  # x forward, y left, angles positive to the left, turning with the base
HEAD = "head"  # the camera's direction; needs the head angles to become a body direction
ODOM = "odom"  # NAOqi's odometry frame (FRAME_WORLD), fixed from NAOqi start-up


@dataclass(frozen=True)
class Observation:
    """One report from one source at one moment. ``at`` is ``time.monotonic()`` (the world model's clock)."""

    source: str
    kind: str
    at: float
    data: Dict[str, Any] = field(default_factory=dict)
    frame: Optional[str] = None
    confidence: Optional[float] = None

    def record(self) -> Dict[str, Any]:
        """A flat dict for the session records (``events.jsonl``), without the monotonic time."""
        out: Dict[str, Any] = {"source": self.source, "obs": self.kind, **self.data}
        if self.frame is not None:
            out["frame"] = self.frame
        if self.confidence is not None:
            out["confidence"] = self.confidence
        return out
