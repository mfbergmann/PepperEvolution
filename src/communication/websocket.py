"""
WebSocket server for real-time communication with Pepper robot.

Relays user chat messages to AIManager and forwards robot events to web clients.
"""

import asyncio
import json
from typing import Any, Dict, Set, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from loguru import logger

from ..ai import AIManager
from ..pepper import PepperRobot


class WebSocketServer:
    """Standalone WebSocket server for real-time robot communication."""

    def __init__(self, host: str, port: int, ai_manager: AIManager, robot: PepperRobot):
        self.host = host
        self.port = port
        self.ai_manager = ai_manager
        self.robot = robot
        self.logger = logger.bind(module="WebSocketServer")
        self.clients: Set[WebSocketServerProtocol] = set()
        self.server = None

    async def start(self):
        self.logger.info(f"Starting WebSocket server on {self.host}:{self.port}")

        # Register for robot events to broadcast
        self.robot.on_event(self._on_robot_event)

        self.server = await websockets.serve(self.handle_client, self.host, self.port)
        self.logger.success(f"WebSocket server running on {self.host}:{self.port}")
        await self.server.wait_closed()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info("WebSocket server stopped")

    async def handle_client(self, websocket: WebSocketServerProtocol, path: str = ""):
        self.clients.add(websocket)
        client_id = id(websocket)
        self.logger.info(f"Client {client_id} connected")

        await self._send(websocket, {"type": "welcome", "client_id": client_id})

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    await self._handle_message(websocket, data)
                except json.JSONDecodeError:
                    await self._send(websocket, {"type": "error", "message": "Invalid JSON"})
                except Exception as exc:
                    self.logger.error(f"Message handling error: {exc}")
                    await self._send(websocket, {"type": "error", "message": str(exc)})
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            self.logger.info(f"Client {client_id} disconnected")

    async def _handle_message(self, ws: WebSocketServerProtocol, data: Dict[str, Any]):
        msg_type = data.get("type", "")

        if msg_type == "chat":
            message = data.get("message", "")
            if not message:
                return await self._send(ws, {"type": "error", "message": "Empty message"})

            result = await self.ai_manager.process_user_input(message)
            await self._send(ws, {"type": "chat_response", **result})

            # Broadcast to other clients
            await self._broadcast(
                {"type": "chat_broadcast", "user_message": message, **result},
                exclude={ws},
            )

        elif msg_type == "command":
            cmd = data.get("command", "")
            params = data.get("params", {})
            bridge = self.robot.connection.bridge
            try:
                result = await getattr(bridge, cmd)(**params)
                await self._send(ws, {"type": "command_response", "command": cmd, "result": result})
            except AttributeError:
                await self._send(ws, {"type": "error", "message": f"Unknown command: {cmd}"})

        elif msg_type == "status_request":
            state = self.robot.get_state()
            await self._send(ws, {
                "type": "status_response",
                "robot_state": {
                    "battery_level": state.battery_level,
                    "posture": state.posture,
                    "is_connected": state.is_connected,
                },
            })

        elif msg_type == "sensor_request":
            sensors = await self.robot.get_sensors()
            await self._send(ws, {"type": "sensor_response", "sensors": sensors})

        else:
            await self._send(ws, {"type": "error", "message": f"Unknown message type: {msg_type}"})

    async def _on_robot_event(self, event_type: str, data: Dict[str, Any]):
        """Forward bridge events to all connected web clients."""
        await self._broadcast({"type": "robot_event", "event": event_type, "data": data})

    async def _send(self, ws: WebSocketServerProtocol, data: Dict[str, Any]):
        try:
            await ws.send(json.dumps(data))
        except Exception:
            pass

    async def _broadcast(self, data: Dict[str, Any], exclude: Optional[Set[WebSocketServerProtocol]] = None):
        exclude = exclude or set()
        msg = json.dumps(data)
        dead = set()
        for client in self.clients:
            if client not in exclude:
                try:
                    await client.send(msg)
                except Exception:
                    dead.add(client)
        for c in dead:
            self.clients.discard(c)
