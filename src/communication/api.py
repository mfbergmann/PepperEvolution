"""
FastAPI server for PepperEvolution: REST API, live WebSocket and the web UI.

One process, one port:
- GET  /                    web control panel (web/index.html)
- GET  /health, /status     bridge + robot state
- POST /chat                AI conversation with tool calling
- POST /command/{cmd}       direct robot commands (no AI)
- GET  /tools               AI tool definitions
- GET  /photo/latest        last photo taken (image/jpeg)
- POST /photo               take a photo now
- GET/DELETE /conversation/history
- WS   /ws                  chat + live robot events (see WebSocketHub)
"""

import asyncio
import base64
import contextlib
import json
import signal
from pathlib import Path
from typing import Any, Dict, Optional, Set

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from loguru import logger
from pydantic import BaseModel

from ..ai import TOOLS, AIManager
from ..pepper import PepperRobot

DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class ChatRequest(BaseModel):
    message: str
    speak: Optional[bool] = None
    client_id: Optional[str] = None  # echoed in the WebSocket broadcast so the sender can match its reply


class CommandParams(BaseModel):
    params: Dict[str, Any] = {}


class WebSocketHub:
    """Tracks browser WebSocket clients and fans out events to them."""

    def __init__(self):
        self.clients: Set[WebSocket] = set()
        self.logger = logger.bind(module="WebSocketHub")

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        await self.send(ws, {"type": "welcome", "clients": len(self.clients)})

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    SEND_TIMEOUT = 2.0  # a stalled browser must not hold up robot events for everyone else

    async def send(self, ws: WebSocket, data: Dict[str, Any]):
        try:
            await asyncio.wait_for(ws.send_text(json.dumps(data)), self.SEND_TIMEOUT)
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, data: Dict[str, Any], exclude: Optional[Set[WebSocket]] = None):
        msg = json.dumps(data)
        for ws in list(self.clients):
            if exclude and ws in exclude:
                continue
            try:
                await asyncio.wait_for(ws.send_text(msg), self.SEND_TIMEOUT)
            except Exception:
                self.disconnect(ws)

    # Callbacks wired to the robot and the AI manager
    async def on_robot_event(self, event_type: str, data: Dict[str, Any]):
        await self.broadcast({"type": "robot_event", "event": event_type, "data": data})

    async def on_partial(self, sentence: str):
        await self.broadcast({"type": "chat_partial", "text": sentence})

    async def on_response(self, result: Dict[str, Any]):
        await self.broadcast({"type": "chat_response", **result})


def _state_payload(robot: PepperRobot) -> Dict[str, Any]:
    return robot.get_state().as_dict()


