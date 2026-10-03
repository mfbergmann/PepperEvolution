"""
Client for Ollama's decision models (``/v1/systemone``, the TypeSafe Jev API): Nimble (text), Clef Flash (text and
images).

Fails open: on a timeout or any error it returns ``None`` and the caller behaves as if there were no judgement (Pepper
answers as it always did). Alien3 being down or busy must never make Pepper mute.
"""

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

DEFAULT_TIMEOUT = 0.8  # seconds; a judgement later than this is no longer fast enough to be worth waiting for
WARM_TIMEOUT = 120.0  # the first call after the model unloaded loads it into the GPU (about 28 s for Clef Flash)
KEEP_WARM_EVERY = 600.0  # seconds; Ollama unloads a model after its keep-alive (30 min on Alien3)


class DecisionClient:
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT, transport: Optional[Any] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self.calls = 0
        self.failures = 0
        self.last_error: Optional[str] = None
        self.logger = logger.bind(module="DecisionClient")
        self._keep_warm: Optional[asyncio.Task] = None

    async def ask(
        self,
        model: str,
        state: Any,
        questions: Dict[str, Any],
        images: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """The ``answers`` object, or ``None`` if the judgement failed or came too late."""
        body: Dict[str, Any] = {"model": model, "state": state, "questions": questions}
        if images:
            body["images"] = images
        self.calls += 1
        try:
            response = await self._http.post(
                f"{self.base_url}/v1/systemone", json=body, timeout=timeout or self.timeout
            )
            response.raise_for_status()
            answers = response.json()["answers"]
            if not isinstance(answers, dict):
                raise ValueError("no answers in the response")
            return answers
        except Exception as exc:  # noqa: BLE001 - fail open, whatever went wrong
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.logger.warning(f"Decision model {model} gave no answer ({self.last_error}); carrying on without it")
            return None

    async def warm(self, models: List[str]) -> Dict[str, bool]:
        """Load the models into the GPU so the first real judgement is fast."""
        ready = {}
        for model in models:
            answers = await self.ask(
                model, "warm up", {"ok": {"type": "noul", "instructions": "Is this a test?"}}, timeout=WARM_TIMEOUT
            )
            ready[model] = answers is not None
        self.logger.info(f"Decision models ready: {ready}")
        return ready

    def keep_warm(self, models: List[str], every: float = KEEP_WARM_EVERY):
        """Warm now and then every ``every`` seconds in the background, while the host runs."""

        async def loop():
            while True:
                await self.warm(models)
                await asyncio.sleep(every)

        if self._keep_warm is None or self._keep_warm.done():
            self._keep_warm = asyncio.create_task(loop(), name="decision-models-warm")

    def status(self) -> Dict[str, Any]:
        return {"url": self.base_url, "calls": self.calls, "failures": self.failures, "last_error": self.last_error}

    async def close(self):
        if self._keep_warm is not None:
            self._keep_warm.cancel()
        await self._http.aclose()
