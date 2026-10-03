"""
Command router: pick the first physical action to start right now from Pepper's fixed list (issue #21).

A local decision model (Nimble) chooses among Pepper's actions in about 0.15 s, so a safe action can start while
Claude, in parallel, prepares the words. The list, the state and the question are exactly what was benchmarked on
2026-10-03 (26 of 28 direct commands right at p >= 0.7, no wrong actions, no action on a question or conversation);
changing the wording means re-running that replay (``results/system-one/bench_router.py``, local only).

Only actions in ``START_AT_ONCE`` run without the model: head moves, gestures, turns in place, stop and be quiet.
Drives ("come here", forward, back) and a full spin still go through Claude, with the bridge's guards.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

MODEL = "nimble"

ACTIONS = {
    "stop": "Stop moving or doing what it is doing right now",
    "be_quiet": "Stop talking, be quiet for a while",
    "look_left": "Turn the head to look to its left",
    "look_right": "Turn the head to look to its right",
    "look_up": "Look up",
    "look_down": "Look down",
    "look_at_me": "Look at the person speaking, face them with its head",
    "turn_left": "Turn the whole body to the left on the spot",
    "turn_right": "Turn the whole body to the right on the spot",
    "turn_around": "Turn the whole body around (half a turn) on the spot",
    "spin_circle": "Spin a full circle on the spot",
    "move_forward": "Drive forward a little",
    "move_back": "Drive backwards a little",
    "come_here": "Come to the person speaking",
    "wave": "Wave hello or goodbye with a hand",
    "nod": "Nod the head",
    "shake_hand": "Offer a hand for a handshake",
    "talk": "No physical action to start: a question, conversation, thanks, something that needs thought or that "
    "Pepper cannot do",
}

QUESTION = {
    "action": {
        "type": "choice",
        "instructions": "Which physical action should Pepper start right now? If several are asked for, the first "
        "one. If none, talk.",
        "criteria": ACTIONS,
    }
}

STATE = (
    "Pepper is a humanoid robot on wheels. Someone just spoke to it. Speech recognition makes mistakes "
    '(Pepper\'s name is often misheard).\nSaid just before: "{prev}"\nJust said: "{utt}"'
)

START_AT_ONCE = {
    "stop",
    "be_quiet",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "look_at_me",
    "turn_left",
    "turn_right",
    "turn_around",
    "wave",
    "nod",
    "shake_hand",
}

ANIMATIONS = {
    "wave": "animations/Stand/Gestures/Hey_1",
    "nod": "animations/Stand/Gestures/Yes_1",
    "shake_hand": "animations/Stand/Gestures/Give_3",
}

DEFAULT_TURN = 90.0
DEFAULT_LOOK = 60.0
A_LITTLE = 30.0
MAX_TURN = 180.0
MAX_LOOK = 90.0

_NUMBER_WORDS = {
    "a hundred and eighty": 180,
    "one hundred and eighty": 180,
    "one eighty": 180,
    "a hundred and twenty": 120,
    "one hundred and twenty": 120,
    "forty five": 45,
    "forty-five": 45,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_A_LITTLE = re.compile(r"\b(a (little|tiny|wee)? ?(bit|touch)|a little|slightly|tiny bit|farther|further|more)\b", re.I)


def build_state(previous: str, text: str) -> str:
    return STATE.format(prev=previous or "(nothing)", utt=text)


async def choose(client, previous: str, text: str, model: str = MODEL) -> Optional[Tuple[str, float]]:
    """(action, probability) or ``None`` when the model gave no answer (fail open: Claude handles it)."""
    answers = await client.ask(model, build_state(previous, text), QUESTION)
    try:
        choice = answers["action"]["choice"]
        return choice, float(answers["action"]["probabilities"][choice])
    except (KeyError, TypeError, ValueError):
        return None


def amount(text: str, default: float) -> float:
    """Degrees asked for: "90 degrees", "ninety", "a little bit farther" (30), else ``default``."""
    digits = re.search(r"(\d{1,3})\s*(?:degrees?|°)?", text)
    if digits:
        return float(digits.group(1))
    lowered = text.lower()
    for words in sorted(_NUMBER_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(words)}\b", lowered):
            return float(_NUMBER_WORDS[words])
    if _A_LITTLE.search(text):
        return A_LITTLE
    return default


@dataclass
class Routed:
    """An action to start at once. ``tool`` is the model tool it stands for (its first repeat is not executed)."""

    action: str
    probability: float
    description: str  # for the model: "turning left 90 degrees"
    tool: Optional[str]  # move_head, turn, play_animation; None for intents
    args: Dict[str, Any]
    intent: Optional[str] = None  # "stop" / "quiet": run through the intent path instead


def plan(action: str, probability: float, text: str) -> Optional[Routed]:
    """What to do at once for a chosen action, or ``None`` if it must go through Claude."""
    if action not in START_AT_ONCE:
        return None
    if action == "stop":
        return Routed(action, probability, "stopping", None, {}, intent="stop")
    if action == "be_quiet":
        return Routed(action, probability, "being quiet", None, {}, intent="quiet")
    if action in ("look_left", "look_right"):
        yaw = min(amount(text, DEFAULT_LOOK), MAX_LOOK)
        side = "left" if action == "look_left" else "right"
        return Routed(
            action, probability, f"looking {side}", "move_head", {"yaw": yaw if side == "left" else -yaw, "pitch": 0}
        )
    if action == "look_up":
        return Routed(action, probability, "looking up", "move_head", {"yaw": 0, "pitch": -20})
    if action == "look_down":
        return Routed(action, probability, "looking down", "move_head", {"yaw": 0, "pitch": 15})
    if action == "look_at_me":
        return Routed(action, probability, "looking at the person", None, {}, intent="look_at_me")
    if action in ("turn_left", "turn_right", "turn_around"):
        if action == "turn_around":
            degrees = 180.0
        else:
            degrees = min(amount(text, DEFAULT_TURN), MAX_TURN)
        angle = -degrees if action == "turn_right" else degrees
        words = "turning around" if action == "turn_around" else f"turning {action[5:]} {degrees:.0f} degrees"
        return Routed(action, probability, words, "turn", {"angle": angle})
    if action in ANIMATIONS:
        words = {"wave": "waving", "nod": "nodding", "shake_hand": "offering a hand"}[action]
        return Routed(action, probability, words, "play_animation", {"name": ANIMATIONS[action]})
    return None
