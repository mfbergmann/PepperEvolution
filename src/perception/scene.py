"""
Scene notes (#11): every so often, one camera frame becomes a short note in working memory (docs/MEMORY.md).

Two local models, each for what it is good at (benchmarked on 12 labelled frames from the robot, 2026-10-08):

- who is where: Clef Flash's typed questions (how many real people, where the nearest one is), with probabilities.
  11 of 12 counts right in 0.14 s; the one miss came with p 0.53. A generative model asked the same counted people in
  glass reflections and on Pepper's own reflection (qwen3.5:4b 7-8 of 12, gemma4:e4b 2 of 12).
- the place: a small vision-language model (qwen3.5:4b in Ollama) writes the kind of place, up to five objects and a
  note of at most 15 words, people left out on purpose. Median 0.46 s; the notes matched the frames.

The pass takes frames the camera stream already sends (``FrameWatcher``), so it costs the robot nothing more. It
looks when someone has come into view and then every ``every`` seconds while someone stays, one look at a time,
and fails open: a model that does not answer just means no note. Frames are never stored.
"""

import asyncio
import base64
import json
import time
from typing import Any, Callable, Dict, Optional

import httpx
from loguru import logger

from ..world.observations import SCENE

PEOPLE_MODEL = "clef-flash"
PLACE_MODEL = "qwen3.5:4b"
STATE = "A camera frame from a small humanoid robot's forehead camera."
PEOPLE_QUESTIONS = {
    "people": {
        "type": "choice",
        "instructions": "How many real people are physically in the room in this camera frame? Reflections in glass "
        "(including a white robot with round glowing eyes, which is the robot itself) and people on screens do not "
        "count.",
        "criteria": {
            "none": "No real person",
            "one": "Exactly one real person",
            "two_or_more": "Two or more real people",
        },
    },
    "where": {
        "type": "choice",
        "instructions": "Where in the frame is the nearest real person (not a reflection)?",
        "criteria": {
            "left": "Left third",
            "centre": "Middle third",
            "right": "Right third",
            "nobody": "No real person",
        },
    },
}
PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "place": {"type": "string"},
        "objects": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["place", "objects", "note"],
}
PLACE_PROMPT = (
    "You are the eyes of a small humanoid robot; this is what its head camera sees. Describe only the place and the "
    "things in it, not people: the kind of place, up to five notable objects, and a note of at most 15 words about "
    "the place (for example 'a meeting room with a round orange table and striped walls'). Glass walls reflect; "
    "ignore reflections."
)
EVERY = 20.0  # seconds between looks while someone stays in view
PLACE_TIMEOUT = 3.0
COUNTS = {"none": 0, "one": 1, "two_or_more": 2}


class ScenePass:
    def __init__(
        self,
        decider: Any,
        world: Any,
        ollama_url: Optional[str] = None,
        place_model: str = PLACE_MODEL,
        every: float = EVERY,
        clock: Callable[[], float] = time.monotonic,
        transport: Optional[Any] = None,
    ):
        self.decider = decider
        self.world = world
        self.ollama_url = ollama_url.rstrip("/") if ollama_url else None
        self.place_model = place_model
        self.every = every
        self._clock = clock
        self._http = httpx.AsyncClient(timeout=PLACE_TIMEOUT, transport=transport) if self.ollama_url else None
        self._busy = False
        self._last_look: Optional[float] = None
        self._seen_count = 0
        self._tasks: set = set()
        self.looks = 0
        self.logger = logger.bind(module="ScenePass")

    def offer(self, jpeg: bytes, head_yaw: Optional[float] = None):
        """A frame from the camera stream: look at it if a look is due (someone new, or ``every`` has passed)."""
        if self._busy:
            return
        now = self._clock()
        count = self.world.count or 0
        new_people = count > self._seen_count
        self._seen_count = count
        due = self._last_look is None or now - self._last_look >= self.every or new_people
        if not due:
            return
        self._busy = True
        self._last_look = now
        task = asyncio.create_task(self.look(jpeg, head_yaw))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def look(self, jpeg: bytes, head_yaw: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One scene note from one frame, into working memory as a ``scene`` observation. Never raises."""
        try:
            image = base64.b64encode(jpeg).decode("ascii")
            people, place = await asyncio.gather(self._people(image), self._place(image))
            if people is None and place is None:
                return None
            data: Dict[str, Any] = {}
            if people is not None:
                data.update(people)
            if place is not None:
                data.update(place)
            if head_yaw is not None:
                data["head_yaw"] = round(head_yaw, 1)
            self.world.note(SCENE, "scene", **data)
            self.looks += 1
            return data
        except Exception as exc:  # noqa: BLE001 - a missed look must never break perception
            self.logger.debug(f"scene look failed: {exc}")
            return None
        finally:
            self._busy = False

    async def _people(self, image: str) -> Optional[Dict[str, Any]]:
        answers = await self.decider.ask(PEOPLE_MODEL, STATE, PEOPLE_QUESTIONS, images=[image])
        try:
            choice = answers["people"]["choice"]
            where = answers["where"]["choice"]
            return {
                "people": COUNTS[choice],
                "people_p": round(float(answers["people"]["probabilities"][choice]), 2),
                "where": None if where == "nobody" else where,
            }
        except (KeyError, TypeError, ValueError):
            return None

    async def _place(self, image: str) -> Optional[Dict[str, Any]]:
        if self._http is None:
            return None
        try:
            response = await self._http.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.place_model,
                    "stream": False,
                    "think": False,
                    "format": PLACE_SCHEMA,
                    "keep_alive": "30m",
                    "options": {"temperature": 0, "num_predict": 200},
                    "messages": [{"role": "user", "content": PLACE_PROMPT, "images": [image]}],
                },
            )
            response.raise_for_status()
            content = json.loads(response.json()["message"]["content"])
            return {
                "place": str(content.get("place", ""))[:80],
                "objects": [str(o)[:40] for o in content.get("objects", [])][:5],
                "note": str(content.get("note", ""))[:160],
            }
        except Exception as exc:  # noqa: BLE001 - fail open: no place note this time
            self.logger.debug(f"place note failed: {exc}")
            return None

    async def close(self):
        for task in list(self._tasks):
            task.cancel()
        if self._http is not None:
            await self._http.aclose()
