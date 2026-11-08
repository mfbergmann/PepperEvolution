"""
Glance towards a voice (issue #33): when someone speaks from where Pepper sees nobody, turn the head that way, so the
camera and NAOqi's people detector can pick them up and face tracking can take over. A reflex, no model; head only,
never the base.

``glance_target()`` is a pure function of the utterance's voice direction (``rec["voice"]``: bearing in the body
frame, number of located sounds and their spread), the people in view and the head's angle, so it can be replayed on
recorded sessions. Voice direction from three or more sounds was within 1-16° of the speaker in front of Pepper, while
single sounds were off by 40-56° (robot, 2026-10-20, #31); so a glance needs several sounds that agree.
"""

from typing import Any, Iterable, Optional

MIN_SOUNDS = 3
MAX_SPREAD = 15.0  # degrees: the located sounds must agree
IN_VIEW_WITHIN = 40.0  # degrees: someone in view this close to the voice is probably the speaker
ALREADY_LOOKING = 20.0  # degrees: the head already points there
HEAD_LIMIT = 110.0  # HeadYaw reaches ±119.5°; behind Pepper the head cannot look, so it looks as far as it can
EVERY = 4.0  # seconds between glances: the next utterance in a conversation must not swing the head again


def glance_target(
    voice: Optional[dict],
    people_yaws: Iterable[Optional[float]],
    head_yaw: Optional[float],
    now: float,
    last_glance: Optional[float],
) -> Optional[float]:
    """The head yaw to glance at (degrees, left positive), or None."""
    if not voice or voice.get("bearing") is None:
        return None
    if (voice.get("sounds") or 0) < MIN_SOUNDS or (voice.get("spread") or 0.0) > MAX_SPREAD:
        return None
    if last_glance is not None and now - last_glance < EVERY:
        return None
    bearing = float(voice["bearing"])
    if any(yaw is not None and abs(float(yaw) - bearing) <= IN_VIEW_WITHIN for yaw in people_yaws):
        return None
    target = max(-HEAD_LIMIT, min(HEAD_LIMIT, bearing))
    if head_yaw is not None and abs(head_yaw - target) <= ALREADY_LOOKING:
        return None
    return target


def people_yaws(world: Any) -> list:
    return [getattr(person, "yaw", None) for person in (getattr(world, "people", None) or [])]


# ----------------------------------------------------------------------------------------------------------------------
# Looking around for a second voice: two voices were heard (SECOND_VOICE), but the camera may show only one person (on
# 2026-11-05 the colleague was in none of the frames of a conversation). Head only, a short sweep, then back.
# ----------------------------------------------------------------------------------------------------------------------

SWING = 50.0  # degrees either side of where the head points: roughly one camera width (about 55°) beyond the view
DISTINCT_STOP = 35.0  # degrees: a stop this close to another (or to the start) adds little
HOLD = 1.6  # seconds at each stop: the stream sends about one frame a second while someone is in view
SAME_PERSON = 25.0  # degrees: faces this close together count as one person
LOOK_AROUND_EVERY = 90.0  # seconds: once per second-voice episode (SECOND_VOICE_WINDOW is 60 s), not per utterance


def voice_bearing(voice: Optional[dict]) -> Optional[float]:
    """The voice's bearing when enough located sounds agree on it (as for a glance), else None."""
    if not voice or voice.get("bearing") is None:
        return None
    if (voice.get("sounds") or 0) < MIN_SOUNDS or (voice.get("spread") or 0.0) > MAX_SPREAD:
        return None
    return float(voice["bearing"])


def look_around_yaws(
    head_yaw: Optional[float], people_yaws: Iterable[Optional[float]], voice: Optional[dict] = None
) -> list:
    """Head yaws (degrees, left positive) to look at in turn: the voice's direction first if it is known, then either
    side of where the head points now. Stops where someone is already in view, or near another stop, are left out."""
    start = float(head_yaw) if head_yaw is not None else 0.0
    seen = [float(y) for y in people_yaws if y is not None]
    wanted = []
    bearing = voice_bearing(voice)
    if bearing is not None:
        wanted.append(bearing)
    wanted += [start + SWING, start - SWING]
    stops: list = []
    for yaw in wanted:
        yaw = max(-HEAD_LIMIT, min(HEAD_LIMIT, yaw))
        if abs(yaw - start) < DISTINCT_STOP or any(abs(yaw - s) < DISTINCT_STOP for s in stops):
            continue
        if any(abs(yaw - s) <= IN_VIEW_WITHIN / 2 for s in seen):
            continue  # already in view there
        stops.append(round(yaw, 1))
    return stops


def others_found(bearings: Iterable[Optional[float]], known: Iterable[Optional[float]]) -> int:
    """How many people the bearings of faces and people seen during a look-around point at, beyond those already
    known (the person Pepper was facing). Bearings within SAME_PERSON of each other count once."""
    known_yaws = [float(k) for k in known if k is not None]
    fresh = sorted(float(b) for b in bearings if b is not None)
    fresh = [b for b in fresh if all(abs(b - k) > SAME_PERSON for k in known_yaws)]
    people = 0
    last: Optional[float] = None
    for b in fresh:
        if last is None or b - last > SAME_PERSON:
            people += 1
        last = b
    return people
