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

# 2026-11-19: on Alien3 (32 GB, about 8 GB taken by the speech server) Nimble (9.9 GB at its 8k context) and Clef Flash
# (11.3 GB at 16k) no longer fit side by side, and Ollama keeps a margin: each call to the other model evicted the one
# loaded, every load outlasted the 0.8 s timeout, and the client counted the models down for the whole session, so the
# addressee gate and the router never ran. Copies with smaller contexts (``nimble-2k``, ``clef-flash-4k``: 8.2 and
# 9.2 GB; the largest prompt is about 650 tokens, and the answers were identical) fit together. ``models`` maps the
# names the callers use to the names served (``DECIDE_MODELS``), so the callers keep one name each.
DOWN_AFTER_FAILURES = 3  # consecutive failures of any kind (timeouts, errors) that also count as down


class DecisionClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
        models: Optional[Dict[str, str]] = None,
        servers: Optional[Dict[str, str]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        # DECIDE_SERVERS: a model asked for -> the server that has it, when one Ollama per model is run (each then
        # keeps its own model loaded, and one scheduler cannot evict the other's: docs/SETUP_PROFILES.md)
        self.servers = {k: v.rstrip("/") for k, v in (servers or {}).items()}
        self.models = dict(models or {})  # the name a caller asks for -> the name the server has
        self.identity: Optional[Dict[str, Any]] = None  # server version and model digests, after the first warm-up
        self.on_identified: Optional[Callable[[Dict[str, Any]], None]] = None  # main.py: into session.json
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

    def url_for(self, model: str) -> str:
        """The server for a model asked for by its caller's name (``DECIDE_SERVERS``, else ``DECIDE_URL``)."""
        return self.servers.get(model, self.base_url)

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
        url = self.url_for(model)
        model = self.models.get(model, model)
        body: Dict[str, Any] = {"model": model, "state": state, "questions": questions}
        if images:
            body["images"] = images
        self.calls += 1
        try:
            response = await self._http.post(f"{url}/v1/systemone", json=body, timeout=timeout or self.timeout)
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
        self.last_error = self._error_text(exc)
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

    @staticmethod
    def _error_text(exc: Exception) -> str:
        """The error, with the server's own reason when it gave one. On 2026-11-16 Ollama 0.40.16 refused the stored
        models with "downloaded in a format that is no longer supported"; the log said only "400 Bad Request"."""
        text = f"{type(exc).__name__}: {exc}".split("\n")[0]
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                reason = response.json().get("error")
            except Exception:  # noqa: BLE001
                reason = (response.text or "")[:200]
            if reason:
                text += f" ({reason})"
        return text

    async def warm(self, models: List[str]) -> Dict[str, bool]:
        """Load the models into the GPU so the first real judgement is fast. The first time, also say which server
        and model builds answer (``identity``, kept in the session record), and say loudly when one does not."""
        ready = {}
        for model in models:
            answers = await self.ask(
                model, "warm up", {"ok": {"type": "noul", "instructions": "Is this a test?"}}, timeout=WARM_TIMEOUT
            )
            ready[model] = answers is not None
        self.logger.info(f"Decision models ready: {ready}")
        first = self.identity is None
        if first:
            self.identity = await self._identify(models)
            self.logger.info(f"Decision models at {self.base_url}: {self.identity}")
            if not all(ready.values()):
                self.logger.error(
                    f"Decision models not working at start-up ({self.last_error}): Pepper runs without its fast "
                    "judgements (addressee gate, router, camera). After an Ollama upgrade, see docs/SETUP_PROFILES.md, "
                    '"Upgrading the decision models"'
                )
            if self.on_identified is not None:
                try:
                    self.on_identified({**self.identity, "ready": ready})
                except Exception as exc:  # noqa: BLE001 - a record, never in the way
                    self.logger.debug(f"on_identified failed: {exc}")
        await self._check_fit([m for m, ok in ready.items() if ok])
        return ready

    async def _identify(self, models: List[str]) -> Dict[str, Any]:
        """The server's version and each served model's digest (Ollama's ``/api/version`` and ``/api/tags``): which
        weights made a session's judgements, since a re-download can bring different weights under the same name."""
        out: Dict[str, Any] = {"server": None, "models": {}}
        versions: Dict[str, Any] = {}
        tags: Dict[str, Dict[str, str]] = {}
        for url in sorted({self.url_for(m) for m in models} | {self.base_url}):
            try:
                versions[url] = (await self._http.get(f"{url}/api/version", timeout=5.0)).json().get("version")
            except Exception:  # noqa: BLE001 - only a record
                versions[url] = None
            try:
                listed = (await self._http.get(f"{url}/api/tags", timeout=5.0)).json().get("models", [])
                tags[url] = {m.get("name", ""): m.get("digest", "") for m in listed}
            except Exception:  # noqa: BLE001
                tags[url] = {}
        out["server"] = versions.get(self.base_url)
        for model in models:
            url, served = self.url_for(model), self.models.get(model, model)
            name = served if ":" in served else f"{served}:latest"
            entry = {"served": served, "digest": (tags[url].get(name) or "")[:12] or None}
            if url != self.base_url:
                entry.update(url=url, server=versions[url])
            out["models"][model] = entry
        return out

    async def _check_fit(self, models: List[str]):
        """Warn when the warmed models are not all loaded at once (Ollama's ``/api/ps``): they then evict each other
        and every judgement times out. Silent when the server has no such endpoint."""
        if len(models) < 2:
            return
        loaded = set()
        for url in {self.url_for(m) for m in models}:
            try:
                response = await self._http.get(f"{url}/api/ps", timeout=5.0)
                response.raise_for_status()
                loaded |= {(url, m.get("name", "").split(":")[0]) for m in response.json().get("models", [])}
            except Exception:  # noqa: BLE001 - only a diagnostic
                return
        missing = [
            self.models.get(m, m)
            for m in models
            if (self.url_for(m), self.models.get(m, m).split(":")[0]) not in loaded
        ]
        if missing:
            self.logger.warning(
                f"Decision models do not fit on the GPU together ({', '.join(missing)} unloaded after the others "
                f"loaded): judgements will time out. Use smaller-context copies (DECIDE_MODELS, docs/SETUP_PROFILES.md)"
            )

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
