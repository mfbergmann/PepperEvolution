#!/usr/bin/env python3
"""
Pepper Bridge Client
Runs on OFF-ROBOT computer - connects to Pepper Bridge for remote AI control

Install: pip install websockets numpy pillow
"""

import json
import struct
import asyncio
import threading
import time
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import base64

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    raise

try:
    import websockets
    from websockets.sync.client import connect as ws_connect
except ImportError:
    print("Install websockets: pip install websockets")
    websockets = None

try:
    import numpy as np
except ImportError:
    print("Install numpy: pip install numpy")
    np = None

try:
    from PIL import Image
except ImportError:
    print("Install pillow: pip install pillow")
    Image = None


@dataclass
class VideoFrame:
    """Video frame from Pepper's camera"""
    width: int
    height: int
    format: str
    timestamp: float
    data: bytes
    
    def to_numpy(self) -> 'np.ndarray':
        """Convert to numpy array (requires numpy)"""
        if np is None:
            raise ImportError("numpy required: pip install numpy")
        arr = np.frombuffer(self.data, dtype=np.uint8)
        return arr.reshape((self.height, self.width, 3))
    
    def to_pil(self) -> 'Image.Image':
        """Convert to PIL Image (requires pillow)"""
        if Image is None:
            raise ImportError("pillow required: pip install pillow")
        return Image.frombytes('RGB', (self.width, self.height), self.data)
    
    def save(self, path: str):
        """Save frame to file"""
        self.to_pil().save(path)


@dataclass
class AudioChunk:
    """Audio chunk from Pepper's microphone"""
    format: str
    sample_rate: int
    channels: int
    timestamp: float
    data: bytes
    
    def save(self, path: str):
        """Save audio chunk to file"""
        with open(path, 'wb') as f:
            f.write(self.data)


@dataclass
class SensorData:
    """Sensor data from Pepper"""
    timestamp: float
    battery: Optional[Dict] = None
    sonar: Optional[Dict] = None
    touch: Optional[Dict] = None
    position: Optional[Dict] = None
    people: Optional[Dict] = None
    raw: Optional[Dict] = None


