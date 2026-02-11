"""
Async HTTP client for the Pepper Bridge Server.

All robot interaction flows through this client, which talks to the
Tornado bridge running on the robot over HTTP.
"""

import httpx
from typing import Any, Dict, Optional

from loguru import logger


class BridgeError(Exception):
    """Raised when the bridge returns a non-OK response."""


class BridgeClient:
    """Async HTTP client wrapping every bridge endpoint."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self.logger = logger.bind(module="BridgeClient")

    async def connect(self):
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(self.timeout, connect=5.0),
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BridgeClient not connected. Call connect() first.")
        return self._client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, **params: Any) -> Dict[str, Any]:
        resp = await self.client.get(path, params=params)
        return self._handle(resp)

    async def _post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = await self.client.post(path, json=json or {})
        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> Dict[str, Any]:
        data = resp.json()
        if resp.status_code == 401:
            raise BridgeError("Unauthorized - check BRIDGE_API_KEY")
        if not data.get("ok"):
            raise BridgeError(data.get("error", f"HTTP {resp.status_code}"))
        return data

    # ------------------------------------------------------------------
    # Health / Status
    # ------------------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        return await self._get("/health")

    async def status(self) -> Dict[str, Any]:
        return await self._get("/status")

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    async def speak(self, text: str, language: Optional[str] = None, animated: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text, "animated": animated}
        if language:
            body["language"] = language
        return await self._post("/speak", json=body)

    async def set_volume(self, level: int) -> Dict[str, Any]:
        return await self._post("/volume", json={"level": level})

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> Dict[str, Any]:
        return await self._post("/move/forward", json={"distance": distance, "speed": speed})

    async def move_turn(self, angle: float) -> Dict[str, Any]:
        return await self._post("/move/turn", json={"angle": angle})

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> Dict[str, Any]:
        return await self._post("/move/head", json={"yaw": yaw, "pitch": pitch, "speed": speed})

    async def move_to(self, x: float, y: float, theta: float = 0) -> Dict[str, Any]:
        return await self._post("/move/to", json={"x": x, "y": y, "theta": theta})

    async def stop(self) -> Dict[str, Any]:
        return await self._post("/stop")

    async def emergency_stop(self) -> Dict[str, Any]:
        return await self._post("/emergency_stop")

    # ------------------------------------------------------------------
    # Posture / Stiffness
    # ------------------------------------------------------------------

    async def set_posture(self, posture: str, speed: float = 0.5) -> Dict[str, Any]:
        return await self._post("/posture", json={"posture": posture, "speed": speed})

    async def wake_up(self) -> Dict[str, Any]:
        return await self._post("/wake_up")

    async def rest(self) -> Dict[str, Any]:
        return await self._post("/rest")

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    async def take_picture(self, camera: int = 0, resolution: int = 2) -> Dict[str, Any]:
        return await self._get("/picture", camera=camera, resolution=resolution)

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------

    async def get_sensors(self) -> Dict[str, Any]:
        return await self._get("/sensors")

    # ------------------------------------------------------------------
    # LEDs
    # ------------------------------------------------------------------

    async def set_eye_leds(
        self, color: Optional[str] = None, r: float = 0, g: float = 0, b: float = 0, duration: float = 0.5
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"r": r, "g": g, "b": b, "duration": duration}
        if color:
            body["color"] = color
        return await self._post("/leds/eyes", json=body)

    async def set_chest_leds(
        self, color: Optional[str] = None, r: float = 0, g: float = 0, b: float = 0, duration: float = 0.5
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"r": r, "g": g, "b": b, "duration": duration}
        if color:
            body["color"] = color
        return await self._post("/leds/chest", json=body)

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    async def play_animation(self, name: str) -> Dict[str, Any]:
        return await self._post("/animation", json={"name": name})

    # ------------------------------------------------------------------
    # Awareness / Autonomous Life
    # ------------------------------------------------------------------

    async def set_awareness(self, enabled: bool) -> Dict[str, Any]:
        return await self._post("/awareness", json={"enabled": enabled})

    async def set_autonomous_life(self, state: str) -> Dict[str, Any]:
        return await self._post("/autonomous_life", json={"state": state})

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    async def record_audio(self, duration: float = 3.0) -> Dict[str, Any]:
        return await self._post("/audio/record", json={"duration": duration})
