"""
The navigator (#36): Pepper's saved map, the places named on it, and getting from one to another.

The map itself is NAOqi's (ALNavigation's exploration, through the bridge's ``/map`` endpoints): one exploration
makes one map around the point where it started, saved as a ``.explo`` file on the robot. The host keeps, in the
long-term store, which map is active, the places named on it (map coordinates) and where Pepper last was in it; at
start-up the active map is loaded and Pepper relocalizes around that last position.

While localized, the position is read every few seconds and handed to the world model as a ``map`` observation
with the place it falls in, so the "Where you are" line and the timeline follow Pepper from room to room.
Exploring is for an operator only (the web UI); the mind can go to a place, name the place it is at, forget a place,
and say where it is when someone tells it.
"""

import asyncio
import io
import math
import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from ..world.observations import MAP, MAP_FRAME, Observation
from ..world.places import FRESH, UNSURE, distance_to, nearest, place_at, place_line

POLL = 5.0  # seconds between position reads while localized
PLACE_RADIUS = 1.5  # metres: Pepper is "at" a place this close to the point it was named at
SAVE_MOVED = (0.2, 10.0)  # metres, degrees moved before the last position is saved again
TOO_UNSURE = 2 * UNSURE  # metres: less sure than this, Pepper does not drive by the map
DRAW_SCALE = 4  # screen pixels per map pixel in the drawn map


