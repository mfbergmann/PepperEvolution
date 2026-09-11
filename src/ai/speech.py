"""
Turn streamed model text into speech, one sentence at a time.

The model's reply is spoken as it is generated: text deltas are buffered,
split at sentence boundaries, cleaned of markdown, and queued to the robot
in order. ``drain()`` waits until the robot has finished everything queued so
far (used before physical tool calls); ``finish()`` flushes the remainder and
waits for silence.
"""

import asyncio
import re
import time
from typing import Any, Awaitable, Callable, List, Optional, Set

from loguru import logger

# Sentence ends: terminal punctuation (optionally followed by a closing quote/bracket) then whitespace,
# or any newline. "3.5 metres" does not split because no whitespace follows the dot.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?…])["\')\]]?\s+|\n+')
# Common abbreviations that end with a period but do not end a sentence.
_ABBREVIATION = re.compile(r"(?:^|\s)(?:Dr|Mr|Mrs|Ms|Prof|St|Sr|Jr|vs|etc|e\.g|i\.e|approx|Mt|No)\.$", re.IGNORECASE)
_ANIMATION_TAG = re.compile(r"\^(?:start|run|wait|stop|runTag|stopTag)\([^)]*\)\s*")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
# A model occasionally writes its tool call as XML text instead of a tool_use block; never say that aloud.
_TOOL_XML = re.compile(r"</?(?:invoke|function_calls|parameter|antml:[a-z_]+)\b", re.IGNORECASE)
_TOOL_XML_LINE = re.compile(r"^\s*<[^>]*>.*$", re.MULTILINE)
# Underscores inside words (Hey_1, snake_case) are not markdown; only strip them at word edges.
_MD_MARKS = re.compile(r"\*\*|\*|__|(?<!\w)_|_(?!\w)|`+|~~")
_MD_HEADING = re.compile(r"(?:^|(?<=\s))#{1,6}(?=\s|$)", re.MULTILINE)
_MD_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"  # symbols & pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027bf"  # misc symbols, dingbats
    "\U0001f1e6-\U0001f1ff"  # flags
    "️‍"  # variation selector, ZWJ
    "]+"
)
_WS = re.compile(r"[ \t]+")


def looks_like_tool_xml(text: str) -> bool:
    """True if the text contains XML-style tool-call markup."""
    return bool(_TOOL_XML.search(text or ""))


def strip_tool_xml(text: str) -> str:
    """Remove XML-style tool-call lines from model text."""
    if not looks_like_tool_xml(text):
        return text
    return _WS.sub(" ", _TOOL_XML_LINE.sub("", text)).strip()


def strip_animation_tags(text: str) -> str:
    """Remove ^start(...) style tags for on-screen display."""
    return _WS.sub(" ", _ANIMATION_TAG.sub("", text)).strip()


def clean_for_speech(text: str) -> str:
    """Make model text safe and pleasant for TTS. Keeps animation tags (the robot understands them)."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = _EMOJI.sub("", text)
    text = text.replace("\r", "")
    text = _WS.sub(" ", text)
    return text.strip()


class SentenceSplitter:
    """Accumulates streamed text and yields complete sentences."""

    def __init__(self, min_chars: int = 3):
        self.min_chars = min_chars
        self._buffer = ""

    def feed(self, delta: str) -> List[str]:
        self._buffer += delta
        sentences: List[str] = []
        start = 0
        while True:
            match = _SENTENCE_BOUNDARY.search(self._buffer, start)
            if not match:
                break
            head = self._buffer[: match.end()].strip()
            if len(head) >= self.min_chars and not _ABBREVIATION.search(head):
                sentences.append(head)
                self._buffer = self._buffer[match.end() :]
                start = 0
            else:
                # Too short, or ends with an abbreviation: keep going past this boundary.
                start = match.end()
        return sentences

    def flush(self) -> Optional[str]:
        rest = self._buffer.strip()
        self._buffer = ""
        return rest or None


SpeakFn = Callable[[str], Awaitable[Any]]
SentenceCallback = Callable[[str], Awaitable[None]]


class SpeechStreamer:
    """Feeds streamed text to the robot sentence by sentence, in order."""

    def __init__(
        self,
        speak: SpeakFn,
        enabled: bool = True,
        on_sentence: Optional[SentenceCallback] = None,
        gate: Optional[Callable[[], bool]] = None,
    ):
        self.speak = speak
        self.enabled = enabled
        self.on_sentence = on_sentence
        self.gate = gate  # returns False when the robot must stay silent (e.g. after an emergency stop)
        self.splitter = SentenceSplitter()
        self.spoken: List[str] = []
        self.errors: List[str] = []
        self.suppressed: List[str] = []
        self.fillers: List[str] = []  # backchannel phrases said while waiting for the model
        self.first_enqueued_at: Optional[float] = None  # when the first real sentence was queued
        self.first_text_at: Optional[float] = None  # when the first text delta arrived from the model
        self.suppress_repeats = False  # set after a phantom tool call so the retry does not re-say its preamble
        self._seen: Set[str] = set()
        self._queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self.logger = logger.bind(module="SpeechStreamer")

    async def on_text(self, delta: str):
        if self.first_text_at is None and delta.strip():
            self.first_text_at = time.monotonic()
        for sentence in self.splitter.feed(delta):
            await self._enqueue(sentence)

    async def say_filler(self, text: str):
        """Queue a short backchannel phrase (does not count as the reply's first sentence)."""
        if self.first_text_at is None and self.first_enqueued_at is None and self.enabled:
            self.fillers.append(text)
            await self._enqueue(text, filler=True)

    async def _enqueue(self, sentence: str, filler: bool = False):
        cleaned = clean_for_speech(sentence)
        if not cleaned:
            return
        if not filler and self.first_enqueued_at is None:
            self.first_enqueued_at = time.monotonic()
        if looks_like_tool_xml(cleaned):
            self.logger.warning(f"Suppressed tool-call XML from speech: {cleaned[:80]!r}")
            self.suppressed.append(cleaned)
            return
        if self.suppress_repeats and cleaned in self._seen:
            self.logger.debug(f"Skipping already-spoken sentence after retry: {cleaned[:60]!r}")
            return
        self._seen.add(cleaned)
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="speech-streamer")
        await self._queue.put(cleaned)

    async def _run(self):
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    break
                if self.on_sentence is not None:
                    try:
                        await self.on_sentence(item)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.debug(f"on_sentence callback failed: {exc}")
                if not self.enabled:
                    continue
                if self.gate is not None and not self.gate():
                    self.suppressed.append(item)
                    continue
                try:
                    await self.speak(item)
                    self.spoken.append(item)
                except Exception as exc:  # noqa: BLE001
                    self.logger.error(f"speech failed: {exc}")
                    self.errors.append(str(exc))
            finally:
                self._queue.task_done()

    async def flush(self):
        """Queue whatever partial sentence is buffered (call at the end of a model message)."""
        rest = self.splitter.flush()
        if rest:
            await self._enqueue(rest)

    async def drain(self):
        """Wait until every queued sentence has actually been spoken (the robot is silent)."""
        if self._worker is None or self._worker.done():
            return
        join = asyncio.ensure_future(self._queue.join())
        done, _ = await asyncio.wait({join, self._worker}, return_when=asyncio.FIRST_COMPLETED)
        if join not in done:
            join.cancel()

    async def finish(self) -> List[str]:
        """Flush any partial sentence and wait for the robot to finish speaking."""
        await self.flush()
        if self._worker is not None:
            await self._queue.put(None)
            try:
                await self._worker
            finally:
                self._worker = None
        return self.spoken

    async def cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
