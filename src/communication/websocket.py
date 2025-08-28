"""
WebSocket server for real-time communication with Pepper robot
"""

import asyncio
import json
from typing import Dict, Any, Set, Optional
import websockets
from websockets.server import WebSocketServerProtocol

from loguru import logger

from ..ai import AIManager
from ..pepper import PepperRobot


class WebSocketServer:
    """WebSocket server for real-time robot communication"""
    
    def __init__(self, host: str, port: int, ai_manager: AIManager, robot: PepperRobot):
        self.host = host
        self.port = port
        self.ai_manager = ai_manager
        self.robot = robot
        self.logger = logger.bind(module="WebSocketServer")
        
        # Connected clients
        self.clients: Set[WebSocketServerProtocol] = set()
        
        # Server instance
        self.server = None
        
    async def start(self):
        """Start the WebSocket server"""
        try:
            self.logger.info(f"Starting WebSocket server on {self.host}:{self.port}")
            
            # Start server
            self.server = await websockets.serve(
                self.handle_client,
                self.host,
                self.port
            )
            
            self.logger.success(f"WebSocket server started on {self.host}:{self.port}")
            
            # Keep server running
            await self.server.wait_closed()
            
        except Exception as e:
            self.logger.error(f"Failed to start WebSocket server: {e}")
            raise
    
    async def stop(self):
        """Stop the WebSocket server"""
        try:
            if self.server:
                self.server.close()
                await self.server.wait_closed()
                self.logger.info("WebSocket server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping WebSocket server: {e}")
    
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """Handle individual WebSocket client connections"""
        try:
            # Add client to set
            self.clients.add(websocket)
            client_id = id(websocket)
            
            self.logger.info(f"Client {client_id} connected from {websocket.remote_address}")
            
            # Send welcome message
            await self.send_to_client(websocket, {
                "type": "welcome",
                "message": "Connected to PepperEvolution WebSocket server",
                "client_id": client_id
            })
            
            # Handle messages from client
            async for message in websocket:
                try:
                    await self.handle_message(websocket, message)
                except Exception as e:
                    self.logger.error(f"Error handling message from client {client_id}: {e}")
                    await self.send_error(websocket, str(e))
                    
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            self.logger.error(f"Error handling client {client_id}: {e}")
        finally:
            # Remove client from set
            self.clients.discard(websocket)
    
    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        """Handle incoming WebSocket message"""
        try:
            # Parse JSON message
            data = json.loads(message)
            message_type = data.get("type")
            
            if message_type == "chat":
                await self.handle_chat_message(websocket, data)
            elif message_type == "command":
                await self.handle_command_message(websocket, data)
            elif message_type == "status_request":
                await self.handle_status_request(websocket, data)
            elif message_type == "sensor_request":
                await self.handle_sensor_request(websocket, data)
            else:
                await self.send_error(websocket, f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            await self.send_error(websocket, "Invalid JSON message")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            await self.send_error(websocket, str(e))
    
    async def handle_chat_message(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle chat message from client"""
        try:
            user_input = data.get("message", "")
            if not user_input:
                await self.send_error(websocket, "Empty message")
                return
            
            # Process with AI manager
            response = await self.ai_manager.process_user_input(user_input)
            
            # Send response back to client
            await self.send_to_client(websocket, {
                "type": "chat_response",
                "message": response,
                "timestamp": asyncio.get_event_loop().time()
            })
            
            # Broadcast to other clients
            await self.broadcast({
                "type": "chat_broadcast",
                "user_message": user_input,
                "robot_response": response,
                "timestamp": asyncio.get_event_loop().time()
            }, exclude={websocket})
            
        except Exception as e:
            self.logger.error(f"Error handling chat message: {e}")
            await self.send_error(websocket, str(e))
    
    async def handle_command_message(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle direct robot command"""
        try:
            command = data.get("command", "")
            params = data.get("params", {})
            
            if not command:
                await self.send_error(websocket, "Empty command")
                return
            
            # Execute robot command
            result = await self.execute_robot_command(command, params)
            
            # Send result back to client
            await self.send_to_client(websocket, {
                "type": "command_response",
                "command": command,
                "result": result,
                "timestamp": asyncio.get_event_loop().time()
            })
            
        except Exception as e:
            self.logger.error(f"Error handling command: {e}")
            await self.send_error(websocket, str(e))
    
    async def handle_status_request(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle status request"""
        try:
            # Get robot status
            status = {
                "robot_state": self.robot.get_state().__dict__,
                "is_ready": await self.robot.is_ready(),
                "is_moving": self.robot.actuators.is_moving(),
                "timestamp": asyncio.get_event_loop().time()
            }
            
            await self.send_to_client(websocket, {
                "type": "status_response",
                "status": status
            })
            
        except Exception as e:
            self.logger.error(f"Error handling status request: {e}")
            await self.send_error(websocket, str(e))
    
    async def handle_sensor_request(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle sensor data request"""
        try:
            # Get sensor data
            sensor_data = await self.robot.get_environment_info()
            
            await self.send_to_client(websocket, {
                "type": "sensor_response",
                "sensor_data": sensor_data,
                "timestamp": asyncio.get_event_loop().time()
            })
            
        except Exception as e:
            self.logger.error(f"Error handling sensor request: {e}")
            await self.send_error(websocket, str(e))
    
    async def execute_robot_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a robot command"""
        try:
            if command == "speak":
                text = params.get("text", "")
                language = params.get("language", "en")
                success = await self.robot.speak(text, language)
                return {"success": success, "message": f"Spoke: {text}"}
                
            elif command == "move_forward":
                distance = params.get("distance", 0.5)
                success = await self.robot.move_forward(distance)
                return {"success": success, "message": f"Moved forward {distance}m"}
                
            elif command == "turn":
                angle = params.get("angle", 90)
                success = await self.robot.turn(angle)
                return {"success": success, "message": f"Turned {angle} degrees"}
                
            elif command == "wave":
                success = await self.robot.wave_hand()
                return {"success": success, "message": "Waved hand"}
                
            elif command == "take_photo":
                photo = await self.robot.take_photo()
                if photo is not None:
                    # Convert to base64 for transmission
                    import base64
                    import cv2
                    _, buffer = cv2.imencode('.jpg', photo)
                    photo_base64 = base64.b64encode(buffer).decode('utf-8')
                    return {"success": True, "photo": photo_base64}
                else:
                    return {"success": False, "message": "Failed to take photo"}
                    
            elif command == "set_eye_color":
                color = params.get("color", "blue")
                await self.robot.actuators.set_eye_color(color)
                return {"success": True, "message": f"Set eye color to {color}"}
                
            elif command == "emergency_stop":
                await self.robot.emergency_stop()
                return {"success": True, "message": "Emergency stop activated"}
                
            else:
                return {"success": False, "message": f"Unknown command: {command}"}
                
        except Exception as e:
            self.logger.error(f"Error executing command {command}: {e}")
            return {"success": False, "error": str(e)}
    
    async def send_to_client(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Send message to specific client"""
        try:
            message = json.dumps(data)
            await websocket.send(message)
        except Exception as e:
            self.logger.error(f"Error sending message to client: {e}")
    
    async def send_error(self, websocket: WebSocketServerProtocol, error_message: str):
        """Send error message to client"""
        await self.send_to_client(websocket, {
            "type": "error",
            "message": error_message,
            "timestamp": asyncio.get_event_loop().time()
        })
    
    async def broadcast(self, data: Dict[str, Any], exclude: Optional[Set[WebSocketServerProtocol]] = None):
        """Broadcast message to all connected clients"""
        if exclude is None:
            exclude = set()
        
        message = json.dumps(data)
        disconnected_clients = set()
        
        for client in self.clients:
            if client not in exclude:
                try:
                    await client.send(message)
                except Exception as e:
                    self.logger.error(f"Error broadcasting to client: {e}")
                    disconnected_clients.add(client)
        
        # Remove disconnected clients
        for client in disconnected_clients:
            self.clients.discard(client)
    
    async def broadcast_sensor_data(self, sensor_data: Dict[str, Any]):
        """Broadcast sensor data to all clients"""
        await self.broadcast({
            "type": "sensor_update",
            "sensor_data": sensor_data,
            "timestamp": asyncio.get_event_loop().time()
        })
    
    async def broadcast_robot_status(self, status: Dict[str, Any]):
        """Broadcast robot status to all clients"""
        await self.broadcast({
            "type": "status_update",
            "status": status,
            "timestamp": asyncio.get_event_loop().time()
        })
