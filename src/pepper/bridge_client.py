"""
Async HTTP client for the Pepper Bridge Server.

All robot interaction flows through this client, which talks to the
Tornado bridge running on the robot over HTTP. Every method maps 1:1 to a
bridge endpoint (see docs/BRIDGE_API.md).

Long-running actions (speech, walking, posture changes, animations) block on
the bridge until the robot has finished, so they use a longer ``action_timeout``
than quick status reads.
"""

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger


class BridgeError(Exception):
    """Raised when the bridge is unreachable or returns a non-OK response."""


class BridgeClient:
    """Async HTTP client wrapping every bridge endpoint."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 15.0, action_timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.action_timeout = action_timeout
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

    async def _get(self, path: str, timeout: Optional[float] = None, **params: Any) -> Dict[str, Any]:
        try:
            resp = await self.client.get(path, params=params, timeout=timeout or self.timeout)
        except httpx.TimeoutException as exc:
            raise BridgeError(f"Bridge timed out on GET {path}") from exc
        except httpx.HTTPError as exc:
            raise BridgeError(f"Bridge unreachable on GET {path}: {exc}") from exc
        return self._handle(resp)

    async def _post(
        self, path: str, json: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        try:
            resp = await self.client.post(path, json=json or {}, timeout=timeout or self.timeout)
        except httpx.TimeoutException as exc:
            raise BridgeError(f"Bridge timed out on POST {path}") from exc
        except httpx.HTTPError as exc:
            raise BridgeError(f"Bridge unreachable on POST {path}: {exc}") from exc
        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> Dict[str, Any]:
        if resp.status_code == 401:
            raise BridgeError("Unauthorized - check BRIDGE_API_KEY")
        try:
            data = resp.json()
        except ValueError:
            snippet = resp.text.strip().replace("\n", " ")[:200]
            raise BridgeError(f"Bridge returned HTTP {resp.status_code} with non-JSON body: {snippet}")
        if not isinstance(data, dict) or not data.get("ok"):
            error = data.get("error") if isinstance(data, dict) else None
            raise BridgeError(error or f"HTTP {resp.status_code}")
        return data

    # ------------------------------------------------------------------
    # Health / Status
    # ------------------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        return await self._get("/health")

    async def status(self) -> Dict[str, Any]:
        return await self._get("/status")

    async def get_sensors(self) -> Dict[str, Any]:
        return await self._get("/sensors")

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    async def speak(
        self,
        text: str,
        language: Optional[str] = None,
        animated: bool = True,
        wait: bool = True,
        body_language: str = "contextual",
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text, "animated": animated, "wait": wait, "body_language": body_language}
        if language:
            body["language"] = language
        return await self._post("/speak", json=body, timeout=self.action_timeout if wait else None)

    async def stop_speaking(self) -> Dict[str, Any]:
        return await self._post("/speak/stop")

    async def set_volume(self, level: int) -> Dict[str, Any]:
        return await self._post("/volume", json={"level": level})

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    async def move_forward(self, distance: float = 0.5, speed: float = 0.3) -> Dict[str, Any]:
        return await self._post(
            "/move/forward", json={"distance": distance, "speed": speed}, timeout=self.action_timeout
        )

    async def move_turn(self, angle: float) -> Dict[str, Any]:
        return await self._post("/move/turn", json={"angle": angle}, timeout=self.action_timeout)

    async def move_head(self, yaw: float = 0, pitch: float = 0, speed: float = 0.2) -> Dict[str, Any]:
        return await self._post("/move/head", json={"yaw": yaw, "pitch": pitch, "speed": speed})

    async def move_to(self, x: float, y: float, theta: float = 0, speed: Optional[float] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"x": x, "y": y, "theta": theta}
        if speed is not None:
            body["speed"] = speed
        return await self._post("/move/to", json=body, timeout=self.action_timeout)

    async def stop(self) -> Dict[str, Any]:
        return await self._post("/stop")

    async def emergency_stop(self) -> Dict[str, Any]:
        return await self._post("/emergency_stop")

    # ------------------------------------------------------------------
    # Posture / Stiffness / Life
    # ------------------------------------------------------------------

    async def set_posture(self, posture: str, speed: float = 0.5) -> Dict[str, Any]:
        return await self._post("/posture", json={"posture": posture, "speed": speed}, timeout=self.action_timeout)

    async def wake_up(self) -> Dict[str, Any]:
        return await self._post("/wake_up", timeout=self.action_timeout)

    async def rest(self) -> Dict[str, Any]:
        return await self._post("/rest", timeout=self.action_timeout)

    async def prepare(
        self,
        autonomous_life: Optional[str] = "disabled",
        wake_up: bool = True,
        posture: Optional[str] = None,
        awareness: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Put the robot into a known state for external control (see bridge /prepare)."""
        body: Dict[str, Any] = {"autonomous_life": autonomous_life or "", "wake_up": wake_up}
        if posture:
            body["posture"] = posture
        if awareness is not None:
            body["awareness"] = awareness
        return await self._post("/prepare", json=body, timeout=self.action_timeout)

    async def set_awareness(self, enabled: bool) -> Dict[str, Any]:
        return await self._post("/awareness", json={"enabled": enabled})

    async def set_autonomous_life(self, state: str) -> Dict[str, Any]:
        return await self._post("/autonomous_life", json={"state": state}, timeout=self.action_timeout)

    # ------------------------------------------------------------------
    # Camera / Audio
    # ------------------------------------------------------------------

    async def take_picture(self, camera: int = 0, resolution: int = 2) -> Dict[str, Any]:
        return await self._get("/picture", camera=camera, resolution=resolution, timeout=self.action_timeout)

    async def record_audio(self, duration: float = 3.0) -> Dict[str, Any]:
        return await self._post("/audio/record", json={"duration": duration}, timeout=self.action_timeout)

    # ------------------------------------------------------------------
    # LEDs / Animation
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

    async def play_animation(self, name: str) -> Dict[str, Any]:
        return await self._post("/animation", json={"name": name}, timeout=self.action_timeout)

    async def list_animations(self) -> List[str]:
        data = await self._get("/animations")
        return list(data.get("animations", []))

    # ------------------------------------------------------------------
    # Tablet
    # ------------------------------------------------------------------

    async def tablet_text(self, text: str, title: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text}
        if title:
            body["title"] = title
        return await self._post("/tablet/text", json=body)

    async def tablet_web(self, url: str) -> Dict[str, Any]:
        return await self._post("/tablet/web", json={"url": url})

    async def tablet_image(self, url: str) -> Dict[str, Any]:
        return await self._post("/tablet/image", json={"url": url})

    async def tablet_hide(self) -> Dict[str, Any]:
        return await self._post("/tablet/hide")
