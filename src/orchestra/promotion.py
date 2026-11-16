"""
The promotion bar (docs/ORCHESTRATOR.md, phase 2): when a candidate routing may replace the current one.

Written before the learner that will be judged by it, so the bar cannot move to fit the candidate: the
candidate replaces the current only when it is no worse on the three failure modes of routing, the blind
readers do not read it as the worse (an exact sign test on their marks), it improves the one thing it was
chosen for, and the data has been cleared for the comparison. Everything is a pure function of these
numbers and the marks — the session records behind them never leave the host machine.
"""

import math
import statistics
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class RouteStats:
    """What a replay of the session tree showed for one routing."""

    not_acted: int  # an action was asked, Pepper did nothing
    claimed_without_tool: int  # Pepper said it would, with no tool call behind it
    unasked_moves: int  # Pepper moved or acted when it was not asked
    first_word_s: List[float] = field(default_factory=list)  # seconds to the first spoken word, per turn
    cost: float = 0.0  # what the replay cost


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided binomial sign test on decided marks only (the caller leaves the ties out).

    The chance of a split at least this unfair, if both routings were equally good. ``sign_test(0, 0)`` is
    1.0: with no decided mark there is no evidence either way.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    p = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n
    return min(1.0, 2 * p)


@dataclass(frozen=True)
class Decision:
    promote: bool
    reasons: List[str]  # each condition that failed, in plain words; empty when it may be promoted


def _median(values: List[float]) -> float:
    return statistics.median(values) if values else float("inf")  # no data is not evidence of being quicker


def decide(
    current: RouteStats,
    candidate: RouteStats,
    marks: List[str],
    aim: str,
    cleared: bool,
    min_marks: int = 40,
    alpha: float = 0.05,
) -> Decision:
    """The bar, in order: no worse on the failure modes, not read as the worse, better at what it was
    chosen for (``aim``: "first_word" or "cost"), and the data cleared. Any condition it fails is named.
    """
    if aim not in ("first_word", "cost"):
        raise ValueError(f"aim must be 'first_word' or 'cost', got {aim!r}")
    reasons: List[str] = []
    for name in ("not_acted", "claimed_without_tool", "unasked_moves"):
        before, after = getattr(current, name), getattr(candidate, name)
        if after > before:
            reasons.append(f"{name}: {before} with the current, {after} with the candidate")
    baseline = sum(1 for m in marks if m == "baseline")
    candidate_wins = sum(1 for m in marks if m == "candidate")
    if len(marks) < min_marks:
        reasons.append(f"blind marks: {len(marks)} read, {min_marks} needed")
    elif baseline > candidate_wins:
        p = sign_test(candidate_wins, baseline)
        if p < alpha:
            reasons.append(
                f"blind marks: the readers read the baseline as the better, {baseline} to {candidate_wins} "
                f"(sign test p={p:.4f})"
            )
    if aim == "first_word":
        before, after = _median(current.first_word_s), _median(candidate.first_word_s)
        if not after < before:
            reasons.append(f"first word: {before:.2f} s with the current, {after:.2f} s with the candidate")
    elif not candidate.cost < current.cost:
        reasons.append(f"cost: {current.cost:.2f} with the current, {candidate.cost:.2f} with the candidate")
    if not cleared:
        reasons.append("the session data is not cleared for this comparison")
    return Decision(promote=not reasons, reasons=reasons)