class PepperClient:
    """
    Client for connecting to Pepper Bridge.
    
    Example usage:
        pepper = PepperClient("192.168.1.100")
        
        # Basic commands
        pepper.speak("Hello!")
        pepper.move_forward(0.5)
        pepper.turn(90)
        
        # Get camera image
        frame = pepper.get_picture()
        frame.save("snapshot.jpg")
        
        # Stream video (async)
        async def handle_frame(frame):
            print(f"Got frame {frame.width}x{frame.height}")
        pepper.start_video_stream(handle_frame)
    """
    
    def __init__(self, host: str, http_port: int = 8888, ws_port: int = 8889):
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.base_url = f"http://{host}:{http_port}"
        self.ws_base = f"ws://{host}:{ws_port}"
        
        # Stream handlers
        self._video_handler: Optional[Callable] = None
        self._audio_handler: Optional[Callable] = None
        self._sensor_handler: Optional[Callable] = None
        
        # Stream threads
        self._video_thread: Optional[threading.Thread] = None
        self._audio_thread: Optional[threading.Thread] = None
        self._sensor_thread: Optional[threading.Thread] = None
        self._streams_running = False
        
    # === HTTP API Methods ===
    
    def _get(self, endpoint: str) -> Dict:
        """Make GET request"""
        resp = requests.get(f"{self.base_url}{endpoint}", timeout=30)
        return resp.json()
    
    def _post(self, endpoint: str, data: Dict = None) -> Dict:
        """Make POST request"""
        resp = requests.post(
            f"{self.base_url}{endpoint}",
            json=data or {},
            timeout=30
        )
        return resp.json()
    
    # --- Status ---
    
    def health(self) -> Dict:
        """Check bridge health and get stream URLs"""
        return self._get("/health")
    
    def status(self) -> Dict:
        """Get robot status (battery, posture, etc.)"""
        return self._get("/status")
    
    def get_animations(self) -> list:
        """Get list of available animations"""
        result = self._get("/animations")
        return result.get("animations", [])
    
    # --- Motion ---
    
    def wake_up(self) -> Dict:
        """Wake up the robot"""
        return self._post("/wake_up")
    
    def rest(self) -> Dict:
        """Put robot to rest"""
        return self._post("/rest")
    
    def set_posture(self, posture: str, speed: float = 0.5) -> Dict:
        """Set robot posture (StandInit, Stand, Crouch, etc.)"""
        return self._post("/posture", {"posture": posture, "speed": speed})
    
    def move_forward(self, distance: float) -> Dict:
        """Move forward by distance in meters"""
        return self._post("/move/forward", {"distance": distance})
    
    def turn(self, angle: float) -> Dict:
        """Turn by angle in degrees"""
        return self._post("/move/turn", {"angle": angle})
    
    def move_head(self, yaw: float, pitch: float, speed: float = 0.2) -> Dict:
        """Move head to yaw/pitch angles in degrees"""
        return self._post("/move/head", {"yaw": yaw, "pitch": pitch, "speed": speed})
    
    def stop(self) -> Dict:
        """Stop all motion"""
        return self._post("/stop")
    
    # --- Speech ---
    
    def speak(self, text: str, animated: bool = True, language: str = "English") -> Dict:
        """Make robot speak text"""
        return self._post("/speak", {
            "text": text,
            "animated": animated,
            "language": language
        })
    
    def set_volume(self, volume: int) -> Dict:
        """Set speech volume (0-100)"""
        return self._post("/volume", {"volume": volume})
    
    # --- Animation ---
    
    def play_animation(self, animation: str) -> Dict:
        """Play an animation by name"""
        return self._post("/animation", {"animation": animation})
    
    # --- LEDs ---
    
    def set_eye_color(self, color: str) -> Dict:
        """Set eye LED color (red, green, blue, white, yellow, magenta, cyan, off)"""
        return self._post("/leds/eyes", {"color": color})
    
    # --- Tablet ---
    
    def tablet_show_text(self, text: str, background: str = "#000000") -> Dict:
        """Show text on tablet"""
        return self._post("/tablet/text", {"text": text, "background": background})
    
    def tablet_show_web(self, url: str) -> Dict:
        """Show webpage on tablet"""
        return self._post("/tablet/web", {"url": url})
    
    def tablet_show_image(self, url: str) -> Dict:
        """Show image on tablet from URL"""
        return self._post("/tablet/image", {"url": url})
    
    def tablet_hide(self) -> Dict:
        """Hide tablet content"""
        return self._post("/tablet/hide")
    
    def tablet_brightness(self, brightness: int) -> Dict:
        """Set tablet brightness (0-100)"""
        return self._post("/tablet/brightness", {"brightness": brightness})
    
    # --- Awareness ---
    
    def set_awareness(self, enabled: bool) -> Dict:
        """Enable or disable basic awareness"""
        return self._post("/awareness", {"enabled": enabled})
    
    # --- Camera (single shot) ---
    
    def get_picture(self) -> VideoFrame:
        """Take a single picture and return as VideoFrame"""
        result = self._get("/picture")
        if not result.get("success"):
            raise Exception(result.get("error", "Failed to capture picture"))
        
        return VideoFrame(
            width=result["width"],
            height=result["height"],
            format=result["format"],
            timestamp=time.time(),
            data=base64.b64decode(result["data"])
        )
    
    # === WebSocket Streaming ===
    
    def _parse_video_packet(self, data: bytes) -> VideoFrame:
        """Parse video packet from WebSocket"""
        header_len = struct.unpack('>I', data[:4])[0]
        header = json.loads(data[4:4+header_len].decode('utf-8'))
        image_data = data[4+header_len:]
        return VideoFrame(
            width=header['width'],
            height=header['height'],
            format=header['format'],
            timestamp=header['timestamp'],
            data=image_data
        )
    
    def _parse_audio_packet(self, data: bytes) -> AudioChunk:
        """Parse audio packet from WebSocket"""
        header_len = struct.unpack('>I', data[:4])[0]
        header = json.loads(data[4:4+header_len].decode('utf-8'))
        audio_data = data[4+header_len:]
        return AudioChunk(
            format=header['format'],
            sample_rate=header['sample_rate'],
            channels=header['channels'],
            timestamp=header['timestamp'],
            data=audio_data
        )
    
    def _parse_sensor_packet(self, data: bytes) -> SensorData:
        """Parse sensor packet from WebSocket"""
        raw = json.loads(data.decode('utf-8'))
        return SensorData(
            timestamp=raw.get('timestamp', time.time()),
            battery=raw.get('battery'),
            sonar=raw.get('sonar'),
            touch=raw.get('touch'),
            position=raw.get('position'),
            people=raw.get('people'),
            raw=raw
        )
    
    def _stream_worker(self, stream_type: str, handler: Callable, parser: Callable):
        """Worker thread for streaming"""
        url = f"{self.ws_base}/{stream_type}"
        while self._streams_running:
            try:
                with ws_connect(url) as ws:
                    while self._streams_running:
                        try:
                            data = ws.recv()
                            if isinstance(data, str):
                                data = data.encode('utf-8')
                            packet = parser(data)
                            handler(packet)
                        except Exception as e:
                            if self._streams_running:
                                print(f"Stream error: {e}")
                            break
            except Exception as e:
                if self._streams_running:
                    print(f"Connection error: {e}, reconnecting...")
                    time.sleep(1)
    
    def start_video_stream(self, handler: Callable[[VideoFrame], None]):
        """
        Start video streaming.
        
        Args:
            handler: Callback function that receives VideoFrame objects
        """
        if websockets is None:
            raise ImportError("websockets required: pip install websockets")
        
        self._video_handler = handler
        self._streams_running = True
        self._video_thread = threading.Thread(
            target=self._stream_worker,
            args=('video', handler, self._parse_video_packet),
            daemon=True
        )
        self._video_thread.start()
    
    def start_audio_stream(self, handler: Callable[[AudioChunk], None]):
        """
        Start audio streaming.
        
        Args:
            handler: Callback function that receives AudioChunk objects
        """
        if websockets is None:
            raise ImportError("websockets required: pip install websockets")
        
        self._audio_handler = handler
        self._streams_running = True
        self._audio_thread = threading.Thread(
            target=self._stream_worker,
            args=('audio', handler, self._parse_audio_packet),
            daemon=True
        )
        self._audio_thread.start()
    
    def start_sensor_stream(self, handler: Callable[[SensorData], None]):
        """
        Start sensor data streaming.
        
        Args:
            handler: Callback function that receives SensorData objects
        """
        if websockets is None:
            raise ImportError("websockets required: pip install websockets")
        
        self._sensor_handler = handler
        self._streams_running = True
        self._sensor_thread = threading.Thread(
            target=self._stream_worker,
            args=('sensors', handler, self._parse_sensor_packet),
            daemon=True
        )
        self._sensor_thread.start()
    
    def stop_streams(self):
        """Stop all streams"""
        self._streams_running = False
        if self._video_thread:
            self._video_thread.join(timeout=2)
        if self._audio_thread:
            self._audio_thread.join(timeout=2)
        if self._sensor_thread:
            self._sensor_thread.join(timeout=2)
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.stop_streams()


