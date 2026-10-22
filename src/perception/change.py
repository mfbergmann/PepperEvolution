"""
Has the view changed? A cheap check on every camera frame (issue #33), so Pepper keeps looking while nobody is in
view without asking a model about every frame.

Each frame is shrunk to a small grayscale thumbnail and compared with the previous one. A change is a share of
thumbnail pixels that moved by more than a level, between two frames taken from the same place: when the head turned
or the base moved since the last frame, the comparison starts again instead of reporting the whole view as new.
"""

import io
import math
from typing import Optional, Sequence

import numpy as np
from PIL import Image

SIZE = (40, 30)  # thumbnail: blurs JPEG noise and small flicker away
LEVEL = 24  # of 255: a pixel this much brighter or darker counts as changed
SHARE = 0.03  # changed share of the thumbnail that counts as a change in the view
HEAD_MOVED = 3.0  # degrees of head yaw or pitch between frames that start the comparison again
BASE_MOVED = (0.05, 3.0)  # metres, degrees of base movement that do the same


class ChangeDetector:
    def __init__(self, level: int = LEVEL, share: float = SHARE):
        self.level = level
        self.share = share
        self.reference: Optional[np.ndarray] = None
        self._head: Optional[Sequence[float]] = None
        self._pose: Optional[Sequence[float]] = None
        self.last_share = 0.0
        self.changes = 0

    def reset(self):
        self.reference = None

    def feed(self, jpeg: bytes, head: Optional[Sequence[float]] = None, pose: Optional[Sequence[float]] = None) -> bool:
        """True when this frame differs from the previous one taken from the same viewpoint."""
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                thumb = np.asarray(image.convert("L").resize(SIZE, Image.Resampling.BILINEAR), dtype=np.int16)
        except Exception:  # noqa: BLE001 - a broken frame is not a change
            return False
        moved = self._viewpoint_moved(head, pose)
        self._head, self._pose = head, pose
        previous, self.reference = self.reference, thumb
        if previous is None or moved:
            self.last_share = 0.0
            return False
        self.last_share = float(np.mean(np.abs(thumb - previous) > self.level))
        if self.last_share >= self.share:
            self.changes += 1
            return True
        return False

    def _viewpoint_moved(self, head: Optional[Sequence[float]], pose: Optional[Sequence[float]]) -> bool:
        if head is not None and self._head is not None:
            if max(abs(a - b) for a, b in zip(head, self._head)) > HEAD_MOVED:
                return True
        if pose is not None and self._pose is not None:
            metres = math.hypot(pose[0] - self._pose[0], pose[1] - self._pose[1])
            degrees = abs(math.degrees(math.remainder(pose[2] - self._pose[2], 2 * math.pi)))
            if metres > BASE_MOVED[0] or degrees > BASE_MOVED[1]:
                return True
        return False
