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