# === Example usage ===

if __name__ == "__main__":
    import sys
    
    # Get Pepper IP from command line or use default
    pepper_ip = sys.argv[1] if len(sys.argv) > 1 else "10.0.100.1"
    
    print(f"Connecting to Pepper at {pepper_ip}...")
    pepper = PepperClient(pepper_ip)
    
    # Check health
    health = pepper.health()
    print(f"Bridge status: {health['status']}")
    print(f"Stream URLs: {health['streams']}")
    
    # Get status
    status = pepper.status()
    print(f"Robot: {status['robot_name']}")
    print(f"Battery: {status['battery']}%")
    print(f"Posture: {status['posture']}")
    
    # Make Pepper speak
    pepper.speak("Hello! I am connected to the remote AI system.")
    
    # Take a picture
    print("Taking picture...")
    frame = pepper.get_picture()
    frame.save("/tmp/pepper_snapshot.jpg")
    print(f"Saved {frame.width}x{frame.height} image to /tmp/pepper_snapshot.jpg")
    
    # Example: Stream video for 5 seconds
    print("\nStreaming video for 5 seconds...")
    frame_count = [0]
    
    def on_video(frame: VideoFrame):
        frame_count[0] += 1
        if frame_count[0] % 10 == 0:
            print(f"  Received {frame_count[0]} frames")
    
    pepper.start_video_stream(on_video)
    time.sleep(5)
    pepper.stop_streams()
    print(f"Total frames received: {frame_count[0]}")