class Navigator:
    def __init__(self, robot: Any, world: Any, store: Any, clock: Callable[[], float] = time.monotonic):
        self.robot = robot
        self.world = world
        self.store = store
        self.clock = clock
        self.available: Optional[bool] = None  # the robot's NAOqi has the exploration API (None: not asked yet)
        self.localizing = False
        self.exploring = False
        self.last_error: Optional[str] = None
        self._saved: Optional[Dict[str, float]] = None  # the last position written to the store
        self._task: Optional[asyncio.Task] = None
        self.logger = logger.bind(module="Navigator")

    @property
    def bridge(self):
        return self.robot.bridge

    @property
    def map(self):
        return self.store.active_map()

    def places(self) -> List[Any]:
        record = self.map
        return self.store.places(record.id) if record is not None else []

    # -- start-up and the position loop ----------------------------------------------------------------------------

    async def start(self):
        try:
            status = await self.bridge.map_status()
        except Exception as exc:  # noqa: BLE001 - an older bridge has no /map
            self.available = False
            self.last_error = str(exc)
            self.logger.warning(f"Map not available (bridge 0.8 or later needed): {exc}")
            return
        self.available = bool(status.get("available"))
        if not self.available:
            self.logger.warning("Map: this NAOqi has no ALNavigation exploration (Pepper only)")
            return
        record = self.map
        if record is None:
            self.logger.info("Map on, but no map yet: explore from the web UI (with someone watching)")
        elif status.get("loaded") == record.path and status.get("localizing"):
            self.localizing = True  # the bridge kept it (only the host restarted)
        else:
            await self.use_map(record)
        self._task = asyncio.ensure_future(self._poll_loop())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        position = self.position()
        record = self.map
        if position is not None and record is not None:
            self.store.save_map_pose(record.id, position["x"], position["y"], position["theta"])

    async def use_map(self, record: Any, guess: Optional[List[float]] = None) -> Dict[str, Any]:
        """Load a map on the robot and localize in it, around ``guess`` or where Pepper last was (else its
        origin, where exploring started)."""
        loaded = await self.bridge.map_load(record.path)
        if not loaded.get("loaded"):
            self.localizing = False
            self.last_error = f"the robot could not load {record.path}"
            self.logger.warning(f"Map: {self.last_error}")
            return {"ok": False, "error": self.last_error}
        if guess is None:
            guess = list(record.last_pose) if record.last_pose is not None else [0.0, 0.0, 0.0]
        return await self._localize(guess)

    async def _localize(self, guess: Optional[List[float]]) -> Dict[str, Any]:
        result = await self.bridge.map_localize(True, guess=guess)
        self.localizing = bool(result.get("localizing"))
        self._observe(result.get("position"))
        self.logger.info(f"Map: localized around {guess}: {result.get('position')}")
        return {"ok": self.localizing, "position": self.position()}

    async def _poll_loop(self):
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep reading; the bridge may be restarting
                self.last_error = str(exc)
                self.logger.debug(f"map position read failed: {exc}")
            await asyncio.sleep(POLL)

    async def refresh(self) -> Optional[Dict[str, Any]]:
        """Read where Pepper is on the map and tell the world model."""
        status = await self.bridge.map_status()
        self.exploring = bool(status.get("exploring"))
        record = self.map
        localized = bool(status.get("localizing")) and record is not None and status.get("loaded") == record.path
        if not localized:
            if self.localizing and not self.exploring:
                self.logger.info("Map: no longer localized")
                self.world.observe(Observation(MAP, "lost", self.clock(), {}, frame=MAP_FRAME))
            self.localizing = False
            return None
        self.localizing = True
        self._observe(status.get("position"))
        return self.position()

    def _observe(self, position: Optional[Dict[str, Any]]):
        if not position or position.get("x") is None:
            return
        record = self.map
        places = self.places()
        here = place_at(float(position["x"]), float(position["y"]), places)
        data = {
            "x": float(position["x"]),
            "y": float(position["y"]),
            "theta": float(position.get("theta") or 0.0),
            "uncertainty": position.get("uncertainty"),
            "place": here.name if here is not None else None,
            "map": record.id if record is not None else None,
        }
        self.world.observe(Observation(MAP, "position", self.clock(), data, frame=MAP_FRAME))
        if record is not None and self._moved_since_saved(data):
            self.store.save_map_pose(record.id, data["x"], data["y"], data["theta"])
            self._saved = data

    def _moved_since_saved(self, data: Dict[str, Any]) -> bool:
        if self._saved is None:
            return True
        metres = math.hypot(data["x"] - self._saved["x"], data["y"] - self._saved["y"])
        degrees = abs((data["theta"] - self._saved["theta"] + 180.0) % 360.0 - 180.0)
        return metres > SAVE_MOVED[0] or degrees > SAVE_MOVED[1]

    def position(self) -> Optional[Dict[str, Any]]:
        """The latest map position (with its place) while it is fresh, else None."""
        latest = self.world.map_position
        if latest is None or not self.localizing or self.clock() - latest[0] > FRESH:
            return None
        return dict(latest[1])

    # -- what the mind and the operator can do ---------------------------------------------------------------------

    async def explore(self, radius: float = 3.0) -> Dict[str, Any]:
        """Operator only: Pepper drives around by itself to make a new map, which becomes the active one."""
        if getattr(self.robot, "halted", False):
            return {"ok": False, "error": "Pepper is halted by an emergency stop; wake it first"}
        self.exploring = True
        try:
            result = await self.robot.explore_map(radius)
        finally:
            self.exploring = False
        if not result.get("completed") or not result.get("path"):
            return {"ok": False, "error": f"exploration ended: {result.get('status', 'unknown')}", "result": result}
        record = self.store.add_map(str(result["path"]), radius=result.get("radius", radius))
        self.localizing = True
        self._saved = None
        self._observe(result.get("position"))
        self.logger.info(f"Map: new map {record.path} (radius {record.radius} m)")
        return {"ok": True, "map": record.id, "path": record.path, "position": self.position()}

    def remember_place(self, name: str, radius: float = PLACE_RADIUS) -> Dict[str, Any]:
        """Name the place Pepper is at now (its position and heading on the map)."""
        name = " ".join(str(name or "").split())
        record = self.map
        position = self.position()
        if not name:
            return {"ok": False, "error": "A place needs a name."}
        if record is None:
            return {"ok": False, "error": "You have no map yet; someone has to let you explore first."}
        if position is None:
            return {"ok": False, "error": "You do not know where you are on your map right now."}
        if max(position.get("uncertainty") or [0.0]) > TOO_UNSURE:
            return {"ok": False, "error": "You are not sure enough where you are on your map to name this place."}
        place = self.store.set_place(record.id, name, position["x"], position["y"], position["theta"], radius)
        self._observe(position)  # the place line knows it at once
        return {"ok": True, "place": place.name, "x": round(place.x, 2), "y": round(place.y, 2)}

    def forget_place(self, name: str) -> Dict[str, Any]:
        record = self.map
        if record is None or not self.store.delete_place(record.id, name):
            return {"ok": False, "error": f"You know no place called {name!r}.", "places": self._names()}
        return {"ok": True, "forgotten": name}

    async def i_am_at(self, name: str) -> Dict[str, Any]:
        """Someone says where Pepper is: relocalize there (no movement). Facing is unknown, so the place's
        heading is the guess and localization corrects it."""
        record = self.map
        place = self.store.find_place(record.id, name) if record is not None else None
        if place is None:
            return {"ok": False, "error": f"You know no place called {name!r}.", "places": self._names()}
        result = await self._localize([place.x, place.y, place.theta or 0.0])
        return {**result, "place": place.name}

    async def go_to(self, name: str) -> Dict[str, Any]:
        """Drive to a named place, around obstacles, and face the way Pepper faced when it was named."""
        record = self.map
        place = self.store.find_place(record.id, name) if record is not None else None
        if place is None:
            return {"ok": False, "error": f"You know no place called {name!r}.", "places": self._names()}
        if getattr(self.robot, "halted", False):
            return {"ok": False, "error": "Halted by an emergency stop; nothing moves until you are woken up."}
        if self.exploring:
            return {"ok": False, "error": "You are exploring; wait until it is done."}
        await self.refresh()
        if not self.localizing and record.last_pose is not None:
            await self._localize(list(record.last_pose))  # e.g. after an emergency stop ended localization
        position = self.position()
        if position is None:
            return {"ok": False, "error": "You do not know where you are on your map; ask where you are."}
        if max(position.get("uncertainty") or [0.0]) > TOO_UNSURE:
            return {"ok": False, "error": "You are not sure where you are on your map; ask where you are first."}
        if position.get("place") == place.name and distance_to(place, position["x"], position["y"]) < 0.5:
            return {"ok": True, "place": place.name, "already_there": True}
        result = await self.robot.go_to_map_point(place.x, place.y, place.theta, place=place.name)
        await self.refresh()
        now = self.position()
        out: Dict[str, Any] = {"ok": bool(result.get("completed")), "place": place.name}
        if now is not None:
            out["distance_left"] = round(distance_to(place, now["x"], now["y"]), 1)
            out["now_at"] = now.get("place")
        if not out["ok"]:
            out["error"] = {
                "cancelled": "Stopped on the way.",
                "target_out_of_map": "That place is off your map.",
            }.get(str(result.get("status")), "Could not get all the way there (blocked or no path).")
        return out

    def _names(self) -> List[str]:
        return [p.name for p in self.places()]

    # -- readers ---------------------------------------------------------------------------------------------------

    def place_line(self) -> str:
        """The every-turn line (empty while there is no map)."""
        if self.map is None or not self.available:
            return ""
        return place_line(self.world.map_position if self.localizing else None, self.places(), self.clock())

    def status(self) -> Dict[str, Any]:
        record = self.map
        position = self.position()
        close = nearest(position["x"], position["y"], self.places()) if position else None
        return {
            "available": self.available,
            "localizing": self.localizing,
            "exploring": self.exploring,
            "map": None if record is None else {"id": record.id, "path": record.path, "radius": record.radius},
            "maps": [{"id": m.id, "path": m.path, "active": m.active} for m in self.store.maps()],
            "position": position,
            "nearest": close.name if close is not None else None,
            "places": [
                {"name": p.name, "x": round(p.x, 2), "y": round(p.y, 2), "theta": p.theta, "radius": p.radius}
                for p in self.places()
            ],
            "last_error": self.last_error,
        }

    async def image_png(self) -> bytes:
        """The map as a PNG: free space light, obstacles dark, the places as circles, Pepper as an arrow. Map y
        points up (the robot's grid rows are drawn bottom to top)."""
        from PIL import Image, ImageDraw  # Pillow is in requirements

        import base64

        grid = await self.bridge.map_image()
        width, height, res = int(grid["width"]), int(grid["height"]), float(grid["resolution"])
        ox, oy = float(grid["origin"][0]), float(grid["origin"][1])
        values = base64.b64decode(grid["data"])
        image = Image.frombytes("L", (width, height), bytes(min(255, round(v * 2.55)) for v in values))
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).resize(
            (width * DRAW_SCALE, height * DRAW_SCALE), Image.Resampling.NEAREST
        )
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image)

        def pixel(x: float, y: float):
            return ((x - ox) / res * DRAW_SCALE, (height - (y - oy) / res) * DRAW_SCALE)

        for place in self.places():
            cx, cy = pixel(place.x, place.y)
            r = place.radius / res * DRAW_SCALE
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(40, 120, 220), width=2)
            draw.text((cx + 4, cy + 4), place.name, fill=(20, 80, 180))
        position = self.position()
        if position is not None:
            cx, cy = pixel(position["x"], position["y"])
            a = math.radians(position["theta"])
            tip = (cx + 14 * math.cos(a), cy - 14 * math.sin(a))
            draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(220, 60, 40))
            draw.line((cx, cy) + tip, fill=(220, 60, 40), width=3)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
