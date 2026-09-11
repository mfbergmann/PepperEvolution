"""
Dispatches AI tool calls to the robot, with parameter validation and safety clamping.

Every call returns a :class:`ToolOutcome` which knows how to render itself as
an Anthropic ``tool_result`` block - including an image block for photos, so
the model can actually see what the camera saw.
"""

import difflib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

from ..pepper.robot import PepperRobot, Photo
from .tools import EYE_COLORS, KNOWN_ANIMATIONS, POSTURES


@dataclass
class ToolOutcome:
    """Result of executing one tool call."""

    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    image: Optional[Photo] = None

    @classmethod
    def failure(cls, error: str, **extra: Any) -> "ToolOutcome":
        return cls(ok=False, data={"error": error, **extra})

    def summary(self) -> str:
        """Compact JSON for the model and the UI."""
        return json.dumps({"success": self.ok, **self.data}, ensure_ascii=False)

    def tool_result(self, tool_use_id: str) -> Dict[str, Any]:
        """Render as an Anthropic tool_result content block."""
        content: Any = self.summary()
        if self.image is not None:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": self.image.media_type,
                        "data": self.image.base64_data,
                    },
                },
                {"type": "text", "text": self.summary()},
            ]
        result: Dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if not self.ok:
            result["is_error"] = True
        return result


