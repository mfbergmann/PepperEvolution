"""
Identity: who a voice (and later a face) belongs to, compared on the host with people enrolled in the long-term store.

Opt-in only (docs/MEMORY.md). Someone who is not enrolled has no record and no embedding kept, ever.
"""

from .compare import (
    Enrolled,
    Match,
    best_match,
    closest_score,
    from_blob,
    load_enrolled,
    robust_centroid,
    to_blob,
    unit,
)
from .voice import SecondVoiceWatch, SpeakerModel, VoiceIdentifier, VoiceReading, merge_readings, read_voice

__all__ = [
    "Enrolled",
    "Match",
    "SecondVoiceWatch",
    "SpeakerModel",
    "VoiceIdentifier",
    "VoiceReading",
    "best_match",
    "closest_score",
    "from_blob",
    "load_enrolled",
    "merge_readings",
    "read_voice",
    "robust_centroid",
    "to_blob",
    "unit",
]
