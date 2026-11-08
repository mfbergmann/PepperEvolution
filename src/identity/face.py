"""
Whose face is this? (#14) Compares the faces in the camera stream's frames with the people enrolled in the long-term
store, on the host's CPU, on a thread of its own, dropping frames while busy, so the stream and the judgements never
wait for it.

A match is a social cue and never authentication: a photo or a screen showing the person is expected to match, and no
permission, tool or safety behaviour may depend on it (docs/SAFETY.md).

- Models: YuNet (OpenCV zoo, MIT), run on the frame upscaled 2x so faces of about 12 px are found, and InsightFace's
  ``w600k_r50`` through onnxruntime. The pretrained ``w600k_r50`` weights are for non-commercial research only. Paths
  are given; nothing is downloaded.
- Method, as validated on held-out sessions (docs/MEMORY.md, "Identity"): the mean of the three best cosines against the
  person's gallery, at 0.45, and only 3 of the last 5 different frames count as recognising them. A gallery is stored
  whole in the person's one ``identity`` row: there is no centroid here, as the threshold was not measured on one.
- Kept: nothing. The gallery is read from the store for every frame, on the event loop's thread, so "forget me" holds
  from the next frame. A face that matches nobody is embedded, compared and dropped inside the call; only how many faces
  a frame had goes further. With nobody enrolled, nothing is even detected. Nothing here writes to disk.
- Failing: ``FaceIdentifier`` never raises into the camera stream; a frame that fails is a frame not read, and the
  reason (a type name, no image data) is in ``status()``.
"""

import asyncio
import os
import statistics
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from loguru import logger

from ..perception.geometry import offsets
from ..world.observations import IDENTITY
from .compare import unit
from .voice import model_name

KIND = "face"
DEFAULT_DETECTOR = "~/.local/share/pepper-models/face_yunet.onnx"
DEFAULT_MODEL = "~/.local/share/pepper-models/face_w600k_r50.onnx"

# Set from the held-out evaluation for YuNet 2x + w600k_r50, and only for that pair: thresholds do not carry over
THRESHOLD = 0.45
TOP = 3  # a score is the mean of this many best cosines against the gallery
VOTES = 3  # of the last ...
VOTE_FRAMES = 5  # ... different frames: one frame is a hint, a vote is a recognition
MIN_FACE_PX = 12.0  # narrower faces are not judged (90 % accepted at 12 px, falling fast below 10)
DETECT_SCORE = 0.6
UPSCALE = 2
MAX_FACES = 8  # the recogniser's cost is per face
# Speech recognition shares the CPU: 91 ms a face on 4 threads and 295 ms on 1, on a desktop at a load of 3-4. A second
# thread takes most of that gain, at a frame a second or two.
THREADS = 2
REFRESH = 15.0  # seconds between notes to working memory while someone stays recognised
STATS_EVERY = 60.0  # seconds between lines of counts in the session record
PLACES_KEPT = 64  # face positions kept in memory for a look-around (src/ai/glance.py): about a minute at 1 fps

# The 112x112 alignment template the ArcFace family was trained on (eyes, nose, mouth corners)
_ARCFACE_TEMPLATE = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


@dataclass(frozen=True)
class Gallery:
    """One person's enrolled faces: N unit vectors, one per row."""

    person_id: int
    name: str
    vectors: np.ndarray


@dataclass(frozen=True)
class Seen:
    """A face large enough to judge, for the length of one call."""

    x: float  # the box's centre as a fraction of the frame, 0 left to 1 right (geometry.offsets)
    y: float
    width: float  # pixels
    vector: np.ndarray


@dataclass(frozen=True)
class Match:
    """A face at the threshold for someone enrolled. Faces that are not leave no Match."""

    person_id: int
    name: str
    score: float
    x: float
    y: float
    width: float


@dataclass(frozen=True)
class FaceReading:
    faces: int = 0  # how many faces this frame had, judged or not
    matches: Tuple[Match, ...] = ()  # at most one per person, their best
    spent: float = 0.0
    places: Tuple[Tuple[float, float], ...] = ()  # (x, y) of every face judged, anyone's: where, never who


def gallery_blob(vectors: Any) -> bytes:
    """What the store keeps for a gallery: each row at unit length, in the float32 the store keeps, one after another."""
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or not len(array):
        raise ValueError("a gallery is N x D")
    return np.stack([unit(row) for row in array]).tobytes()


