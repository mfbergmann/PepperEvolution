"""
Identity: who a voice or a face belongs to, compared on the host with people enrolled in the long-term store.

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
from .face import (
    FaceIdentifier,
    FaceModel,
    FrameVotes,
    Gallery,
    gallery_blob,
    gallery_score,
    load_galleries,
    read_faces,
)
from .voice import SecondVoiceWatch, SpeakerModel, VoiceIdentifier, VoiceReading, merge_readings, read_voice

__all__ = [
    "Enrolled",
    "FaceIdentifier",
    "FaceModel",
    "FrameVotes",
    "Gallery",
    "Match",
    "SecondVoiceWatch",
    "SpeakerModel",
    "VoiceIdentifier",
    "VoiceReading",
    "best_match",
    "closest_score",
    "from_blob",
    "gallery_blob",
    "gallery_score",
    "load_enrolled",
    "load_galleries",
    "merge_readings",
    "read_faces",
    "read_voice",
    "robust_centroid",
    "to_blob",
    "unit",
]
