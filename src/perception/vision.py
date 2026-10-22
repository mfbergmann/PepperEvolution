"""
Camera judgements: small frames from the bridge (/ws/camera) go to a local decision model with images (Clef Flash)
about once a second while someone is in view, and the answers go into the world model (issue #11, #20).

With ``watch`` (WATCH_ROOM, issue #33) the stream also stays open while nobody is in view, slowly (IDLE_FPS), and a
cheap change check on every frame (src/perception/change.py) decides when to look closer: a changed view asks where
the nearest person is and brings the next scene note forward. Nothing wakes the mind; what is found goes into working
memory, where the "Around you" line and recall see it.

Only questions with fixed answers, no descriptions: is someone waving, holding something up to show Pepper, facing
the camera? A judgement that holds for two frames in a row becomes an event ("[Sensor event] Someone is waving at
you."), with a cooldown, so Claude is only woken when something happens. Frames stay in memory; with
PHOTO_RECORD_DIR set, only the frame that triggered an event is kept, for review.
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from ..world.observations import CAMERA
from .change import ChangeDetector
from .geometry import offsets
from .scene import PEOPLE_MODEL, PEOPLE_QUESTIONS
from .scene import STATE as SCENE_STATE

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    ws_connect = None  # type: ignore[assignment,misc]

MODEL = "clef-flash"
STATE = "Pepper, a humanoid robot, sees this through its forehead camera."
QUESTIONS = {
    "waving": {"type": "noul", "instructions": "Is someone waving or raising a hand towards the camera?"},
    "showing": {"type": "noul", "instructions": "Is someone holding up an object to show the camera?"},
    "facing": {"type": "noul", "instructions": "Is a person looking towards the camera?"},
}
EVENT_KINDS = ("waving", "showing")  # "facing" only informs the world model and the addressee gate
FIRE_AT = {"waving": 0.7, "showing": 0.6}  # probability that counts as "yes", per kind. On the robot (2026-10-08):
# a wave scored 0.89-0.92, an object held up 0.68-0.77 (missed at 0.7), standing still at most 0.21 waving and
# 0.35 showing
CONSECUTIVE = 2  # frames in a row before an event fires
FIND_EVERY = 1.5  # seconds between "where is the nearest person?" questions while the detector sees nobody (#24)
FIND_AT = 0.7  # how sure Clef Flash must be that a real person is in the frame (11/12 counts right, the miss at 0.53)
THIRDS = {"left": offsets(1 / 6, 0.5)[0], "centre": 0.0, "right": offsets(5 / 6, 0.5)[0]}  # degrees from the middle
COOLDOWN = 30.0  # seconds between events of the same kind
JUDGE_TIMEOUT = 1.0
STOP_AFTER_EMPTY = 5.0  # seconds with nobody in view before the stream is closed or slowed (Wi-Fi, shared GPU)
IDLE_FPS = 0.5  # watching an empty room (WATCH_ROOM): the robot's CPU and Wi-Fi carry this all the time

EventCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]


class FrameWatcher:
    def __init__(
        self,
        url: str,
        decider: Any,
        world: Any,
        on_event: EventCallback,
        fps: float = 1.0,
        api_key: str = "",
        recorder: Optional[Any] = None,
        photo_dir: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        scene: Optional[Any] = None,
        wanted: Optional[Callable[[], bool]] = None,
        watch: bool = False,
        idle_fps: float = IDLE_FPS,
    ):
        self.watch = watch  # keep a slow stream open while nobody is in view, looking for changes (#33)
        self.idle_fps = idle_fps
        self.changes = ChangeDetector()
        self.scene = scene  # ScenePass (src/perception/scene.py): an occasional scene note from the same frames
        # stream while this is true: someone in view, or (main.py) someone talking with Pepper whom the detector lost
        self._wanted = wanted or (lambda: bool(self.world.count))
        self.head: Optional[List[float]] = None  # the head's [yaw, pitch] for the latest frame (bridge 0.7)
        self.head_at: Optional[float] = None
        self._last_find: Optional[float] = None
        self._finding = False
        self.found = 0
        self.url = url
        self.decider = decider
        self.world = world
        self.on_event = on_event
        self.fps = fps
        self.api_key = api_key
        self.recorder = recorder
        self.photo_dir = photo_dir
        self._clock = clock
        self.frames = 0
        self.judged = 0
        self.dropped = 0
        self.last_seconds: Optional[float] = None
        self._busy = False
        self._streak: Dict[str, int] = {k: 0 for k in EVENT_KINDS}
        self._last_fired: Dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._judging: set = set()
        self.logger = logger.bind(module="FrameWatcher")

    # -- stream ------------------------------------------------------------------

    def start(self):
        if ws_connect is None or (self._task is not None and not self._task.done()):
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="camera-judgements")

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def _stream_url(self, fps: Optional[float] = None) -> str:
        url = f"{self.url}?fps={self.fps if fps is None else fps:g}"
        return f"{url}&api_key={self.api_key}" if self.api_key else url

    def _rate(self) -> Optional[float]:
        """Frames per second wanted now: the full rate while someone is in view (or talking), the idle rate while
        watching an empty room, or None for no stream."""
        if self._wanted():
            return self.fps
        return self.idle_fps if self.watch else None

    async def _loop(self):
        while self._running:
            rate = self._rate()
            if rate is None:
                await asyncio.sleep(0.5)  # NAOqi's people detection (or a conversation) is the trigger
                continue
            try:
                async with ws_connect(self._stream_url(rate), open_timeout=10, max_size=None) as ws:
                    why = "someone in view" if rate == self.fps and self._wanted() else "watching the room"
                    self.logger.info(f"Camera stream open at {rate:g} fps ({why})")
                    self.changes.reset()
                    empty_since: Optional[float] = None
                    async for message in ws:
                        if isinstance(message, (bytes, bytearray)):
                            self.frame(bytes(message))
                        else:
                            self._text(message)
                        wanted = self._rate()
                        if wanted == rate:
                            empty_since = None
                        elif wanted is not None and wanted > rate:
                            break  # someone appeared: reopen at the full rate at once
                        elif empty_since is None:
                            empty_since = self._clock()
                        elif self._clock() - empty_since > STOP_AFTER_EMPTY:
                            break  # nobody for a while: slow down, or close
                    self.logger.info("Camera stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - try again in a moment
                self.logger.warning(f"Camera stream: {exc}")
                await asyncio.sleep(3.0)

    # -- judgements --------------------------------------------------------------

    def frame(self, jpeg: bytes):
        """A new frame: judge it unless the previous judgement is still running (then drop it). While only watching
        an empty room, a frame is looked into only when the view changed."""
        self.frames += 1
        head_yaw = self.head[0] if self.head else None
        changed = self._changed(jpeg, head_yaw)
        active = self._wanted()
        if self.scene is not None:
            self.scene.offer(jpeg, head_yaw=head_yaw, changed=changed)
        if self.world.count == 0 and (active or changed):  # the detector reported nobody (None: no report yet)
            self._find_person(jpeg, head_yaw)
        if self.watch and not active:
            return  # only watching an empty room: the judgements are for people
        if self._busy:
            self.dropped += 1
            return
        self._busy = True
        task = asyncio.create_task(self._judge(jpeg))
        self._judging.add(task)
        task.add_done_callback(self._judging.discard)

    def head_now(self, fresh: float = 3.0) -> Optional[List[float]]:
        """The head's [yaw, pitch] if a frame reported it in the last ``fresh`` seconds (the stream is open)."""
        if self.head is None or self.head_at is None or self._clock() - self.head_at > fresh:
            return None
        return self.head

    def _changed(self, jpeg: bytes, head_yaw: Optional[float]) -> bool:
        pose = getattr(self.world, "pose", None)
        where = (pose.x, pose.y, pose.theta) if pose is not None and hasattr(pose, "theta") else None
        if not self.changes.feed(jpeg, head=self.head, pose=where):
            return False
        share = round(self.changes.last_share, 3)
        self.world.note(CAMERA, "view_changed", share=share, head_yaw=head_yaw)
        if self.recorder is not None:
            self.recorder.record_event("view_changed", share=share, head_yaw=head_yaw)
        return True

    def _text(self, message: Any):
        """A text frame: ``hello``, or (bridge 0.7) the head's angles for the frame that follows."""
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            return
        if isinstance(data, dict) and data.get("type") == "frame" and isinstance(data.get("head"), list):
            self.head = [float(v) for v in data["head"][:2]]
            self.head_at = self._clock()

    # -- the camera fallback for "come to me" (#24) ---------------------------------------------------------------

    def _find_person(self, jpeg: bytes, head_yaw: Optional[float]):
        """The people detector sees nobody, but the stream is open (someone is talking with Pepper): ask where the
        nearest person is in the frame, every FIND_EVERY seconds, one question at a time."""
        now = self._clock()
        if self._finding or (self._last_find is not None and now - self._last_find < FIND_EVERY):
            return
        self._finding = True
        self._last_find = now
        task = asyncio.create_task(self._find(jpeg, head_yaw))
        self._judging.add(task)
        task.add_done_callback(self._judging.discard)

    async def _find(self, jpeg: bytes, head_yaw: Optional[float]):
        try:
            answers = await self.decider.ask(
                PEOPLE_MODEL, SCENE_STATE, PEOPLE_QUESTIONS, images=[base64.b64encode(jpeg).decode("ascii")]
            )
            try:
                choice = answers["people"]["choice"]
                p = float(answers["people"]["probabilities"][choice])
                where = answers["where"]["choice"]
            except (KeyError, TypeError, ValueError):
                return
            if choice == "none" or where not in THIRDS or p < FIND_AT:
                return
            bearing = (head_yaw or 0.0) + THIRDS[where]
            self.found += 1
            self.world.note(
                CAMERA, "person_seen", bearing=round(bearing, 1), p=round(p, 2), where=where, head_yaw=head_yaw
            )
        finally:
            self._finding = False

    async def _judge(self, jpeg: bytes):
        try:
            started = self._clock()
            answers = await self.decider.ask(
                MODEL, STATE, QUESTIONS, images=[base64.b64encode(jpeg).decode("ascii")], timeout=JUDGE_TIMEOUT
            )
            self.last_seconds = self._clock() - started
            if not answers:
                return
            probs = {}
            for key in QUESTIONS:
                try:
                    probs[key] = float(answers[key]["noul"])
                except (KeyError, TypeError, ValueError):
                    pass
            self.judged += 1
            now = self._clock()
            self.world.update_seen(probs, now)
            if self.recorder is not None:
                self.recorder.record_event(
                    "camera", seconds=round(self.last_seconds, 3), **{k: round(v, 3) for k, v in probs.items()}
                )
            for kind in EVENT_KINDS:
                p = probs.get(kind, 0.0)
                self._streak[kind] = self._streak[kind] + 1 if p >= FIRE_AT[kind] else 0
                if self._streak[kind] < CONSECUTIVE:
                    continue
                if now - self._last_fired.get(kind, -1e9) < COOLDOWN:
                    continue
                self._last_fired[kind] = now
                self._keep(jpeg, kind)
                self.logger.info(f"Camera: {kind} (p={p:.2f})")
                await self.on_event("vision", {"what": kind, "p": p})
        finally:
            self._busy = False

    def _keep(self, jpeg: bytes, kind: str):
        if not self.photo_dir:
            return
        try:
            os.makedirs(self.photo_dir, exist_ok=True)
            name = datetime.now().strftime(f"camera_{kind}_%Y%m%d_%H%M%S.jpg")
            with open(os.path.join(self.photo_dir, name), "wb") as fh:
                fh.write(jpeg)
        except OSError as exc:
            self.logger.debug(f"Could not keep the frame: {exc}")

    def status(self) -> Dict[str, Any]:
        return {
            "frames": self.frames,
            "judged": self.judged,
            "dropped": self.dropped,
            "last_seconds": self.last_seconds,
        }