class ToolExecutor:
    """Validates and executes tool calls against the robot."""

    def __init__(self, robot: PepperRobot):
        self.robot = robot
        self.logger = logger.bind(module="ToolExecutor")

    @staticmethod
    def halted_outcome() -> ToolOutcome:
        return ToolOutcome.failure("Robot is halted by emergency stop; nothing runs until it is woken up.")

    @staticmethod
    def aborted_outcome() -> ToolOutcome:
        return ToolOutcome.failure("Not executed: the person said stop.")

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> ToolOutcome:
        """Execute a tool call; never raises."""
        self.logger.info(f"Tool call: {tool_name}({json.dumps(tool_input, ensure_ascii=False)})")
        try:
            outcome = await self._dispatch(tool_name, tool_input or {})
        except Exception as exc:  # noqa: BLE001 - report to the model instead of crashing the turn
            self.logger.error(f"Tool {tool_name} failed: {exc}")
            outcome = ToolOutcome.failure(str(exc))
        level = "info" if outcome.ok else "warning"
        getattr(self.logger, level)(f"Tool {tool_name} -> {outcome.summary()[:200]}")
        return outcome

    async def _dispatch(self, name: str, inp: Dict[str, Any]) -> ToolOutcome:
        if name == "speak":
            text = str(inp.get("text", "")).strip()
            if not text:
                return ToolOutcome.failure("text is required")
            animated = bool(inp.get("animated", True))
            language = inp.get("language") or None
            result = await self.robot.speak(text, language=language, animated=animated)
            return ToolOutcome(True, {"spoken": text, "duration": result.get("duration")})

        if name == "play_animation":
            anim_name, error = self._resolve_animation(str(inp.get("name", "")).strip())
            if error:
                return ToolOutcome.failure(error)
            await self.robot.play_animation(anim_name)
            return ToolOutcome(True, {"animation": anim_name, "meaning": KNOWN_ANIMATIONS.get(anim_name)})

        if name == "move_head":
            yaw = self._clamp(inp.get("yaw", 0), -119, 119)
            pitch = self._clamp(inp.get("pitch", 0), -40, 25)
            await self.robot.move_head(yaw, pitch)
            return ToolOutcome(True, {"yaw": yaw, "pitch": pitch})

        if name == "turn":
            angle = self._clamp(inp.get("angle", 0), -180, 180)
            result = await self.robot.turn(angle)
            if result.get("completed", True) is False:
                return ToolOutcome.failure(
                    "Turn was interrupted (obstacle, collision avoidance or stop); the robot may not have turned "
                    "the full angle.",
                    angle=angle,
                    completed=False,
                )
            return ToolOutcome(True, {"angle": angle})

        if name == "move_forward":
            distance = self._clamp(inp.get("distance", 0.5), -2.0, 2.0)
            speed = self._clamp(inp.get("speed", 0.3), 0.1, 0.5)
            blocked = await self._obstacle_in_the_way(distance)
            if blocked:
                return ToolOutcome.failure(blocked)
            result = await self.robot.move_forward(distance, speed)
            if result.get("completed", True) is False:
                return ToolOutcome.failure(
                    "Move was interrupted (obstacle, collision avoidance or stop); the actual distance travelled "
                    "is unknown.",
                    distance=distance,
                    completed=False,
                )
            return ToolOutcome(True, {"distance": distance, "speed": speed})

        if name == "set_posture":
            posture = str(inp.get("posture", "Stand"))
            if posture not in POSTURES:
                return ToolOutcome.failure(f"Invalid posture {posture!r}. Must be one of: {', '.join(POSTURES)}")
            result = await self.robot.set_posture(posture)
            return ToolOutcome(True, {"posture": posture, "reached": result.get("reached", True)})

        if name == "set_eye_color":
            color = str(inp.get("color", "white")).lower()
            if color not in EYE_COLORS:
                return ToolOutcome.failure(f"Unknown color {color!r}. Must be one of: {', '.join(EYE_COLORS)}")
            await self.robot.set_eye_color(color)
            return ToolOutcome(True, {"color": color})

        if name == "take_photo":
            camera = 1 if str(inp.get("camera", 0)) == "1" else 0
            photo = await self.robot.take_picture(camera=camera)
            return ToolOutcome(
                True,
                {
                    "width": photo.width,
                    "height": photo.height,
                    "camera": "forehead" if camera == 0 else "mouth",
                    "note": "The photo is attached above; describe what you actually see in it.",
                },
                image=photo,
            )

        if name == "get_sensors":
            data = await self.robot.get_sensors()
            if "error" in data:
                return ToolOutcome.failure(data["error"])
            return ToolOutcome(True, data)

        if name == "show_on_tablet":
            url = str(inp.get("url", "")).strip()
            text = str(inp.get("text", "")).strip()
            if url:
                await self.robot.tablet_web(url)
                return ToolOutcome(True, {"shown": "web", "url": url})
            if text:
                await self.robot.tablet_text(text, title=inp.get("title") or None)
                return ToolOutcome(True, {"shown": "text", "text": text})
            return ToolOutcome.failure("Give either text or url")

        if name == "emergency_stop":
            await self.robot.emergency_stop()
            return ToolOutcome(True, {"message": "Emergency stop activated; motors relaxed"})

        return ToolOutcome.failure(f"Unknown tool: {name}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_animation(self, requested: str):
        """Map a requested animation to one that exists on the robot."""
        if not requested:
            return None, "name is required"
        installed = list(self.robot.animations or [])
        candidates = installed or list(KNOWN_ANIMATIONS)
        if requested in candidates:
            return requested, None
        lowered = requested.lower()
        for cand in candidates:  # allow 'Hey_1' or 'gestures/hey_1'
            if cand.lower() == lowered or cand.lower().endswith("/" + lowered):
                return cand, None
        tail = requested.rsplit("/", 1)[-1]
        close = difflib.get_close_matches(tail, [c.rsplit("/", 1)[-1] for c in candidates], n=3, cutoff=0.6)
        suggestions = [c for c in candidates if c.rsplit("/", 1)[-1] in close][:3]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return None, f"Animation {requested!r} is not installed on this robot.{hint}"

    async def _obstacle_in_the_way(self, distance: float) -> Optional[str]:
        """Refuse to drive into something the sonar can already see."""
        if abs(distance) < 0.05:
            return None
        try:
            sensors = await self.robot.get_sensors()
        except Exception:  # noqa: BLE001 - sensors are advisory
            return None
        sonar = sensors.get("sonar") or {}
        side = "front" if distance > 0 else "back"
        reading = sonar.get(side)
        if reading is None and abs(distance) > 0.5:
            return f"Not moving that far: no {side} sonar reading is available; try 0.5 m or less."
        if isinstance(reading, (int, float)) and reading < 0.45:
            return f"Not moving: {side} sonar shows an obstacle at {reading:.2f} m."
        return None

    @staticmethod
    def _clamp(value: Any, min_val: float, max_val: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        return max(min_val, min(max_val, v))
