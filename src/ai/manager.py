"""
AI manager - orchestrates multi-turn tool-calling conversations.

Sends user messages to the AI provider, speaks the reply as it streams in,
executes any tool calls, feeds results back, and loops until the AI produces
a final text response. Also turns robot sensor events into short reactions.
"""

import asyncio
import random
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from loguru import logger

from ..pepper.robot import PepperRobot, Photo
from .intents import Intent, IntentExecutor, match_intent
from .models import ERROR_TEXT, SYSTEM_PROMPT, AIProvider, AIResponse
from .speech import SpeechStreamer, looks_like_tool_xml, strip_animation_tags, strip_tool_xml
from .tool_executor import ToolExecutor
from .tools import TOOLS

ResponseCallback = Callable[[Dict[str, Any]], Awaitable[None]]
PartialCallback = Callable[[str], Awaitable[None]]

MAX_ROUNDS_TEXT = "I got a bit carried away there. What would you like next?"
HALTED_TEXT = "Emergency stop pressed. I'm resting until someone wakes me up."
ABORTED_TEXT = "Okay, stopping."
FILLERS = ["Hmm.", "Let me think.", "One moment.", "Let me see."]
# Eye colours that show what Pepper is doing; "idle" restores whatever colour was chosen last.
LED_STATES = {"listening": "blue", "thinking": "purple", "speaking": "white", "idle": None}
TRUNCATED_TOOL_TEXT = (
    "Not executed: your reply was cut off by the output token limit before this tool call was complete. "
    "Reply again more briefly."
)

EVENT_MESSAGES = {
    ("touch", "head_front"): "Someone touched the front of your head.",
    ("touch", "head_middle"): "Someone touched the top of your head.",
    ("touch", "head_rear"): "Someone touched the back of your head.",
    ("touch", "hand_left"): "Someone touched your left hand.",
    ("touch", "hand_right"): "Someone touched your right hand.",
    ("bumper", "front_left"): "Your front-left bumper hit something.",
    ("bumper", "front_right"): "Your front-right bumper hit something.",
    ("bumper", "back"): "Your back bumper hit something.",
}


