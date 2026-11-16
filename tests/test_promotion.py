"""
Tests for the promotion bar (src/orchestra/promotion.py, docs/ORCHESTRATOR.md phase 2): the sign test on
the blind marks, and each of the four conditions failing on its own, plus a candidate that clears all of
them and a case that may not promote.
"""

from dataclasses import replace

import pytest

from src.orchestra.promotion import RouteStats, decide, sign_test

MARKS = ["candidate"] * 20 + ["equal"] * 20  # 40 marked turns; the candidate is never read as the worse


def base_stats():
    """A candidate that clears the bar: no worse on the failure modes, and quicker to the first word."""
    return (
        RouteStats(not_acted=2, claimed_without_tool=1, unasked_moves=3, first_word_s=[2.0, 3.0, 4.0], cost=120.0),
        RouteStats(not_acted=1, claimed_without_tool=1, unasked_moves=2, first_word_s=[1.0, 2.0, 4.0], cost=130.0),
    )


class TestSignTest:
    def test_known_values(self):
        assert sign_test(0, 0) == 1.0
        assert sign_test(0, 10) == pytest.approx(0.00195, abs=0.0001)
        assert sign_test(5, 5) == 1.0

    def test_two_sided(self):
        assert sign_test(1, 0) == pytest.approx(sign_test(0, 1))
        assert sign_test(7, 3) == pytest.approx(sign_test(3, 7))


class TestDecide:
    def test_a_candidate_that_clears_the_bar(self):
        current, candidate = base_stats()
        d = decide(current, candidate, MARKS, "first_word", True)
        assert d.promote and d.reasons == []

    def test_worse_on_a_failure_mode(self):
        current, candidate = base_stats()
        d = decide(current, replace(candidate, not_acted=3), MARKS, "first_word", True)
        assert not d.promote and len(d.reasons) == 1 and "not_acted" in d.reasons[0]

    def test_read_significantly_worse_by_the_readers(self):
        current, candidate = base_stats()
        marks = ["baseline"] * 30 + ["candidate"] * 5 + ["equal"] * 5
        d = decide(current, candidate, marks, "first_word", True)
        assert not d.promote and len(d.reasons) == 1 and "baseline" in d.reasons[0]

    def test_read_worse_but_not_significantly_is_no_reason_to_fail(self):
        current, candidate = base_stats()
        marks = ["baseline"] * 4 + ["candidate"] * 3 + ["equal"] * 33
        d = decide(current, candidate, marks, "first_word", True)
        assert d.promote

    def test_ties_count_towards_min_marks(self):
        current, candidate = base_stats()
        marks = ["candidate"] * 25 + ["equal"] * 14  # 39 marked turns, one short
        d = decide(current, candidate, marks, "first_word", True)
        assert not d.promote and len(d.reasons) == 1 and "marks" in d.reasons[0]

    def test_aim_not_improved(self):
        current, candidate = base_stats()
        same_speed = replace(candidate, first_word_s=[2.0, 3.0, 4.0])  # median 3 s, no quicker
        d = decide(current, same_speed, MARKS, "first_word", True)
        assert not d.promote and len(d.reasons) == 1 and "first word" in d.reasons[0]

    def test_cost_aim(self):
        current, _ = base_stats()
        cheaper = RouteStats(not_acted=2, claimed_without_tool=1, unasked_moves=3, first_word_s=[9.0], cost=110.0)
        assert decide(current, cheaper, MARKS, "cost", True).promote
        d = decide(current, replace(cheaper, cost=120.0), MARKS, "cost", True)
        assert not d.promote and len(d.reasons) == 1 and "cost" in d.reasons[0]

    def test_data_not_cleared(self):
        current, candidate = base_stats()
        d = decide(current, candidate, MARKS, "first_word", False)
        assert not d.promote and len(d.reasons) == 1 and "cleared" in d.reasons[0]

    def test_unknown_aim(self):
        current, candidate = base_stats()
        with pytest.raises(ValueError):
            decide(current, candidate, MARKS, "speed", True)
