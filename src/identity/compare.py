"""
Comparing identity embeddings: the part voice and face share (docs/MEMORY.md, "Identity").

An embedding is a unit vector from one model. Two are comparable only when the same model made them, and their cosine
is the score. Everything here is pure: no model, no store writes, nothing kept. An identifier makes unit vectors with
its own model, reads what is enrolled with ``load_enrolled(store, kind, model)`` and compares with ``best_match``. The
face side keeps a gallery rather than one vector, so it has its own loader and score (``face.py``).
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

MAD_TO_SIGMA = 1.4826  # median absolute deviation to standard deviation, for normally distributed scores
OUTLIER_SIGMAS = 2.5  # enrolment drops samples this far below the median similarity to the centroid
MIN_SAMPLES_TO_TRIM = 5  # with fewer, nothing can be called an outlier


@dataclass(frozen=True)
class Enrolled:
    """One enrolled embedding, as read from the long-term store."""

    person_id: int
    name: str
    vector: np.ndarray


@dataclass(frozen=True)
class Match:
    person_id: int
    name: str
    score: float


def unit(vector: Any) -> np.ndarray:
    """``vector`` scaled to length 1, in the float32 the store keeps. A zero vector cannot be compared: ValueError."""
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("an embedding with no direction")
    return v / norm


def to_blob(vector: Any) -> bytes:
    return unit(vector).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def robust_centroid(vectors: Sequence[Any]) -> Tuple[np.ndarray, List[int]]:
    """The centre of a set of samples of one person, without the ones that do not fit (someone else in the clip, a
    cough, the fans). Returns the centroid and the indices kept."""
    if not len(vectors):
        raise ValueError("no samples")
    samples = np.stack([unit(v) for v in vectors])
    kept = np.arange(len(samples))
    if len(samples) >= MIN_SAMPLES_TO_TRIM:
        scores = samples @ unit(samples.mean(axis=0))
        median = float(np.median(scores))
        spread = MAD_TO_SIGMA * float(np.median(np.abs(scores - median)))
        kept = np.flatnonzero(scores >= median - OUTLIER_SIGMAS * spread)
    return unit(samples[kept].mean(axis=0)), [int(i) for i in kept]


def load_enrolled(store: Any, kind: str, model: str) -> List[Enrolled]:
    """What this model can be compared with. Read for each comparison and not cached, so "forget me" holds at once."""
    return [Enrolled(i.person_id, i.name, from_blob(i.embedding)) for i in store.identities(kind=kind, model=model)]


def best_match(vector: np.ndarray, enrolled: Sequence[Enrolled]) -> Optional[Match]:
    """The closest enrolled person and how close, whatever the score: deciding what is confident is the caller's."""
    best = max(enrolled, key=lambda e: float(vector @ e.vector), default=None)
    return None if best is None else Match(best.person_id, best.name, float(vector @ best.vector))


def closest_score(vector: np.ndarray, enrolled: Sequence[Enrolled]) -> float:
    return max((float(vector @ e.vector) for e in enrolled), default=-1.0)