class AIManager:
    """Manages multi-turn AI conversations with tool calling and speech."""

    MAX_TOOL_ROUNDS = 10  # Safety limit on tool-call loops
    EVENT_MAX_AGE = 2.0  # seconds; older queued reactions are dropped

    def __init__(
        self,
        robot: PepperRobot,
        provider: AIProvider,
        speak_responses: bool = True,
        tablet_subtitles: bool = True,
        react_to_touch: bool = True,
        touch_cooldown: float = 8.0,
        history_turns: int = 20,
        image_history: int = 2,
        led_signals: bool = True,
        backchannel_after: float = 2.0,
    ):
        self.robot = robot
        self.provider = provider
        self.executor = ToolExecutor(robot)
        self.logger = logger.bind(module="AIManager")

        self.speak_responses = speak_responses
        self.tablet_subtitles = tablet_subtitles
        self.react_to_touch = react_to_touch
        self.touch_cooldown = touch_cooldown
        self.history_turns = history_turns  # user turns kept in context
        self.image_history = image_history  # photos kept in context (older ones become text)

        self.conversation_history: List[Dict[str, Any]] = []
        self.last_photo: Optional[Photo] = None
        self._lock = asyncio.Lock()
        self._last_event_reaction: Optional[float] = None  # monotonic time; None = never (monotonic may start near 0)
        self._clock = time.monotonic  # injectable for tests
        self._tablet_ok = True
        self._tasks: Set[asyncio.Task] = set()
        self.intents = IntentExecutor(robot)
        self.led_signals = led_signals  # eye colour shows listening / thinking / speaking
        self.backchannel_after = backchannel_after  # seconds of silence before a filler ("Hmm.") is spoken
        self._led_ok = True
        self._hush = False  # "be quiet": suppress the rest of the current turn's speech
        self._abort = False  # "stop": cancel the current turn's remaining tool calls
        self._chat_task: Optional[asyncio.Task] = None  # the model call in flight, cancelled by "stop"
        self._last_stop_at: Optional[float] = None  # turns submitted before this are dropped, not run
        self._eye_color_at_start: Optional[str] = None  # to leave a colour the model chose mid-turn alone
        self._spoke_this_turn = False
        self._response_callbacks: List[ResponseCallback] = []
        self._partial_callbacks: List[PartialCallback] = []

    # Backwards-compatible alias (older code/tests used context_window = pairs kept)
    @property
    def context_window(self) -> int:
        return self.history_turns

    @context_window.setter
    def context_window(self, value: int):
        self.history_turns = value

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def process_user_input(
        self,
        user_input: str,
        speak: Optional[bool] = None,
        source: str = "user",
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process user input through the AI with tool calling.

        Returns a dict with:
            - text: the AI's reply (animation tags stripped)
            - spoken: sentences that were sent to the robot's TTS
            - tool_calls: list of {name, input, result, ok}
            - photo: {"media_type", "base64"} of the last photo taken this turn, if any
            - model, stop_reason, usage, rounds, source, client_id
        """
        speak = self.speak_responses if speak is None else speak
        if source in ("user", "voice"):
            intent = match_intent(user_input)
            if intent is not None:
                # Control phrases never wait behind the model or an in-flight turn.
                return await self._handle_intent(intent, user_input, speak, source, client_id)
        submitted = self._clock()
        async with self._lock:
            if self._last_stop_at is not None and submitted < self._last_stop_at:
                # Queued behind the turn that "stop" cancelled: the person does not want it any more.
                self.logger.info(f"[{source}] dropped after a stop: {user_input!r}")
                return self._bare_result("", source, client_id, "cancelled")
            return await self._run_turn(user_input, speak, source, client_id)

    async def _handle_intent(
        self, intent: Intent, user_input: str, speak: bool, source: str, client_id: Optional[str]
    ) -> Dict[str, Any]:
        self.logger.info(f"[{source}] {user_input!r} -> intent {intent.name}")
        if intent.hushes:
            self._hush = True
        if intent.aborts_turn:
            self._abort = True
            self._last_stop_at = self._clock()
            chat = self._chat_task
            if chat is not None and not chat.done():
                chat.cancel()  # do not wait for the model to finish a reply nobody wants
        outcome = await self.intents.execute(intent)
        spoken: List[str] = []
        ack = intent.ack if outcome.get("ok") else "Sorry, that didn't work."
        if speak and ack and not self.robot.halted:
            try:
                await self.robot.speak(ack, animated=False)
                spoken.append(ack)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"Intent acknowledgement failed: {exc}")
        result = self._bare_result(ack or "", source, client_id, "intent", spoken=spoken, intent=intent.name)
        if not outcome.get("ok"):
            result["error"] = outcome.get("error")
        if not self.busy:
            await self._signal("idle")  # e.g. the "listening" colour set by voice input
        for cb in self._response_callbacks:
            try:
                await cb(result)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(f"response callback failed: {exc}")
        return result

    @staticmethod
    def _bare_result(text: str, source: str, client_id: Optional[str], stop_reason: str, **extra: Any) -> Dict:
        result: Dict[str, Any] = {
            "text": text,
            "spoken": [],
            "tool_calls": [],
            "photo": None,
            "model": "",
            "stop_reason": stop_reason,
            "usage": {},
            "rounds": 0,
            "source": source,
            "client_id": client_id,
        }
        result.update(extra)
        return result

    async def _run_turn(self, user_input: str, speak: bool, source: str, client_id: Optional[str]) -> Dict[str, Any]:
        self.logger.info(f"[{source}] {user_input}")
        self.conversation_history.append({"role": "user", "content": user_input})
        self._trim_history()

        self._hush = False
        self._abort = False
        self._spoke_this_turn = False
        self._eye_color_at_start = self.robot.last_eye_color
        speaker = SpeechStreamer(
            self._speak_sentence,
            enabled=speak,
            on_sentence=self._on_sentence,
            gate=lambda: not self.robot.halted and not self._hush,
        )
        await self._signal("thinking")
        backchannel: Optional[asyncio.Task] = None
        if speak and source == "user" and self.backchannel_after > 0:
            backchannel = asyncio.create_task(self._backchannel(speaker), name="backchannel")
        all_tool_calls: List[Dict[str, Any]] = []
        photo: Optional[Photo] = None
        text_parts: List[str] = []
        response: Optional[AIResponse] = None
        rounds = 0
        phantom_retries = 0

        async def say(text: str):
            text_parts.append(text)
            await speaker.on_text(text + " ")

        try:
            for rounds in range(1, self.MAX_TOOL_ROUNDS + 1):
                if self._abort:
                    await say(ABORTED_TEXT)
                    break
                chat = asyncio.ensure_future(
                    self.provider.chat(
                        messages=self.conversation_history,
                        tools=TOOLS,
                        system=self._build_system_prompt(),
                        on_text=speaker.on_text,
                    )
                )
                self._chat_task = chat
                try:
                    response = await chat
                except asyncio.CancelledError:
                    if not self._abort:
                        chat.cancel()  # we are being cancelled (shutdown): take the model call down too
                        raise
                    self.logger.info("Model call cancelled by a stop intent")
                    if self._is_user_text(self.conversation_history[-1]):
                        self.conversation_history.pop()  # nothing answered it; keep the history valid
                    else:
                        self.conversation_history.append({"role": "assistant", "content": ABORTED_TEXT})
                    text_parts.append(ABORTED_TEXT)
                    break
                finally:
                    self._chat_task = None
                await speaker.flush()  # each model message ends a sentence, even mid-tool-loop
                if backchannel is not None:
                    backchannel.cancel()  # the model has answered; a filler now would talk over a tool
                    backchannel = None

                if response.is_error:
                    self._drop_dangling_user_message()
                    await say(response.text)
                    break

                if response.stop_reason == "refusal":
                    await say(response.text)
                    self.conversation_history.append({"role": "assistant", "content": response.text})
                    break

                if response.stop_reason == "max_tokens" and not response.text and not response.tool_calls:
                    self.logger.warning("Empty response at max_tokens (thinking used the whole budget)")
                    self._drop_dangling_user_message()
                    await say(ERROR_TEXT)
                    break

                phantom = not response.tool_calls and (
                    response.stop_reason == "tool_use" or looks_like_tool_xml(response.text)
                )
                if phantom:
                    # The model wrote its tool call as text. Nothing was added to history; ask again once.
                    phantom_retries += 1
                    self.logger.warning(f"Phantom tool call (attempt {phantom_retries}): {response.text[:120]!r}")
                    if phantom_retries <= 1:
                        speaker.suppress_repeats = True
                        continue
                    await say(ERROR_TEXT)
                    break
                if response.text and looks_like_tool_xml(response.text):
                    response.text = strip_tool_xml(response.text)

                if response.text:
                    text_parts.append(response.text)

                if not response.tool_calls:
                    if response.text:
                        self.conversation_history.append({"role": "assistant", "content": response.text})
                    break

                # Assistant turn: replay the provider's blocks verbatim (thinking blocks must be kept for
                # the tool round on Claude); fall back to a hand-built list for providers without them.
                assistant_content: List[Dict[str, Any]] = list(response.content) if response.content else []
                if not assistant_content:
                    if response.text:
                        assistant_content.append({"type": "text", "text": response.text})
                    for tc in response.tool_calls:
                        assistant_content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
                self.conversation_history.append({"role": "assistant", "content": assistant_content})

                if response.stop_reason == "max_tokens":
                    # Tool inputs may be truncated; never act on them.
                    self.logger.warning("Response truncated by max_tokens mid tool call; not executing")
                    results = [
                        {"type": "tool_result", "tool_use_id": tc.id, "is_error": True, "content": TRUNCATED_TOOL_TEXT}
                        for tc in response.tool_calls
                    ]
                    self.conversation_history.append({"role": "user", "content": results})
                    continue

                # Let the robot finish saying the preamble before it starts moving or looking.
                await speaker.drain()

                # Execute tools (in order - they move a physical robot) and collect results
                tool_results: List[Dict[str, Any]] = []
                for tc in response.tool_calls:
                    if self.robot.halted:
                        outcome = self.executor.halted_outcome()
                    elif self._abort:
                        outcome = self.executor.aborted_outcome()
                    else:
                        outcome = await self.executor.execute(tc.name, tc.input)
                    tool_results.append(outcome.tool_result(tc.id))
                    all_tool_calls.append(
                        {"name": tc.name, "input": tc.input, "result": outcome.summary(), "ok": outcome.ok}
                    )
                    if outcome.image is not None:
                        photo = outcome.image
                        self.last_photo = photo
                self.conversation_history.append({"role": "user", "content": tool_results})

                if self.robot.halted:
                    self.logger.warning("Robot halted by emergency stop; ending the turn")
                    await say(HALTED_TEXT)
                    self.conversation_history.append({"role": "assistant", "content": HALTED_TEXT})
                    break
                if self._abort:
                    # The stop intent already said "Okay." and hushed the streamer; this only closes the
                    # history and the reply text.
                    self.logger.info("Turn aborted by a stop intent")
                    await say(ABORTED_TEXT)
                    self.conversation_history.append({"role": "assistant", "content": ABORTED_TEXT})
                    break
            else:
                self.logger.warning("Hit max tool-call rounds")
                await say(MAX_ROUNDS_TEXT)
                self.conversation_history.append({"role": "assistant", "content": MAX_ROUNDS_TEXT})
        finally:
            if backchannel is not None:
                backchannel.cancel()
            spoken = await speaker.finish()
            await self._signal("idle")

        self._prune_images()
        text = strip_animation_tags("\n".join(p for p in text_parts if p))
        result = {
            "text": text,
            "spoken": spoken,
            "tool_calls": all_tool_calls,
            "photo": {"media_type": photo.media_type, "base64": photo.base64_data} if photo else None,
            "model": response.model if response else "",
            "stop_reason": response.stop_reason if response else "",
            "usage": response.usage if response else {},
            "rounds": rounds,
            "source": source,
            "client_id": client_id,
        }
        for cb in self._response_callbacks:
            try:
                await cb(result)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(f"response callback failed: {exc}")
        return result

    def _drop_dangling_user_message(self):
        """Remove the user message we just appended if nothing answered it (keeps history valid)."""
        if self.conversation_history and self._is_user_text(self.conversation_history[-1]):
            self.conversation_history.pop()

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    async def _speak_sentence(self, sentence: str):
        await self.robot.speak(sentence, animated=True)

    async def _backchannel(self, speaker: SpeechStreamer):
        """Say a short filler if the model has not produced any text after backchannel_after seconds."""
        try:
            await asyncio.sleep(self.backchannel_after)
        except asyncio.CancelledError:
            return
        if speaker.first_text_at is None and not self._hush and not self.robot.halted:
            await speaker.say_filler(random.choice(FILLERS))

    async def _signal(self, state: str):
        """Show what Pepper is doing with its eye colour (best effort; off after the first failure)."""
        if not self.led_signals or not self._led_ok or self.robot.halted:
            return
        color = LED_STATES.get(state)
        if color is None:  # idle: restore the colour the model or the user chose, if any
            color = self.robot.last_eye_color or "white"
        try:
            await self.robot.bridge.set_eye_leds(color=color)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"Eye LED state signals disabled after error: {exc}")
            self._led_ok = False

    async def signal_state(self, state: str):
        """Show an external state (``listening``/``idle``) on the eyes when no turn is running."""
        if self.busy:
            return
        await self._signal(state)

    async def _on_sentence(self, sentence: str):
        if not self._spoke_this_turn:
            self._spoke_this_turn = True
            if not self._hush and self.robot.last_eye_color == self._eye_color_at_start:
                await self._signal("speaking")  # unless the model just chose a colour: leave that alone
        display = strip_animation_tags(sentence)
        for cb in self._partial_callbacks:
            try:
                await cb(display)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(f"partial callback failed: {exc}")
        if self.tablet_subtitles and self._tablet_ok and display:
            try:
                await self.robot.tablet_text(display)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"Tablet subtitles disabled after error: {exc}")
                self._tablet_ok = False

    # ------------------------------------------------------------------
    # Sensor events -> reactions
    # ------------------------------------------------------------------

    async def handle_event(self, event_type: str, data: Dict[str, Any]):
        """React to touch/bumper events with a short spoken response (rate-limited)."""
        if not self.react_to_touch:
            return
        if event_type == "touch" and not data.get("touched"):
            return
        if event_type == "bumper" and not data.get("pressed"):
            return
        message = EVENT_MESSAGES.get((event_type, data.get("sensor", "")))
        if not message:
            return
        now = self._clock()
        if self.busy or self.robot.direct_commands_running or self.robot.halted:
            return
        if self._last_event_reaction is not None and now - self._last_event_reaction < self.touch_cooldown:
            return
        self._last_event_reaction = now
        prompt = f"[Sensor event] {message} React in one short sentence, or stay quiet if it doesn't warrant a reply."
        task = asyncio.create_task(self._react(prompt, now), name="event-reaction")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _react(self, prompt: str, scheduled_at: float):
        # Re-check right before taking the lock: a user turn may have started in the meantime.
        if self._lock.locked() or self.robot.direct_commands_running or self.robot.halted:
            self.logger.debug("Dropping sensor reaction: robot busy")
            self._last_event_reaction = None
            return
        if self._clock() - scheduled_at > self.EVENT_MAX_AGE:
            self.logger.debug("Dropping sensor reaction: stale")
            self._last_event_reaction = None
            return
        try:
            await self.process_user_input(prompt, source="event")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Sensor reaction failed: {exc}")

    # ------------------------------------------------------------------
    # Prompt / history management
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> List[Dict[str, Any]]:
        """Static prompt (cached) + a small dynamic state block."""
        state = self.robot.get_state()
        now = datetime.now()
        charging = " (charging)" if state.charging else ""
        awake = "awake" if state.awake else ("asleep, motors off" if state.awake is False else "unknown")
        dynamic = (
            f"Current state: battery {state.battery_level:.0f}%{charging}; posture {state.posture}; "
            f"motors {awake}; autonomous life {state.autonomous_life}; voice language {state.language}. "
            f"Local time: {now.strftime('%A %H:%M')}."
        )
        return [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic},
        ]

    def _trim_history(self):
        """Keep the last N user turns, cutting only at real user messages so tool pairs stay intact."""
        starts = [i for i, m in enumerate(self.conversation_history) if self._is_user_text(m)]
        if len(starts) > self.history_turns:
            cut = starts[-self.history_turns]
            self.conversation_history = self.conversation_history[cut:]

    def _prune_images(self):
        """Replace all but the most recent photos with a text placeholder to bound context size."""
        seen = 0
        for msg in reversed(self.conversation_history):
            content = msg.get("content")
            if msg.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") != "tool_result" or not isinstance(block.get("content"), list):
                    continue
                if any(b.get("type") == "image" for b in block["content"]):
                    seen += 1
                    if seen > self.image_history:
                        block["content"] = [
                            b if b.get("type") != "image" else {"type": "text", "text": "[earlier photo removed]"}
                            for b in block["content"]
                        ]

    @staticmethod
    def _is_user_text(message: Dict[str, Any]) -> bool:
        return message.get("role") == "user" and isinstance(message.get("content"), str)

    # ------------------------------------------------------------------
    # Callbacks / utility
    # ------------------------------------------------------------------

    def on_response(self, callback: ResponseCallback):
        self._response_callbacks.append(callback)

    def on_partial(self, callback: PartialCallback):
        """Called with each sentence as it is spoken (for live UI updates)."""
        self._partial_callbacks.append(callback)

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        return [self._display_message(m) for m in self.conversation_history]

    @staticmethod
    def _display_message(message: Dict[str, Any]) -> Dict[str, Any]:
        """Copy of a message with image data elided (for the history API)."""
        content = message.get("content")
        if not isinstance(content, list):
            return dict(message)
        out = []
        for block in content:
            if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                block = dict(block)
                block["content"] = [
                    b if b.get("type") != "image" else {"type": "image", "elided": True} for b in block["content"]
                ]
            out.append(block)
        return {**message, "content": out}

    def clear_conversation_history(self):
        self.conversation_history.clear()
