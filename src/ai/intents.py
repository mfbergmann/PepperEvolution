"""
Local intents: fixed commands answered immediately, with no model call.

Borrowed from Autonomous OS's ``intent`` table. Safety and control phrases
("stop", "be quiet", "emergency stop", "wake up") must not wait behind a
model round-trip or an in-flight turn, so they are matched here first and
executed directly against the robot.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from ..pepper.robot import PepperRobot

_PREFIX = re.compile(r"^(?:please\s+)?(?:(?:okay|hey|ok|hi)\b)?[\s,]*(?:pepper\b)?[\s,]*(?:please\s+)?", re.IGNORECASE)
_SUFFIX = re.compile(r"[\s,]+(?:please|now|pepper)$")
_TRAILING = re.compile(r"[\s.!?]+$")
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase; drop 'hey Pepper,' / 'please' / 'now' around the phrase and trailing punctuation."""
    t = _SPACES.sub(" ", text.strip().lower())
    t = _PREFIX.sub("", t, count=1)
    t = _TRAILING.sub("", t)
    for _ in range(2):  # "stop now please"
        t = _SUFFIX.sub("", t)
    return t.strip()


@dataclass
class Intent:
    name: str
    phrases: List[str]
    description: str
    ack: Optional[str] = None  # spoken confirmation, if any
    aborts_turn: bool = False  # cancel the AI turn in progress
    hushes: bool = False  # stop current speech and suppress the rest of the turn's speech
    _regex: List[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._regex = [re.compile(rf"^{p}$") for p in self.phrases]

    def matches(self, text: str) -> bool:
        return any(r.match(text) for r in self._regex)


INTENTS: List[Intent] = [
    Intent(
        "emergency_stop",
        [r"emergency stop", r"e[- ]?stop", r"kill (?:the )?motors"],
        "Kill all motion, speech and animation; rest the robot (needs wake up afterwards).",
        aborts_turn=True,
        hushes=True,
    ),
    Intent(
        "stop",
        [
            r"stop",
            r"stop (?:it|that|moving|there|everything)",
            r"halt",
            r"freeze",
            r"don'?t move",
            r"hold on",
            r"wait",
        ],
        "Stop the base and any animation, cancel the current turn (motors stay on).",
        ack="Okay.",
        aborts_turn=True,
        hushes=True,
    ),
    Intent(
        "quiet",
        [r"(?:be )?quiet", r"shush", r"sh+", r"hush", r"stop talking", r"silence", r"shut up", r"enough"],
        "Stop speaking and finish the turn silently.",
        hushes=True,
    ),
    Intent(
        "wake_up",
        [r"wake up", r"stand up", r"get up", r"wake"],
        "Motors on, standing posture; clears an emergency halt.",
        ack="I'm up.",
    ),
    Intent(
        "rest",
        [r"rest", r"sit down", r"go to sleep", r"sleep", r"relax", r"take a rest"],
        "Rest posture, motors off.",
        ack="Resting.",
    ),
    Intent(
        "look_at_me",
        [r"look at me", r"look here", r"over here", r"pay attention"],
        "Turn people tracking on so the head follows whoever is talking.",
    ),
    Intent(
        "look_ahead",
        [r"look ahead", r"look straight", r"look forward", r"eyes front", r"look straight ahead"],
        "Head to the neutral position.",
    ),
]

AckFn = Callable[[str], Awaitable[Any]]


def match_intent(text: str) -> Optional[Intent]:
    t = normalise(text)
    if not t or len(t) > 40:
        return None
    for intent in INTENTS:
        if intent.matches(t):
            return intent
    return None


class IntentExecutor:
    """Runs a matched intent against the robot. Never raises."""

    def __init__(self, robot: PepperRobot):
        self.robot = robot
        self.logger = logger.bind(module="Intents")

    async def execute(self, intent: Intent) -> Dict[str, Any]:
        self.logger.info(f"Local intent: {intent.name}")
        result: Dict[str, Any] = {"intent": intent.name, "ok": True, "text": intent.ack or ""}
        try:
            if intent.name == "emergency_stop":
                await self.robot.emergency_stop()
            elif intent.name == "stop":
                await self.robot.stop_speaking()
                await self.robot.stop()
            elif intent.name == "quiet":
                await self.robot.stop_speaking()
            elif intent.name == "wake_up":
                await self.robot.wake_up()
            elif intent.name == "rest":
                await self.robot.rest()
            elif intent.name == "look_at_me":
                await self.robot.set_awareness(True)
            elif intent.name == "look_ahead":
                await self.robot.set_awareness(False)
                await self.robot.move_head(0, 0)
        except Exception as exc:  # noqa: BLE001 - report, never crash a control path
            self.logger.error(f"Intent {intent.name} failed: {exc}")
            result["ok"] = False
            result["error"] = str(exc)
        return result
