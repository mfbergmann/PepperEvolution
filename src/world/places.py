"""
Places on Pepper's map (#36): pure readers over a map position and the named places of the active map.

The navigator (``src/navigation/``) reads the position from the bridge and the places from the long-term store;
these functions only compute. Everything is in the map frame: metres, and degrees with left positive.
"""

import math
from typing import Any, Dict, Optional, Sequence, Tuple

FRESH = 60.0  # seconds: an older map position is no longer "where Pepper is"
UNSURE = 1.0  # metres: a localization less sure than this makes the place "probably"
MAX_NAMES = 8  # places named in the every-turn line


def distance_to(place: Any, x: float, y: float) -> float:
    return math.hypot(place.x - x, place.y - y)


def place_at(x: float, y: float, places: Sequence[Any]) -> Optional[Any]:
    """The nearest place whose radius Pepper is within, or None."""
    inside = [p for p in places if distance_to(p, x, y) <= p.radius]
    return min(inside, key=lambda p: distance_to(p, x, y)) if inside else None


def nearest(x: float, y: float, places: Sequence[Any]) -> Optional[Any]:
    return min(places, key=lambda p: distance_to(p, x, y)) if places else None


def unsure(data: Dict[str, Any]) -> bool:
    radii = [float(v) for v in (data.get("uncertainty") or []) if v is not None]
    return bool(radii) and max(radii) > UNSURE


def place_line(position: Optional[Tuple[float, Dict[str, Any]]], places: Sequence[Any], now: float) -> str:
    """One sentence for the mind, every turn: where Pepper is on its map and which places it knows."""
    names = [p.name for p in places]
    if names:
        more = f" and {len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else ""
        known = f"places you know: {', '.join(names[:MAX_NAMES])}{more}"
    else:
        known = "you have not named any places yet"
    if position is None or now - position[0] > FRESH:
        return f"Where you are on your map: not known right now (not localized); {known}."
    data = position[1]
    probably = "probably " if unsure(data) else ""
    if data.get("place"):
        here = f"{probably}at {data['place']}"
    else:
        close = nearest(float(data["x"]), float(data["y"]), places)
        if close is None:
            here = f"{probably}somewhere on your map"
        else:
            metres = distance_to(close, float(data["x"]), float(data["y"]))
            here = f"{probably}about {metres:.0f} m from {close.name}"
    return f"Where you are on your map: {here}; {known}."