async def execute_command(robot: PepperRobot, cmd: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a direct robot command (bypasses the AI)."""
    bridge = robot.bridge
    p = params or {}

    async def photo():
        resolution = p.get("resolution")
        shot = await robot.take_picture(
            camera=int(p.get("camera", 0)), resolution=int(resolution) if resolution is not None else None
        )
        return {"media_type": shot.media_type, "base64": shot.base64_data, "width": shot.width, "height": shot.height}

    dispatch = {
        "speak": lambda: bridge.speak(
            p.get("text", ""), language=p.get("language"), animated=bool(p.get("animated", True))
        ),
        "stop_speaking": lambda: bridge.stop_speaking(),
        "move_forward": lambda: bridge.move_forward(float(p.get("distance", 0.5)), float(p.get("speed", 0.3))),
        "turn": lambda: bridge.move_turn(float(p.get("angle", 90))),
        "move_head": lambda: bridge.move_head(float(p.get("yaw", 0)), float(p.get("pitch", 0))),
        "posture": lambda: bridge.set_posture(p.get("posture", "Stand")),
        "wake_up": lambda: bridge.wake_up(),
        "rest": lambda: bridge.rest(),
        "prepare": lambda: bridge.prepare(
            autonomous_life=p.get("autonomous_life", "disabled"),
            wake_up=bool(p.get("wake_up", True)),
            posture=p.get("posture"),
            awareness=p.get("awareness"),
        ),
        "autonomous_life": lambda: bridge.set_autonomous_life(p.get("state", "solitary")),
        "awareness": lambda: bridge.set_awareness(bool(p.get("enabled", True))),
        "stop": lambda: bridge.stop(),
        "emergency_stop": lambda: bridge.emergency_stop(),
        "photo": photo,
        "sensors": lambda: bridge.get_sensors(),
        "eye_color": lambda: bridge.set_eye_leds(color=p.get("color", "white")),
        "chest_color": lambda: bridge.set_chest_leds(color=p.get("color", "white")),
        "animation": lambda: bridge.play_animation(p.get("name", "")),
        "animations": lambda: bridge.list_animations(),
        "volume": lambda: bridge.set_volume(int(p.get("level", 50))),
        "tablet_text": lambda: bridge.tablet_text(p.get("text", ""), title=p.get("title")),
        "tablet_web": lambda: bridge.tablet_web(p.get("url", "")),
        "tablet_hide": lambda: bridge.tablet_hide(),
        "refresh_state": lambda: robot.refresh_state(),
    }

    handler = dispatch.get(cmd)
    if not handler:
        return {"success": False, "error": f"Unknown command: {cmd}", "known": sorted(dispatch)}
    robot.direct_commands_running += 1  # sensor reactions wait while an operator drives the robot
    try:
        result = await handler()
    except Exception as exc:  # noqa: BLE001 - surface bridge errors to the caller
        return {"success": False, "command": cmd, "error": str(exc)}
    finally:
        robot.direct_commands_running -= 1
    if hasattr(result, "as_dict"):
        result = result.as_dict()
    response: Dict[str, Any] = {"success": True, "command": cmd, "result": result}
    if isinstance(result, dict) and result.get("errors"):
        response["warnings"] = list(result["errors"])
    return response


def create_app(ai_manager: AIManager, robot: PepperRobot, web_dir: Optional[Path] = None) -> FastAPI:
    web_dir = web_dir or DEFAULT_WEB_DIR
    hub = WebSocketHub()
    app = FastAPI(
        title="PepperEvolution API",
        description="Cloud AI control system for Pepper robot",
        version="2.1.0",
    )
    app.state.hub = hub
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )

    robot.on_event(hub.on_robot_event)
    ai_manager.on_partial(hub.on_partial)
    ai_manager.on_response(hub.on_response)

    @app.get("/")
    async def root():
        index = web_dir / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")
        return {"name": "PepperEvolution", "version": "2.1.0", "status": "running"}

    @app.get("/health")
    async def health():
        try:
            h = await robot.connection.health_check()
            return {"status": "healthy" if h.get("status") == "connected" else "degraded", "bridge": h}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/status")
    async def status():
        try:
            sensors = await robot.get_sensors()
            return {"robot_state": _state_payload(robot), "sensors": sensors, "busy": ai_manager.busy}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/chat")
    async def chat(request: ChatRequest):
        if not request.message.strip():
            raise HTTPException(status_code=400, detail="message is empty")
        try:
            return await ai_manager.process_user_input(
                request.message, speak=request.speak, client_id=request.client_id
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/command/{cmd}")
    async def command(cmd: str, body: Optional[CommandParams] = None):
        return await execute_command(robot, cmd, body.params if body else {})

    @app.get("/tools")
    async def list_tools():
        return {"tools": TOOLS}

    @app.get("/animations")
    async def list_animations():
        return {"animations": robot.animations}

    @app.post("/photo")
    async def take_photo(camera: int = 0, resolution: Optional[int] = None):
        try:
            shot = await robot.take_picture(camera=camera, resolution=resolution)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc))
        return {"media_type": shot.media_type, "base64": shot.base64_data, "width": shot.width, "height": shot.height}

    @app.get("/photo/latest")
    async def latest_photo():
        shot = ai_manager.last_photo or robot.last_photo
        if shot is None:
            raise HTTPException(status_code=404, detail="no photo taken yet")
        return Response(content=base64.b64decode(shot.base64_data), media_type=shot.media_type)

    @app.get("/conversation/history")
    async def get_history():
        return {"history": ai_manager.get_conversation_history()}

    @app.delete("/conversation/history")
    async def clear_history():
        ai_manager.clear_conversation_history()
        return {"success": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await hub.send(websocket, {"type": "error", "message": "Invalid JSON"})
                    continue
                await _handle_ws_message(websocket, data)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.bind(module="WebSocketHub").warning(f"WebSocket error: {exc}")
        finally:
            hub.disconnect(websocket)

    chat_tasks: Set[asyncio.Task] = set()

    async def _run_chat(ws: WebSocket, message: str, speak: Optional[bool], client_id: Optional[str]):
        try:
            await ai_manager.process_user_input(message, speak=speak, client_id=client_id)
        except Exception as exc:  # noqa: BLE001
            await hub.send(ws, {"type": "error", "message": str(exc)})

    async def _handle_ws_message(ws: WebSocket, data: Dict[str, Any]):
        msg_type = data.get("type", "")
        if msg_type == "chat":
            message = (data.get("message") or "").strip()
            if not message:
                return await hub.send(ws, {"type": "error", "message": "Empty message"})
            await hub.broadcast({"type": "chat_user", "message": message, "source": "ws"})
            # The reply reaches every client through hub.on_response.
            # Run the turn in the background so this socket keeps receiving (e.g. an emergency_stop
            # command sent mid-turn). The reply reaches every client through hub.on_response.
            task = asyncio.create_task(_run_chat(ws, message, data.get("speak"), data.get("client_id")), name="ws-chat")
            chat_tasks.add(task)
            task.add_done_callback(chat_tasks.discard)
        elif msg_type == "command":
            result = await execute_command(robot, data.get("command", ""), data.get("params") or {})
            await hub.send(ws, {"type": "command_response", **result})
        elif msg_type == "status_request":
            await hub.send(
                ws, {"type": "status_response", "robot_state": _state_payload(robot), "busy": ai_manager.busy}
            )
        elif msg_type == "sensor_request":
            await hub.send(ws, {"type": "sensor_response", "sensors": await robot.get_sensors()})
        elif msg_type == "ping":
            await hub.send(ws, {"type": "pong"})
        else:
            await hub.send(ws, {"type": "error", "message": f"Unknown message type: {msg_type}"})

    return app


class _Server(uvicorn.Server):
    """uvicorn re-raises a captured SIGINT after serve() returns, which under asyncio.run() cancels
    the shutdown coroutine before the robot is put to rest. We handle the signals ourselves."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class APIServer:
    """Runs the FastAPI app with uvicorn (handles SIGINT/SIGTERM for a clean shutdown)."""

    def __init__(self, host: str, port: int, ai_manager: AIManager, robot: PepperRobot, web_dir: Optional[Path] = None):
        self.host = host
        self.port = port
        self.ai_manager = ai_manager
        self.robot = robot
        self.logger = logger.bind(module="APIServer")
        self.app = create_app(ai_manager, robot, web_dir)
        self.server: Optional[uvicorn.Server] = None

    @property
    def hub(self) -> WebSocketHub:
        return self.app.state.hub

    async def start(self):
        self.logger.info(f"Starting API server on http://{self.host}:{self.port}")
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self.server = _Server(config)
        loop = asyncio.get_running_loop()
        installed = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_exit, sig)
                installed.append(sig)
            except (NotImplementedError, RuntimeError):  # Windows / non-main thread
                pass
        try:
            await self.server.serve()
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)

    def _request_exit(self, sig: int):
        self.logger.info(f"Signal {sig} received; shutting down")
        if self.server:
            self.server.should_exit = True

    async def stop(self):
        if self.server:
            self.server.should_exit = True
            self.logger.info("API server stopping")
