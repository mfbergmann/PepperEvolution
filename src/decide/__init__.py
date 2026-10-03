"""
Situation judgements ("system one"): small local decision models answer fixed questions with probabilities before
the mind is called (docs/ARCHITECTURE.md, "Situation judgements"; issue #20).
"""

from .addressee import addressed, judge_addressee
from .client import DecisionClient

__all__ = ["DecisionClient", "addressed", "judge_addressee"]
