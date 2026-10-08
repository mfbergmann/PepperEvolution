"""
Client for Ollama's decision models (``/v1/systemone``, the TypeSafe Jev API): Nimble (text), Clef Flash (text and
images).

Fails open: on a timeout or any error it returns ``None`` and the caller behaves as if there were no judgement (Pepper
answers as it always did). Alien3 being down or busy must never make Pepper mute.

When the server cannot be reached (or several judgements in a row fail), the client treats the models as down: it says
so once, answers ``None`` at once for ``RETRY_AFTER`` seconds, then tries again. On the robot (2026-10-08) a stopped
Ollama logged a warning per camera frame, every second; an unreachable host (a connect timeout) would also have cost
every turn the full timeout.
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import httpx
from loguru import logger

DEFAULT_TIMEOUT = 0.8  # seconds; a judgement later than this is no longer fast enough to be worth waiting for
WARM_TIMEOUT = 120.0  # the first call after the model unloaded loads it into the GPU (about 28 s for Clef Flash)
KEEP_WARM_EVERY = 600.0  # seconds; Ollama unloads a model after its keep-alive (30 min on Alien3)
RETRY_AFTER = 30.0  # seconds to treat the models as down after the server could not be reached
DOWN_AFTER_FAILURES = 3  # consecutive failures of any kind (timeouts, errors) that also count as down


class DecisionClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._clock = clock
        self.calls = 0
        self.failures = 0
        self.skipped = 0  # calls answered None at once while the models were down
        self.last_error: Optional[str] = None
        self.down_until: Optional[float] = None
        self._failures_in_a_row = 0
        self.logger = logger.bind(module="DecisionClient")
        self._keep_warm: Optional[asyncio.Task] = None

    @property
    def down(self) -> bool:
        return self.down_until is not None and self._clock() < self.down_until

    async def ask(
        self,
        model: str,
        state: Any,
        questions: Dict[str, Any],
        images: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """The ``answers`` object, or ``None`` if the judgement failed or came too late."""
        if self.down:
            self.skipped += 1
            return None
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
        except Exception as exc:  # noqa: BLE001 - fail open, whatever went wrong
            self._failed(model, exc)
            return None
        if self.down_until is not None:
            self.logger.info(f"Decision models at {self.base_url} answering again")
            self.down_until = None
        self._failures_in_a_row = 0
        return answers

    def _failed(self, model: str, exc: Exception):
        self.failures += 1
        self._failures_in_a_row += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        unreachable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
        if unreachable or self._failures_in_a_row >= DOWN_AFTER_FAILURES:
            if self.down_until is None:  # say it once per outage, not once per frame
                self.logger.warning(
                    f"Decision models at {self.base_url} not answering ({self.last_error}); carrying on without "
                    f"them, trying again every {RETRY_AFTER:.0f} s"
                )
            self.down_until = self._clock() + RETRY_AFTER
        else:
            self.logger.warning(f"Decision model {model} gave no answer ({self.last_error}); carrying on without it")

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
        return {
            "url": self.base_url,
            "calls": self.calls,
            "failures": self.failures,
            "skipped": self.skipped,
            "down": self.down,
            "last_error": self.last_error,
        }

    async def close(self):
        if self._keep_warm is not None:
            self._keep_warm.cancel()
        await self._http.aclose()
