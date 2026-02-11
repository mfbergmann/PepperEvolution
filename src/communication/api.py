"""
FastAPI REST server for PepperEvolution.

Simplified routes that work with the bridge-based architecture:
- POST /chat — AI conversation with tool calling
- GET /status — robot status
- POST /command/{cmd} — direct robot commands
- GET /tools — list available AI tools
"""

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

from loguru import logger

from ..ai import AIManager, TOOLS
from ..pepper import PepperRobot


class ChatRequest(BaseModel):
    message: str


class CommandParams(BaseModel):
    params: Dict[str, Any] = {}


class APIServer:
    """REST API server for PepperEvolution."""

    def __init__(self, host: str, port: int, ai_manager: AIManager, robot: PepperRobot):
        self.host = host
        self.port = port
        self.ai_manager = ai_manager
        self.robot = robot
        self.logger = logger.bind(module="APIServer")
        self.server: Optional[uvicorn.Server] = None

        self.app = FastAPI(
            title="PepperEvolution API",
            description="Cloud AI control system for Pepper robot",
            version="2.0.0",
        )

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._setup_routes()

    def _setup_routes(self):

        @self.app.get("/")
        async def root():
            return {"name": "PepperEvolution", "version": "2.0.0", "status": "running"}

        @self.app.get("/health")
        async def health():
            try:
                h = await self.robot.connection.health_check()
                return {"status": "healthy", "bridge": h}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.get("/status")
        async def status():
            try:
                state = self.robot.get_state()
                sensors = await self.robot.get_sensors()
                return {
                    "robot_state": {
                        "battery_level": state.battery_level,
                        "posture": state.posture,
                        "robot_name": state.robot_name,
                        "autonomous_life": state.autonomous_life,
                        "is_connected": state.is_connected,
                    },
                    "sensors": sensors,
                }
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.post("/chat")
        async def chat(request: ChatRequest):
            try:
                result = await self.ai_manager.process_user_input(request.message)
                return result
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.post("/command/{cmd}")
        async def command(cmd: str, body: CommandParams):
            try:
                result = await self._execute_command(cmd, body.params)
                return result
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.get("/tools")
        async def list_tools():
            return {"tools": TOOLS}

        @self.app.get("/conversation/history")
        async def get_history():
            return {"history": self.ai_manager.get_conversation_history()}

        @self.app.delete("/conversation/history")
        async def clear_history():
            self.ai_manager.clear_conversation_history()
            return {"success": True}

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            try:
                while True:
                    raw = await websocket.receive_text()
                    data = json.loads(raw)
                    msg_type = data.get("type", "")

                    if msg_type == "chat":
                        message = data.get("message", "")
                        if message:
                            result = await self.ai_manager.process_user_input(message)
                            await websocket.send_json({"type": "chat_response", **result})
                    elif msg_type == "status_request":
                        state = self.robot.get_state()
                        await websocket.send_json({
                            "type": "status_response",
                            "robot_state": {
                                "battery_level": state.battery_level,
                                "posture": state.posture,
                                "is_connected": state.is_connected,
                            },
                        })
                    else:
                        await websocket.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})
            except WebSocketDisconnect:
                self.logger.info("WebSocket client disconnected")
            except Exception as exc:
                self.logger.error(f"WebSocket error: {exc}")

    async def _execute_command(self, cmd: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a direct robot command."""
        bridge = self.robot.connection.bridge

        dispatch = {
            "speak": lambda: bridge.speak(params.get("text", ""), language=params.get("language")),
            "move_forward": lambda: bridge.move_forward(params.get("distance", 0.5)),
            "turn": lambda: bridge.move_turn(params.get("angle", 90)),
            "move_head": lambda: bridge.move_head(params.get("yaw", 0), params.get("pitch", 0)),
            "posture": lambda: bridge.set_posture(params.get("posture", "Stand")),
            "wake_up": lambda: bridge.wake_up(),
            "rest": lambda: bridge.rest(),
            "stop": lambda: bridge.stop(),
            "emergency_stop": lambda: bridge.emergency_stop(),
            "photo": lambda: bridge.take_picture(camera=params.get("camera", 0)),
            "sensors": lambda: bridge.get_sensors(),
            "eye_color": lambda: bridge.set_eye_leds(color=params.get("color", "white")),
            "chest_color": lambda: bridge.set_chest_leds(color=params.get("color", "white")),
            "animation": lambda: bridge.play_animation(params.get("name", "")),
            "volume": lambda: bridge.set_volume(params.get("level", 50)),
            "awareness": lambda: bridge.set_awareness(params.get("enabled", True)),
        }

        handler = dispatch.get(cmd)
        if not handler:
            return {"success": False, "error": f"Unknown command: {cmd}"}

        result = await handler()
        return {"success": True, "command": cmd, "result": result}

    async def start(self):
        self.logger.info(f"Starting API server on {self.host}:{self.port}")
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    async def stop(self):
        if self.server:
            self.server.should_exit = True
            self.logger.info("API server stopped")
