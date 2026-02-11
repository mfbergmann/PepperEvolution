"""
Dispatches AI tool calls to the robot bridge, with parameter validation and safety clamping.
"""

import json
from typing import Any, Dict, Optional

from loguru import logger

from ..pepper.robot import PepperRobot


class ToolExecutor:
    """Validates and executes tool calls against the robot."""

    def __init__(self, robot: PepperRobot):
        self.robot = robot
        self.logger = logger.bind(module="ToolExecutor")

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Execute a tool call. Returns a JSON string result for the AI."""
        self.logger.info(f"Executing tool: {tool_name} with {tool_input}")
        try:
            result = await self._dispatch(tool_name, tool_input)
            return json.dumps(result)
        except Exception as exc:
            self.logger.error(f"Tool execution failed: {tool_name}: {exc}")
            return json.dumps({"success": False, "error": str(exc)})

    async def _dispatch(self, name: str, inp: Dict[str, Any]) -> Dict[str, Any]:
        if name == "speak":
            text = inp.get("text", "")
            if not text:
                return {"success": False, "error": "text is required"}
            animated = inp.get("animated", False)
            ok = await self.robot.speak(text, animated=animated)
            return {"success": ok, "spoken": text}

        elif name == "move_forward":
            distance = self._clamp(inp.get("distance", 0.5), -2.0, 2.0)
            speed = self._clamp(inp.get("speed", 0.3), 0.1, 0.8)
            ok = await self.robot.move_forward(distance, speed)
            return {"success": ok, "distance": distance}

        elif name == "turn":
            angle = self._clamp(inp.get("angle", 0), -180, 180)
            ok = await self.robot.turn(angle)
            return {"success": ok, "angle": angle}

        elif name == "move_head":
            yaw = self._clamp(inp.get("yaw", 0), -119, 119)
            pitch = self._clamp(inp.get("pitch", 0), -40, 36)
            ok = await self.robot.move_head(yaw, pitch)
            return {"success": ok, "yaw": yaw, "pitch": pitch}

        elif name == "set_posture":
            posture = inp.get("posture", "Stand")
            allowed = {"Stand", "StandInit", "StandZero", "Crouch"}
            if posture not in allowed:
                return {"success": False, "error": f"Invalid posture. Must be one of: {allowed}"}
            ok = await self.robot.set_posture(posture)
            return {"success": ok, "posture": posture}

        elif name == "play_animation":
            anim_name = inp.get("name", "")
            if not anim_name:
                return {"success": False, "error": "name is required"}
            ok = await self.robot.play_animation(anim_name)
            return {"success": ok, "animation": anim_name}

        elif name == "set_eye_color":
            color = inp.get("color", "white")
            ok = await self.robot.set_eye_color(color)
            return {"success": ok, "color": color}

        elif name == "take_photo":
            camera = inp.get("camera", 0)
            result = await self.robot.take_picture(camera=camera)
            if result and result.get("image"):
                return {
                    "success": True,
                    "width": result.get("width"),
                    "height": result.get("height"),
                    "image_base64": result["image"][:100] + "...",  # Truncate for AI context
                    "note": "Photo captured successfully. Full image available to the user.",
                }
            return {"success": False, "error": "Camera returned no image"}

        elif name == "get_sensors":
            data = await self.robot.get_sensors()
            return {"success": True, **data}

        elif name == "emergency_stop":
            await self.robot.emergency_stop()
            return {"success": True, "message": "Emergency stop activated"}

        else:
            return {"success": False, "error": f"Unknown tool: {name}"}

    @staticmethod
    def _clamp(value: Any, min_val: float, max_val: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        return max(min_val, min(max_val, v))