def load_galleries(store: Any, model: str) -> List[Gallery]:
    """Who this model can be compared with. Read for each frame and not cached, so "forget me" holds at once."""
    return [
        Gallery(i.person_id, i.name, np.frombuffer(i.embedding, dtype=np.float32).reshape(i.samples, -1))
        for i in store.identities(kind=KIND, model=model)
    ]


def gallery_score(vector: np.ndarray, gallery: np.ndarray, top: int = TOP) -> float:
    """The mean of the ``top`` best cosines between a unit vector and a gallery of them."""
    cosines = gallery @ vector
    return float(np.sort(cosines)[-min(top, len(cosines)) :].mean())


def read_faces(seen: Sequence[Seen], galleries: Sequence[Gallery], threshold: float = THRESHOLD) -> Tuple[Match, ...]:
    """Each person's best face in a frame, if it reaches the threshold. A face goes to the one person it is closest to."""
    best: Dict[int, Match] = {}
    for face in seen:
        scored = [(gallery_score(face.vector, g.vectors), g) for g in galleries]
        if not scored:
            continue
        score, gallery = max(scored, key=lambda pair: pair[0])
        if score >= threshold and (gallery.person_id not in best or score > best[gallery.person_id].score):
            best[gallery.person_id] = Match(gallery.person_id, gallery.name, score, face.x, face.y, face.width)
    return tuple(sorted(best.values(), key=lambda m: -m.score))


def bearing_of(match: Match, head_yaw: Optional[float]) -> Optional[float]:
    """Body-frame direction of a face (degrees, left positive), or None without the head's angle for the frame."""
    return None if head_yaw is None else head_yaw + offsets(match.x, match.y)[0]


class FrameVotes:
    """VOTES of the last VOTE_FRAMES frames, per person. A frame without them, face or not, is a vote against, so leaving
    the view ends a recognition within VOTE_FRAMES frames."""

    def __init__(self, votes: int = VOTES, frames: int = VOTE_FRAMES):
        self.votes = votes
        self._last: Deque[Set[int]] = deque(maxlen=frames)

    def update(self, people: Set[int]) -> Set[int]:
        """One more frame, with who matched in it. Returns who holds the vote now."""
        self._last.append(set(people))
        return {person for person in set().union(*self._last) if self.count(person) >= self.votes}

    def count(self, person: int) -> int:
        return sum(person in frame for frame in self._last)

    def forget(self, person: int):
        for frame in self._last:
            frame.discard(person)


