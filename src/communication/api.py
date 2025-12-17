"""
REST API server for PepperEvolution
"""

import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from loguru import logger

from ..ai import AIManager
from ..pepper import PepperRobot


class ChatRequest(BaseModel):
    message: str


class CommandRequest(BaseModel):
    command: str
    params: Optional[Dict[str, Any]] = {}

class MoveHeadRequest(BaseModel):
    yaw: float
    pitch: float
    speed: float = 0.2

class TabletTextRequest(BaseModel):
    text: str
    background: Optional[str] = "#000000"

class TabletURLRequest(BaseModel):
    url: str

class AwarenessRequest(BaseModel):
    enabled: bool

class VolumeRequest(BaseModel):
    volume: int


class APIServer:
    """REST API server for PepperEvolution"""
    
    def __init__(self, host: str, port: int, ai_manager: AIManager, robot: PepperRobot):
        self.host = host
        self.port = port
        self.ai_manager = ai_manager
        self.robot = robot
        self.logger = logger.bind(module="APIServer")
        
        # Create FastAPI app
        self.app = FastAPI(
            title="PepperEvolution API",
            description="REST API for controlling Pepper robot with AI",
            version="1.0.0"
        )
        
        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Configure appropriately for production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Setup routes
        self._setup_routes()
        
        # Server instance
        self.server = None
        
    def _setup_routes(self):
        """Setup API routes"""
        
        @self.app.get("/")
        async def root():
            """Root endpoint"""
            return {
                "message": "PepperEvolution API",
                "version": "1.0.0",
                "status": "running"
            }
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint"""
            try:
                # Check robot connection
                robot_health = await self.robot.connection.health_check()
                
                return {
                    "status": "healthy",
                    "robot": robot_health,
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/status")
        async def get_status():
            """Get robot status"""
            try:
                status = {
                    "robot_state": self.robot.get_state().__dict__,
                    "is_ready": await self.robot.is_ready(),
                    "is_moving": self.robot.actuators.is_moving(),
                    "timestamp": asyncio.get_event_loop().time()
                }
                return status
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/sensors")
        async def get_sensor_data():
            """Get sensor data"""
            try:
                sensor_data = await self.robot.get_environment_info()
                return {
                    "sensor_data": sensor_data,
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/chat")
        async def chat(request: ChatRequest):
            """Send chat message to robot"""
            try:
                response = await self.ai_manager.process_user_input(request.message)
                return {
                    "response": response,
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/listen")
        async def listen_for_speech(timeout: float = 10.0):
            """Listen for speech and process it"""
            try:
                user_input = await self.robot.listen(timeout=timeout)
                if user_input:
                    response = await self.ai_manager.process_user_input(user_input)
                    return {
                        "success": True,
                        "heard": user_input,
                        "response": response,
                        "timestamp": asyncio.get_event_loop().time()
                    }
                else:
                    return {
                        "success": False,
                        "message": "No speech detected",
                        "timestamp": asyncio.get_event_loop().time()
                    }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/command")
        async def execute_command(request: CommandRequest):
            """Execute robot command"""
            try:
                result = await self._execute_robot_command(request.command, request.params)
                return {
                    "command": request.command,
                    "result": result,
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/speak")
        async def speak(request: ChatRequest):
            """Make robot speak"""
            try:
                success = await self.robot.speak(request.message)
                return {
                    "success": success,
                    "message": f"Spoke: {request.message}",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/move/forward")
        async def move_forward(distance: float = 0.5):
            """Move robot forward"""
            try:
                success = await self.robot.move_forward(distance)
                return {
                    "success": success,
                    "message": f"Moved forward {distance}m",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/move/head")
        async def move_head(req: MoveHeadRequest):
            try:
                success = await self.robot.actuators.move_head(req.yaw, req.pitch, req.speed)
                return {"success": success, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/move/turn")
        async def turn(angle: float = 90):
            """Turn robot"""
            try:
                success = await self.robot.turn(angle)
                return {
                    "success": success,
                    "message": f"Turned {angle} degrees",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/gesture/wave")
        async def wave():
            """Wave gesture"""
            try:
                success = await self.robot.wave_hand()
                return {
                    "success": success,
                    "message": "Waved hand",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/gesture/nod")
        async def nod():
            """Nod head"""
            try:
                success = await self.robot.nod_head()
                return {
                    "success": success,
                    "message": "Nodded head",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/photo")
        async def take_photo():
            """Take a photo"""
            try:
                photo = await self.robot.take_photo()
                if photo is not None:
                    # Convert to base64 for transmission
                    import base64
                    import cv2
                    _, buffer = cv2.imencode('.jpg', photo)
                    photo_base64 = base64.b64encode(buffer).decode('utf-8')
                    return {
                        "success": True,
                        "photo": photo_base64,
                        "timestamp": asyncio.get_event_loop().time()
                    }
                else:
                    return {
                        "success": False,
                        "message": "Failed to take photo",
                        "timestamp": asyncio.get_event_loop().time()
                    }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/wake_up")
        async def wake_up():
            try:
                success = await self.robot.actuators.wake_up()
                return {"success": success, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/rest")
        async def rest():
            try:
                success = await self.robot.actuators.rest()
                return {"success": success, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/volume")
        async def set_volume(req: VolumeRequest):
            try:
                success = await self.robot.actuators.set_volume(req.volume)
                return {"success": success, "volume": req.volume, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/awareness")
        async def set_awareness(req: AwarenessRequest):
            try:
                success = await self.robot.actuators.set_awareness(req.enabled)
                return {"success": success, "enabled": req.enabled, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/led/eyes")
        async def set_eye_color(color: str = "blue"):
            """Set eye LED color"""
            try:
                await self.robot.actuators.set_eye_color(color)
                return {
                    "success": True,
                    "message": f"Set eye color to {color}",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/led/chest")
        async def set_chest_color(color: str = "blue"):
            """Set chest LED color"""
            try:
                await self.robot.actuators.set_chest_led(color)
                return {
                    "success": True,
                    "message": f"Set chest color to {color}",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/tablet/text")
        async def tablet_text(req: TabletTextRequest):
            try:
                success = await self.robot.actuators.tablet_show_text(req.text, req.background)
                return {"success": success, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/tablet/web")
        async def tablet_web(req: TabletURLRequest):
            try:
                success = await self.robot.actuators.show_webpage(req.url)
                return {"success": success, "url": req.url, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/tablet/image")
        async def tablet_image(req: TabletURLRequest):
            try:
                success = await self.robot.actuators.show_image(req.url)
                return {"success": success, "url": req.url, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/tablet/hide")
        async def tablet_hide():
            try:
                success = await self.robot.actuators.tablet_hide()
                return {"success": success, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/tablet/brightness")
        async def tablet_brightness(brightness: int = 100):
            try:
                success = await self.robot.actuators.tablet_set_brightness(brightness)
                return {"success": success, "brightness": brightness, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/animations")
        async def get_animations():
            try:
                animations = await self.robot.actuators.list_animations()
                return {"animations": animations, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/animation")
        async def play_animation(name: str):
            try:
                success = await self.robot.actuators.play_animation(name)
                return {"success": success, "name": name, "timestamp": asyncio.get_event_loop().time()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/emergency/stop")
        async def emergency_stop():
            """Emergency stop"""
            try:
                await self.robot.emergency_stop()
                return {
                    "success": True,
                    "message": "Emergency stop activated",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/conversation/history")
        async def get_conversation_history():
            """Get conversation history"""
            try:
                history = self.ai_manager.get_conversation_history()
                return {
                    "history": history,
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.delete("/conversation/history")
        async def clear_conversation_history():
            """Clear conversation history"""
            try:
                self.ai_manager.clear_conversation_history()
                return {
                    "success": True,
                    "message": "Conversation history cleared",
                    "timestamp": asyncio.get_event_loop().time()
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time communication"""
            await websocket.accept()
            try:
                while True:
                    # Receive message
                    data = await websocket.receive_text()
                    
                    # Parse JSON
                    import json
                    message = json.loads(data)
                    
                    # Handle message
                    if message.get("type") == "chat":
                        response = await self.ai_manager.process_user_input(message.get("message", ""))
                        await websocket.send_text(json.dumps({
                            "type": "chat_response",
                            "response": response
                        }))
                    elif message.get("type") == "status_request":
                        status = {
                            "robot_state": self.robot.get_state().__dict__,
                            "is_ready": await self.robot.is_ready(),
                            "is_moving": self.robot.actuators.is_moving()
                        }
                        await websocket.send_text(json.dumps({
                            "type": "status_response",
                            "status": status
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Unknown message type"
                        }))
                        
            except WebSocketDisconnect:
                self.logger.info("WebSocket client disconnected")
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": str(e)
                }))
    
    async def _execute_robot_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
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
    
    async def start(self):
        """Start the API server"""
        try:
            self.logger.info(f"Starting API server on {self.host}:{self.port}")
            
            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="info"
            )
            
            self.server = uvicorn.Server(config)
            await self.server.serve()
            
        except Exception as e:
            self.logger.error(f"Failed to start API server: {e}")
            raise
    
    async def stop(self):
        """Stop the API server"""
        try:
            if self.server:
                self.server.should_exit = True
                self.logger.info("API server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping API server: {e}")
