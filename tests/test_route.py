"""
Tests for the per-turn decision as pure functions (src/orchestra/route.py, docs/ORCHESTRATOR.md phase 1b).

The cases are the behaviour AIManager had before the extraction; scripts/replay_decisions.py checks the same on the
recorded sessions (276 of 276 gate verdicts, 2026-11-10).
"""

from src.orchestra import route
from src.orchestra.route import GateSituation, Thresholds, accept_route, decide_turn, gate_verdicts

T = Thresholds()


def situation(looking=False, talking=False, question=False, alone=False):
    return GateSituation(looking, talking, question, alone)


class TestGate:
    def test_threshold_and_no_judgement(self):
        assert gate_verdicts(0.7, situation(), T).usual
        assert not gate_verdicts(0.5, situation(), T).usual
        assert gate_verdicts(None, situation(), T).usual  # fail open: as before the gate

    def test_looking_conversation_and_question_lower_the_bar(self):
        assert gate_verdicts(0.45, situation(looking=True), T).usual
        assert gate_verdicts(0.45, situation(talking=True), T).usual
        assert not gate_verdicts(0.3, situation(talking=True), T).usual
        assert gate_verdicts(0.25, situation(question=True), T).usual  # 0.2 right after a question
        assert not gate_verdicts(0.15, situation(question=True), T).usual

    def test_alone_in_a_conversation_side_talk_has_no_one_to_talk_to(self):
        assert gate_verdicts(0.06, situation(talking=True, alone=True), T).usual
        assert not gate_verdicts(0.06, situation(alone=True), T).usual  # alone, but not talking with Pepper

    def test_a_second_voice_leaves_the_judgement_alone(self):
        v = gate_verdicts(0.3, situation(talking=True, alone=True), T)
        assert v.usual and not v.by_judgement and v.second_voice_matters
        assert v.final(second_voice=False) and not v.final(second_voice=True)
        looking = gate_verdicts(0.45, situation(looking=True), T)
        assert looking.final(True) and not looking.second_voice_matters  # someone looking still counts


class TestRouter:
    def test_accept(self):
        assert accept_route(0.8, 0.7, planned=True, is_intent=False, body_free=True)
        assert not accept_route(0.6, 0.7, True, False, True)  # below the threshold
        assert not accept_route(0.9, 0.7, False, False, True)  # e.g. a drive: Claude decides
        assert not accept_route(0.9, 0.7, True, False, body_free=False)  # a turn or command owns the body
        assert accept_route(0.9, 0.7, True, is_intent=True, body_free=False)  # stop and quiet still start


class TestPath:
    def test_paths(self):
        assert decide_turn(False, False, None, False) == route.NOT_ADDRESSED
        assert decide_turn(True, False, None, False) == route.MIND
        assert decide_turn(True, False, "stop", True) == route.ROUTED_INTENT
        assert decide_turn(True, False, "quiet", True) == route.ROUTED_INTENT
        assert decide_turn(True, False, "look_at_me", True) == route.LOOK_THEN_MIND
        assert decide_turn(True, False, None, True) == route.ROUTED_THEN_MIND

    def test_stopping_is_never_held_back_by_a_second_voice(self):
        assert decide_turn(False, True, "stop", True) == route.ROUTED_INTENT
        assert decide_turn(False, False, "stop", True) == route.NOT_ADDRESSED  # held back by the judgement itself
        assert decide_turn(False, True, None, True) == route.NOT_ADDRESSED
        assert decide_turn(False, True, "look_at_me", True) == route.NOT_ADDRESSED

    def test_every_path_is_listed(self):
        assert set(route.PATHS) >= {route.INTENT, route.CANCELLED, route.MIND, route.NOT_ADDRESSED}
