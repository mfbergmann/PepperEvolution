"""
From a spot in a photo to a direction Pepper can look or point in (#6, #28).

Directions are in the body frame, in degrees: yaw left positive (like the turn tool), pitch down positive (like
the head). A spot is given as fractions of the photo: x from 0 (left edge) to 1 (right edge), y from 0 (top) to 1
(bottom), which is how a vision model can describe where something is in an image.

Pepper's cameras (NAOqi 2.5 docs, the OV5640 sensor): 56.3 degrees across, 43.7 degrees up and down. On the virtual
Pepper (2026-10-09) the forehead camera looks exactly where the head looks, and the mouth camera is tilted 40
degrees down from it. The spot's direction is the head's direction when the photo was taken (measured by the bridge)
plus the spot's offset from the middle of the frame; the camera's few centimetres of offset from the head joint
are ignored, which is right for anything more than about a metre away.
"""

import math
from typing import Any, Optional, Tuple

CAMERA_FOV = {0: (56.3, 43.7), 1: (56.3, 43.7)}  # (horizontal, vertical) degrees
CAMERA_PITCH = {0: 0.0, 1: 40.0}  # degrees down from the head's direction
CHEST_BELOW_EYES = 15.0  # degrees: point at someone's chest, not their face


def offsets(x: float, y: float, camera: int = 0) -> Tuple[float, float]:
    """(yaw, pitch) of a spot relative to the middle of the frame. Spots are clamped to the frame."""
    hfov, vfov = CAMERA_FOV.get(camera, CAMERA_FOV[0])
    x = min(max(float(x), 0.0), 1.0)
    y = min(max(float(y), 0.0), 1.0)
    yaw = -math.degrees(math.atan((2 * x - 1) * math.tan(math.radians(hfov / 2))))
    pitch = math.degrees(math.atan((2 * y - 1) * math.tan(math.radians(vfov / 2))))
    return yaw, pitch


def photo_direction(photo: Any, x: float, y: float) -> Tuple[float, float, bool]:
    """(yaw, pitch, measured) of a spot in ``photo``: ``measured`` is False when the head's direction at the time
    was only the last command (face tracking may have moved it)."""
    camera = int(getattr(photo, "camera", 0) or 0)
    yaw_off, pitch_off = offsets(x, y, camera)
    measured = getattr(photo, "head_measured", None)
    head_yaw: Optional[float]
    head_pitch: Optional[float]
    if measured and len(measured) >= 2 and None not in measured[:2]:
        head_yaw, head_pitch, known = float(measured[0]), float(measured[1]), True
    else:
        head_yaw, head_pitch, known = getattr(photo, "head_yaw", None), None, False
    yaw = (head_yaw or 0.0) + yaw_off
    pitch = (head_pitch or 0.0) + CAMERA_PITCH.get(camera, 0.0) + pitch_off
    return round(yaw, 1), round(pitch, 1), known
