"""
Views: what readers get from working memory (docs/MEMORY.md, "Readers are pure functions").

Each view reads the world model's state and returns plain data; none of them changes anything or calls a model.
The every-turn "Around you" sentence is ``WorldModel.summary()``; the views here answer the mind's ``recall`` tool
and other readers.
"""

from typing import Any, Dict, List

RECALL_WINDOW = 600.0  # seconds of the timeline that recall describes
RECALL_EVENTS = 12  # most recent entries only
TEXT_CHARS = 120
QUIET_KINDS = {"track"}  # bookkeeping, for review rather than for the mind


def _ago(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)} s ago"
    return f"{int(seconds // 60)} min ago"


def _clip(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_CHARS else text[: TEXT_CHARS - 1] + "…"


def describe_event(event: Any, now: float) -> str:
    """One line for one timeline entry, for the mind to read."""
    data = event.data
    when = _ago(now - event.at)
    kind = event.kind
    if kind == "heard":
        verdict = "" if data.get("addressed", True) else " (not answered: seemed meant for someone else)"
        return f'{when}: heard "{_clip(data.get("text"))}"{verdict}'
    if kind == "said":
        return f'{when}: you said "{_clip(data.get("text"))}"'
    if kind == "arrived":
        return f"{when}: someone came into view ({data.get('count')} in view)"
    if kind == "left":
        return f"{when}: someone left your view ({data.get('count')} in view)"
    if kind == "back":
        return f"{when}: someone came back into view after a few seconds"
    if kind == "greeting":
        return f"{when}: you greeted someone"
    if kind in ("turn", "drive", "move"):
        verb = {"turn": "turned", "drive": "drove", "move": "moved"}[kind]
        return f"{when}: you {verb} {data.get('description', '')}".rstrip()
    if kind == "moved_unexpectedly":
        return f"{when}: you were moved (about {data.get('degrees')}°, {data.get('metres')} m) without a command"
    if kind == "camera_event":
        return f"{when}: your camera saw {data.get('what')}"
    if kind == "remembered":
        return f"{when}: you stored a fact about {data.get('about')}"
    return f"{when}: {kind}"


def recall(world: Any) -> Dict[str, Any]:
    """Working memory for the mind's recall tool: who is around now and what happened in the last ten minutes."""
    now = world.now()
    events = [e for e in world.timeline.recent(within=RECALL_WINDOW, now=now) if e.kind not in QUIET_KINDS]
    events = events[-RECALL_EVENTS:]
    out: Dict[str, Any] = {}
    around = world.summary()
    if around:
        out["around"] = around
    lines: List[str] = [describe_event(e, now) for e in events]
    if lines:
        out["recently"] = lines
    return out
