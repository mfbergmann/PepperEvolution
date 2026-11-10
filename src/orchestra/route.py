"""
The per-turn decision as pure functions (docs/ORCHESTRATOR.md, phase 1b).

``AIManager`` gathers the judgements (the addressee gate, the command router, the second-voice watch, the world
model), calls these, and executes what they return. They take plain values and do no I/O, so tests and
``scripts/replay_decisions.py`` can run them on recorded turns and check them against what was recorded.

Control phrases (``src/ai/intents.py``) are not decided here: they act before anything else, with no await in
between, and are recorded afterwards as the ``intent`` path.
"""

from dataclasses import dataclass
from typing import Optional

from ..decide.addressee import addressed

# paths a turn can take; recorded as ``path`` in turns.jsonl
INTENT = "intent"
NOT_ADDRESSED = "not_addressed"
ROUTED_INTENT = "routed_intent"  # the router heard stop or quiet: nothing goes to the mind
LOOK_THEN_MIND = "look_then_mind"  # look at the person at once, the rest of the request to the mind
ROUTED_THEN_MIND = "routed_then_mind"  # a routed action starts at once, the mind says the words
MIND = "mind"
CANCELLED = "cancelled"  # queued behind a turn that "stop" cancelled
PATHS = (INTENT, NOT_ADDRESSED, ROUTED_INTENT, LOOK_THEN_MIND, ROUTED_THEN_MIND, MIND, CANCELLED)

STOPPING = ("stop", "quiet")  # requests that are never held back by the gate's second-voice rule


@dataclass(frozen=True)
class GateSituation:
    """What the addressee gate knows besides the probability (all recorded in the turn's ``addressee``)."""

    someone_looking: bool
    in_conversation: bool  # Pepper answered moments ago
    after_question: bool  # Pepper just asked something
    alone: bool  # at most one person in view for a while


@dataclass(frozen=True)
class Thresholds:
    addressed: float = 0.6
    looking: float = 0.4
    after_question: float = 0.2


@dataclass(frozen=True)
class GateVerdicts:
    usual: bool  # with the conversation bypasses
    by_judgement: bool  # the judgement alone (with someone looking): what decides while a second voice is active

    def final(self, second_voice: bool) -> bool:
        return self.by_judgement if second_voice else self.usual

    @property
    def second_voice_matters(self) -> bool:
        """Whether a second voice would change the answer (only then is it worth waiting for a voice reading)."""
        return self.usual != self.by_judgement


def gate_verdicts(p: Optional[float], situation: GateSituation, thresholds: Thresholds) -> GateVerdicts:
    """The addressee gate's two candidate verdicts for an open-mic utterance.

    - Someone looking at Pepper, being in a conversation with Pepper, or having just been asked something lowers the
      bar (to ``looking``, or ``after_question`` right after a question).
    - In a conversation with nobody else in view, side talk has no one to talk to: answer. Alone with Pepper on
      2026-10-08, 12 of 38 things said to it mid-conversation scored 0.06-0.35 and went unanswered, three of them
      answers to its own questions (the judgement never sees what Pepper said).
    - With a second voice active (SECOND_VOICE, #25) the two people have each other to talk to, whatever the people
      detector counts: being in a conversation with Pepper proves nothing then, and only the judgement and someone
      looking count.
    - No judgement (``p`` is None): answer, as before the gate existed.
    """
    s = situation
    lower = thresholds.after_question if s.after_question else thresholds.looking
    usual = addressed(p, thresholds.addressed, s.someone_looking or s.in_conversation or s.after_question, lower)
    if not usual and s.alone and (s.in_conversation or s.after_question):
        usual = True
    by_judgement = addressed(p, thresholds.addressed, s.someone_looking, thresholds.looking)
    return GateVerdicts(usual, by_judgement)


def accept_route(p: float, threshold: float, planned: bool, is_intent: bool, body_free: bool) -> bool:
    """Start the router's choice at once? Only above the threshold, only an action that may start without the mind
    (``planned``), and, unless it is stop or quiet, only while no turn, command or rest owns the body."""
    if p < threshold or not planned:
        return False
    return is_intent or body_free


def decide_turn(meant: bool, second_voice_decided: bool, routed_intent: Optional[str], routed: bool) -> str:
    """Which path an utterance that is not a control phrase takes, once the judgements are in.

    ``routed``: the router's choice was accepted; ``routed_intent`` its intent (stop, quiet, look_at_me) if any.
    """
    if not meant and second_voice_decided and routed and routed_intent in STOPPING:
        meant = True  # stopping is never held back because of a second voice
    if not meant:
        return NOT_ADDRESSED
    if routed and routed_intent in STOPPING:
        return ROUTED_INTENT
    if routed and routed_intent == "look_at_me":
        return LOOK_THEN_MIND
    if routed:
        return ROUTED_THEN_MIND
    return MIND