class FaceModel:
    """Detector and recogniser. Called from one thread at a time; ``faces`` may raise."""

    def __init__(
        self,
        detector_path: str = DEFAULT_DETECTOR,
        model_path: str = DEFAULT_MODEL,
        threads: int = THREADS,
        detect_score: float = DETECT_SCORE,
        min_face_px: float = MIN_FACE_PX,
        upscale: int = UPSCALE,
        max_faces: int = MAX_FACES,
    ):
        import cv2
        import onnxruntime

        detector_path, model_path = os.path.expanduser(detector_path), os.path.expanduser(model_path)
        for path in (detector_path, model_path):
            if not os.path.exists(path):
                raise FileNotFoundError(f"face model not found: {path}")
        self.name = model_name(model_path)
        self.min_face_px = min_face_px
        self.upscale = upscale
        self.max_faces = max_faces
        self._cv2 = cv2
        cv2.setNumThreads(threads)  # YuNet; the rest of the host does not use OpenCV
        self._detector = cv2.FaceDetectorYN.create(
            detector_path, "", (320, 240), detect_score, 0.3, max(max_faces * 2, 16)
        )
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self._template = np.array(_ARCFACE_TEMPLATE, dtype=np.float32)

    def faces(self, jpeg: bytes) -> Tuple[int, List[Seen]]:
        """How many faces a JPEG holds, and those large enough to judge, each with its embedding (at most max_faces)."""
        cv2 = self._cv2
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("not an image")
        high, wide = image.shape[:2]
        scale = self.upscale
        scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        self._detector.setInputSize((scaled.shape[1], scaled.shape[0]))
        _, found = self._detector.detect(scaled)
        if found is None:
            return 0, []
        seen = []
        for face in found[: self.max_faces]:
            x, y, width, height = face[:4] / scale
            if width < self.min_face_px:
                continue
            vector = self._embed(image, (face[4:14] / scale).reshape(5, 2).astype(np.float32))
            if vector is not None:
                seen.append(Seen(float((x + width / 2) / wide), float((y + height / 2) / high), float(width), vector))
        return len(found), seen

    def _embed(self, image: np.ndarray, landmarks: np.ndarray) -> Optional[np.ndarray]:
        cv2 = self._cv2
        matrix = cv2.estimateAffinePartial2D(landmarks, self._template, method=cv2.LMEDS)[0]
        if matrix is None:
            return None
        crop = cv2.warpAffine(image, matrix, (112, 112), borderValue=0.0)
        blob = ((crop[:, :, ::-1].astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        return unit(self._session.run(None, {self._input: blob})[0][0])


@dataclass
class _Counts:
    """For the session record and ``status()``: how often, never who or what a face was like."""

    offered: int = 0
    repeats: int = 0  # byte-identical to the frame before: not new evidence, so not a vote
    dropped: int = 0  # the previous frame was still being read
    read: int = 0
    with_face: int = 0
    matched: int = 0  # frames where someone enrolled matched
    failed: int = 0
    spent: List[float] = field(default_factory=list)


class FaceIdentifier:
    """Reads frames offered by the camera stream, and tells working memory and the long-term store about a recognition
    when the vote is first won and then every REFRESH seconds, never per frame. All of it except the reading itself runs
    on the event loop's thread."""

    def __init__(
        self,
        store: Any,
        world: Any = None,
        memory: Any = None,
        recorder: Any = None,
        detector_path: str = DEFAULT_DETECTOR,
        model_path: str = DEFAULT_MODEL,
        threshold: float = THRESHOLD,
        threads: int = THREADS,
        model: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.world = world
        self.memory = memory
        self.recorder = recorder
        self.detector_path = detector_path
        self.model_path = model_path
        self.threshold = threshold
        self.threads = threads
        self.model = model
        self.votes = FrameVotes()
        self.counts = _Counts()
        self.last_error: Optional[str] = None
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face-id")
        self._busy = False
        self._previous: Optional[bytes] = None
        self._held: Set[int] = set()  # who holds the vote
        self._noted: Dict[int, float] = {}  # when working memory last heard of them
        self._known: Set[int] = set()  # who was enrolled at the last look, to notice someone forgotten
        self._stats_at = clock()
        self._places: Deque[Tuple[float, Optional[float]]] = deque(maxlen=PLACES_KEPT)  # (when, bearing)

    async def start(self) -> None:
        """Load the models (under a second; may fail: no file, no OpenCV). The caller decides what failing means."""
        if self.model is None:
            self.model = await asyncio.get_running_loop().run_in_executor(
                self._pool, FaceModel, self.detector_path, self.model_path, self.threads
            )

    def galleries(self) -> List[Gallery]:
        return load_galleries(self.store, self.model.name)

    def offer(self, jpeg: bytes, head_yaw: Optional[float] = None) -> None:
        """A frame from the stream. Returns at once, whatever happens."""
        try:
            self.counts.offered += 1
            if jpeg == self._previous:  # an older bridge doubled its stream (0.8.0, CameraStream): one look, one vote
                self.counts.repeats += 1
                return
            self._previous = jpeg
            if self._busy:
                self.counts.dropped += 1
                return
            galleries = self.galleries()
            self._notice_forgotten({g.person_id for g in galleries})
            if not galleries:
                return  # nobody to compare with: no face is even looked for
            self._busy = True
            future = asyncio.get_running_loop().run_in_executor(self._pool, self._read, jpeg, galleries)
            future.add_done_callback(lambda done: self._take(done, head_yaw))
        except Exception as exc:  # noqa: BLE001 - never into the camera stream
            self._busy = False
            self._failed(exc)

    def _read(self, jpeg: bytes, galleries: List[Gallery]) -> FaceReading:
        started = time.monotonic()
        faces, seen = self.model.faces(jpeg)
        places = tuple((f.x, f.y) for f in seen)
        return FaceReading(faces, read_faces(seen, galleries, self.threshold), time.monotonic() - started, places)

    def _take(self, done: "asyncio.Future", head_yaw: Optional[float]) -> None:
        """A frame was read (a future's callback, so it must not raise)."""
        self._busy = False
        try:
            if done.cancelled():
                return
            reading = done.result()
            enrolled = {i.person_id for i in self.store.identities(kind=KIND, model=self.model.name)}
            self._notice_forgotten(enrolled)  # forgotten while this frame was being read: it does not count
            matches = [m for m in reading.matches if m.person_id in enrolled]
            now = self._clock()
            for x, y in reading.places:
                self._places.append((now, None if head_yaw is None else head_yaw + offsets(x, y)[0]))
            self._count(reading, matches)
            held = self.votes.update({m.person_id for m in matches})
            for match in matches:
                if match.person_id in held:
                    self._recognised(match, head_yaw)
                self._record("face", match, held)
            for person in self._held - held:
                self._noted.pop(person, None)
                if self.recorder is not None:
                    self.recorder.record_event("face_lost", person_id=person)
            self._held = held
            self._maybe_record_stats()
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)

    def faces_since(self, since: float) -> List[Optional[float]]:
        """Body-frame bearings of every face judged since ``since`` (this identifier's clock), anyone's. Kept in memory
        only, a few seconds' worth, never written: for a look-around to count people, never to say who they are."""
        return [bearing for at, bearing in self._places if at >= since]

    def _recognised(self, match: Match, head_yaw: Optional[float]) -> None:
        now = self._clock()
        last = self._noted.get(match.person_id)
        if last is not None and now - last < REFRESH:
            return
        self._noted[match.person_id] = now
        bearing = bearing_of(match, head_yaw)
        if self.world is not None:
            self.world.note(
                IDENTITY,
                "recognised",
                via="face",
                person_id=match.person_id,
                name=match.name,
                score=round(match.score, 3),
                bearing=None if bearing is None else round(bearing, 1),
                head_yaw=head_yaw,
            )
        if self.memory is not None:
            self.memory.recognised(match.person_id, match.name)

    def _notice_forgotten(self, enrolled: Set[int]) -> None:
        """Someone is no longer enrolled (forgotten by the tool or scripts/memory_admin.py): out of the votes and out of
        working memory, at once."""
        for person in self._known - enrolled:
            self.votes.forget(person)
            self._held.discard(person)
            self._noted.pop(person, None)
            if self.world is not None:
                self.world.forget_known(person)
        self._known = enrolled

    def _count(self, reading: FaceReading, matches: Sequence[Match]) -> None:
        c = self.counts
        c.read += 1
        c.with_face += bool(reading.faces)
        c.matched += bool(matches)
        c.spent.append(reading.spent)

    def _record(self, kind: str, match: Match, held: Set[int]) -> None:
        """For the review. Only about someone enrolled: scores of faces that match nobody are never kept."""
        if self.recorder is not None:
            self.recorder.record_event(
                kind,
                person_id=match.person_id,
                score=round(match.score, 3),
                width=round(match.width, 1),
                votes=self.votes.count(match.person_id),
                held=match.person_id in held,
            )

    def _maybe_record_stats(self) -> None:
        now = self._clock()
        if now - self._stats_at >= STATS_EVERY:
            self._stats_at = now
            if self.recorder is not None:
                self.recorder.record_event("face_stats", **self.status())

    def _failed(self, exc: Exception) -> None:
        self.counts.failed += 1
        if self.last_error is None:  # once: a broken model would say so every second
            logger.warning(f"Face identification: a frame failed ({type(exc).__name__})")
        self.last_error = type(exc).__name__

    def status(self) -> Dict[str, Any]:
        c = self.counts
        spent = sorted(c.spent[-600:])
        return {
            "offered": c.offered,
            "repeats": c.repeats,
            "dropped": c.dropped,
            "read": c.read,
            "with_face": c.with_face,
            "matched": c.matched,
            "failed": c.failed,
            "median_ms": round(statistics.median(spent) * 1000, 1) if spent else None,
            "max_ms": round(spent[-1] * 1000, 1) if spent else None,
        }

    def close(self) -> None:
        if self.recorder is not None and self.counts.offered:
            self.recorder.record_event("face_stats", **self.status())
        self._pool.shutdown(wait=False, cancel_futures=True)
