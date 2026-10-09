"""
Camera judgements: small frames from the bridge (/ws/camera) go to a local decision model with images (Clef Flash)
about once a second while someone is in view, and the answers go into the world model (issue #11, #20).

Only questions with fixed answers, no descriptions: is someone waving, holding something up to show Pepper, facing
the camera? A judgement that holds for two frames in a row becomes an event ("[Sensor event] Someone is waving at
you."), with a cooldown, so Claude is only woken when something happens. Frames stay in memory; with
PHOTO_RECORD_DIR set, only the frame that triggered an event is kept, for review.
"""

import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger

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
COOLDOWN = 30.0  # seconds between events of the same kind
JUDGE_TIMEOUT = 1.0
STOP_AFTER_EMPTY = 5.0  # seconds with nobody in view before the stream is closed (Wi-Fi, shared GPU)

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
    ):
        self.scene = scene  # ScenePass (src/perception/scene.py): an occasional scene note from the same frames
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

    def _stream_url(self) -> str:
        url = f"{self.url}?fps={self.fps:g}"
        return f"{url}&api_key={self.api_key}" if self.api_key else url

    async def _loop(self):
        while self._running:
            if not self.world.count:
                await asyncio.sleep(0.5)  # NAOqi's people detection is the always-on trigger
                continue
            try:
                async with ws_connect(self._stream_url(), open_timeout=10, max_size=None) as ws:
                    self.logger.info("Camera stream open (someone in view)")
                    empty_since: Optional[float] = None
                    async for message in ws:
                        if isinstance(message, (bytes, bytearray)):
                            self.frame(bytes(message))
                        if self.world.count:
                            empty_since = None
                        elif empty_since is None:
                            empty_since = self._clock()
                        elif self._clock() - empty_since > STOP_AFTER_EMPTY:
                            break
                    self.logger.info("Camera stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - try again in a moment
                self.logger.warning(f"Camera stream: {exc}")
                await asyncio.sleep(3.0)

    # -- judgements --------------------------------------------------------------

    def frame(self, jpeg: bytes):
        """A new frame: judge it unless the previous judgement is still running (then drop it)."""
        self.frames += 1
        if self.scene is not None:
            self.scene.offer(jpeg)
        if self._busy:
            self.dropped += 1
            return
        self._busy = True
        task = asyncio.create_task(self._judge(jpeg))
        self._judging.add(task)
        task.add_done_callback(self._judging.discard)

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
