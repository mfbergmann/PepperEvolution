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


def _age(seconds: float) -> str:
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    minutes = int(seconds // 60)
    return "a minute ago" if minutes == 1 else f"{minutes} minutes ago"


def _span(seconds: float) -> str:
    """'40 seconds', 'a minute', '5 minutes', 'over an hour' for the greeting prompt."""
    if seconds < 60:
        return f"{int(seconds)} seconds"
    if seconds < 120:
        return "a minute"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes"
    return "over an hour"


class AIManager:
    """Manages multi-turn AI conversations with tool calling and speech."""

    MAX_TOOL_ROUNDS = 10  # Safety limit on tool-call loops
    EVENT_MAX_AGE = 2.0  # seconds; older queued reactions are dropped
    GREET_WINDOW = 10.0  # seconds after an arrival in which a greeting may still fire (they look over, step closer)
    GREET_UNKNOWN_GAZE_DISTANCE = 1.8  # metres; this close, a person whose gaze is not known yet is greeted too
    TURN_TO_ARRIVAL_DISTANCE = 3.0  # metres; Pepper turns its head towards newcomers this close
    TURN_TO_ARRIVAL_SPEED = 0.3  # fraction of maximum head speed
    TRACKING_STIMULI = ["People", "Touch"]  # no Sound or Movement: they pull the head around an open room
    NEUTRAL_HEAD = (0.0, -18.0)  # yaw, pitch: straight ahead at face height of someone about 2 m away
    RECENTRE_AFTER = 3.0  # seconds with nobody in view before the head goes back to neutral

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
        world: Optional[Any] = None,
        greet_newcomers: bool = True,
        greet_cooldown: float = 90.0,
        greet_max_distance: float = 3.0,
        greet_quiet_after_talk: float = 30.0,
        face_tracking: bool = False,
    ):
        self.robot = robot
        self.world = world  # WorldModel: its summary goes into the state block on every turn
        self.provider = provider
        self.executor = ToolExecutor(robot)
        self.logger = logger.bind(module="AIManager")

        self.speak_responses = speak_responses
        self.tablet_subtitles = tablet_subtitles
        self.react_to_touch = react_to_touch
        self.touch_cooldown = touch_cooldown
        self.greet_newcomers = greet_newcomers  # greet someone who walks up after the room was empty
        self.greet_cooldown = greet_cooldown  # seconds between greetings
        self.greet_max_distance = greet_max_distance  # metres, for someone looking at Pepper (detection starts ~3 m)
        self.greet_quiet_after_talk = greet_quiet_after_talk  # no greeting this soon after someone spoke to Pepper
        # NAOqi face tracking (ALBasicAwareness) on while someone is in view, off while the room is empty:
        # left on, it parks the head looking down, where the camera cannot see the next person coming.
        self.face_tracking = face_tracking
        self._tracking_off = False  # we switched it off for an empty room
        self.history_turns = history_turns  # user turns kept in context
        self.image_history = image_history  # photos kept in context (older ones become text)

        self.conversation_history: List[Dict[str, Any]] = []
        self.last_photo: Optional[Photo] = None
        self._lock = asyncio.Lock()
        self._last_event_reaction: Optional[float] = None  # monotonic time; None = never (monotonic may start near 0)
        self._clock = time.monotonic  # injectable for tests
        self._last_greeting: Optional[float] = None
        self._last_talk_at: Optional[float] = None  # last user or voice turn
        self._pending_arrival: Optional[Any] = None  # (time, Arrival) waiting for the person to qualify
        self._recentre_task: Optional[asyncio.Task] = None
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
        self.recorder: Optional[Any] = None  # SessionRecorder (src/session.py): turns.jsonl and events.jsonl
        self._rec: Optional[Dict[str, Any]] = None  # the record of the turn being run

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
        heard_at: Optional[float] = None,
        open_mic: bool = False,
    ) -> Dict[str, Any]:
        """Process user input through the AI with tool calling.

        ``heard_at`` (``time.monotonic()``) is when voice input delivered the final transcript, for the session record.
        ``open_mic``: heard by the robot's microphone rather than typed or push-to-talk, so it may not be meant for
        Pepper (the addressee gate only judges these).

        Returns a dict with:
            - text: the AI's reply (animation tags stripped)
            - spoken: sentences that were sent to the robot's TTS
            - tool_calls: list of {name, input, result, ok}
            - photo: {"media_type", "base64"} of the last photo taken this turn, if any
            - model, stop_reason, usage, rounds, source, client_id
        """
        speak = self.speak_responses if speak is None else speak
        rec = self._new_record(user_input, source, heard_at)
        if source in ("user", "voice"):
            self._last_talk_at = self._clock()
            intent = match_intent(user_input)
            if intent is not None:
                # Control phrases never wait behind the model or an in-flight turn.
                result = await self._handle_intent(intent, user_input, speak, source, client_id)
                rec["intent"] = intent.name
                self._finish_record(rec, result)
                return result
        submitted = self._clock()
        async with self._lock:
            if self._last_stop_at is not None and submitted < self._last_stop_at:
                # Queued behind the turn that "stop" cancelled: the person does not want it any more.
                self.logger.info(f"[{source}] dropped after a stop: {user_input!r}")
                result = self._bare_result("", source, client_id, "cancelled")
                self._finish_record(rec, result)
                return result
            self._rec = rec
            try:
                result = await self._run_turn(user_input, speak, source, client_id)
            finally:
                self._rec = None
        self._finish_record(rec, result)
        self._after_turn(result, submitted)
        return result

    # ------------------------------------------------------------------
    # Session records (src/session.py)
    # ------------------------------------------------------------------

    def _new_record(self, text: str, source: str, heard_at: Optional[float]) -> Dict[str, Any]:
        return {
            "source": source,
            "text": text,
            "heard_at": heard_at,
            "received_at": self._clock(),
            "around": self.world.summary() if self.world is not None else "",
            "tools": [],
        }

    def _finish_record(self, rec: Dict[str, Any], result: Dict[str, Any]):
        """Write one line to turns.jsonl: what was heard, decided, done and said, with timings."""
        if self.recorder is None:
            return
        start = rec.get("heard_at") or rec["received_at"]
        first = rec.pop("first_word_at", None)
        out = {
            "source": rec["source"],
            "text": rec["text"],
            "heard": self.recorder.wall(rec.get("heard_at")),
            "received": self.recorder.wall(rec["received_at"]),
            "first_word_s": round(first - start, 2) if first is not None else None,
            "duration_s": round(self._clock() - start, 2),
            "stop_reason": result.get("stop_reason"),
            "spoken": [strip_animation_tags(s) for s in result.get("spoken") or []],
            "tools": rec.get("tools", []),
            "around": rec.get("around", ""),
        }
        for key in ("intent", "addressee", "router"):
            if key in rec:
                out[key] = rec[key]
        self.recorder.record_turn(out)

    def _event(self, kind: str, **data: Any):
        if self.recorder is not None:
            self.recorder.record_event(kind, **data)

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
        if speak and source in ("user", "voice") and self.backchannel_after > 0:
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
                if backchannel is not None and (response.text or speaker.first_text_at is not None):
                    backchannel.cancel()  # words are coming; no filler needed
                    backchannel = None
                elif backchannel is not None and response.tool_calls:
                    # The model wants to act before saying anything: say the filler now, before the robot
                    # goes quiet for the tool (an animation alone can take four seconds), not later over it.
                    backchannel.cancel()
                    backchannel = None
                    if not self._hush and not self.robot.halted:
                        await speaker.say_filler(random.choice(FILLERS))

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
                        started = self._clock()
                        outcome = await self.executor.execute(tc.name, tc.input)
                        if self._rec is not None:
                            self._rec["tools"].append(
                                {
                                    "name": tc.name,
                                    "input": tc.input,
                                    "ok": outcome.ok,
                                    "seconds": round(self._clock() - started, 2),
                                    "result": outcome.summary()[:500],
                                }
                            )
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
        if self._rec is not None and "first_word_at" not in self._rec:
            self._rec["first_word_at"] = self._clock()
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
        if event_type == "people":
            self._try_greeting()  # a pending arrival may qualify now (they looked over or came closer)
            if not data.get("count"):
                self._schedule_recentre()
            elif self._tracking_off:
                people = self.world.people if self.world is not None else []
                self._turn_towards(people[0] if people else None)  # someone back before it counted as an arrival
            return
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

    async def handle_arrival(self, arrival: Any):
        """World-model callback: someone came into view after the room was empty (``WorldModel.on_arrival``)."""
        if not self.greet_newcomers:
            return
        self._pending_arrival = (self._clock(), arrival)
        self._turn_towards(arrival.nearest)
        self._try_greeting()

    NEUTRAL_POSE_DELAY = 0.5  # seconds after a turn before the arms go back down, unless a new turn has started

    def _after_turn(self, result: Dict[str, Any], started: float):
        """Tidy up the body after a turn: arms down after gestures, eyes back on the person after a head move.

        Inline gestures (``^start(...)``) and animations can end with a hand still raised (seen on the robot after
        a greeting wave). After "look left, what's there?" the head stayed left: with face tracking off nothing
        brought it back, with it on it took 8 s (reported by a second Pepper's owner).
        """
        spoken = " ".join(result.get("spoken") or [])
        tools = [tc.get("name") for tc in result.get("tool_calls") or []]
        gestured = "^start(" in spoken or "^run(" in spoken or "play_animation" in tools
        moved_head = "move_head" in tools
        if not gestured and not moved_head:
            return

        def stopped() -> bool:  # "stop" means no more movement, not even tidying up
            return self._last_stop_at is not None and self._last_stop_at >= started

        if stopped():
            return

        async def settle():
            await asyncio.sleep(self.NEUTRAL_POSE_DELAY)
            if self.busy or self.robot.direct_commands_running or self.robot.halted or stopped():
                return  # the next turn, a command or a stop owns the body now
            if gestured:
                try:
                    await self.robot.neutral_pose()
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug(f"Could not return to the neutral pose: {exc}")
            if moved_head:
                await self._look_back()

        task = asyncio.create_task(settle(), name="after-turn")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _look_back(self):
        """After a turn that moved the head: face the nearest person again, or look out at the room."""
        people = self.world.people if self.world is not None and self.world.count else []
        self._event("look_back", people=len(people), tracking=self.face_tracking)
        try:
            if self.face_tracking and people and not self._tracking_off:
                # Re-enabling resumes tracking at once (a head move paused it for 8 s); it finds the face itself.
                await self.robot.set_awareness(
                    True, tracking_mode="Head", engagement_mode="SemiEngaged", stimuli=self.TRACKING_STIMULI
                )
            elif people and people[0].yaw is not None:
                pitch = people[0].pitch if people[0].pitch is not None else self.NEUTRAL_HEAD[1]
                await self.robot.move_head(people[0].yaw, pitch, speed=self.TURN_TO_ARRIVAL_SPEED, wait=False)
            else:
                await self.robot.move_head(*self.NEUTRAL_HEAD, speed=self.TURN_TO_ARRIVAL_SPEED, wait=False)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"Could not look back: {exc}")

    def look_at_the_room(self):
        """Head to neutral (and face tracking off) as if the room had just emptied."""
        self._schedule_recentre()

    def _schedule_recentre(self):
        """Reflex: once nobody has been in view for a few seconds, look back out at the room.

        Face tracking leaves the head wherever it lost the last person (on the robot: pointing at the
        floor), where the camera cannot see the next one coming.
        """
        if self._recentre_task is not None and not self._recentre_task.done():
            self._recentre_task.cancel()

        async def recentre():
            await asyncio.sleep(self.RECENTRE_AFTER)
            if self.world is not None and self.world.count:
                return  # someone is back
            if self.busy or self.robot.direct_commands_running or self.robot.halted:
                return
            try:
                if self.face_tracking:
                    await self.robot.set_awareness(False)
                    self._tracking_off = True
                await self.robot.move_head(*self.NEUTRAL_HEAD, speed=0.15, wait=False)
                self._event("look_at_room")
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(f"Could not recentre the head: {exc}")

        self._recentre_task = asyncio.create_task(recentre(), name="recentre-head")
        self._tasks.add(self._recentre_task)
        self._recentre_task.add_done_callback(self._tasks.discard)

    def _turn_towards(self, person: Any):
        """Reflex, no model: turn the head to a newcomer at once, so Pepper visibly notices them,
        and hand over to face tracking if it was switched off for the empty room."""
        if person is None or person.distance is None or person.distance > self.TURN_TO_ARRIVAL_DISTANCE:
            return
        if self.busy or self.robot.direct_commands_running or self.robot.halted:
            return  # a turn or a direct command may be using the head
        resume_tracking = self._tracking_off
        self._tracking_off = False
        if person.yaw is None and not resume_tracking:
            return
        pitch = person.pitch if person.pitch is not None else 0.0

        async def turn():
            try:
                if person.yaw is not None:
                    await self.robot.move_head(person.yaw, pitch, speed=self.TURN_TO_ARRIVAL_SPEED, wait=False)
                if resume_tracking:
                    await self.robot.set_awareness(
                        True, tracking_mode="Head", engagement_mode="SemiEngaged", stimuli=self.TRACKING_STIMULI
                    )
                if self.led_signals and self._led_ok:
                    await self.robot.set_eye_color("blue")  # attending, as while listening
            except Exception as exc:  # noqa: BLE001 - a missed glance is not worth an error
                self.logger.debug(f"Could not turn towards the newcomer: {exc}")

        if person.yaw is not None:
            self.logger.info(f"Turning towards a newcomer: yaw {person.yaw:.0f}, pitch {pitch:.0f}")
            self._event("turn_towards", yaw=person.yaw, pitch=pitch, distance=person.distance)
        task = asyncio.create_task(turn(), name="turn-to-arrival")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _try_greeting(self):
        """Greet a pending arrival once the nearest person is close and looking at Pepper."""
        if self._pending_arrival is None:
            return
        arrived_at, arrival = self._pending_arrival
        now = self._clock()
        if now - arrived_at > self.GREET_WINDOW:
            self._pending_arrival = None  # they never came close or looked over: a passer-by
            return
        people = self.world.people if self.world is not None else [arrival.nearest] if arrival.nearest else []
        nearest = people[0] if people else None
        if nearest is None or nearest.distance is None or nearest.distance > self.greet_max_distance:
            return
        if nearest.looking is False:
            return
        if nearest.looking is None and nearest.distance > self.GREET_UNKNOWN_GAZE_DISTANCE:
            return  # gaze not known yet: only greet someone who has clearly come up close
        self._pending_arrival = None  # decided now, whether or not we speak
        if self.busy or self.robot.direct_commands_running or self.robot.halted:
            return
        if self._last_talk_at is not None and now - self._last_talk_at < self.greet_quiet_after_talk:
            return  # someone is already talking with Pepper
        if self._last_greeting is not None and now - self._last_greeting < self.greet_cooldown:
            return
        self._last_greeting = now  # consumed even if the greeting is dropped below: never greet late
        away = "" if arrival.empty_for == float("inf") else f" Nobody had been around for {_span(arrival.empty_for)}."
        prompt = (
            f"[Sensor event] Someone just walked up to you, about {nearest.distance:.1f} m away"
            f"{' and looking at you' if nearest.looking else ''}."
            f"{away} Greet them in one short, friendly sentence, or stay quiet if a greeting doesn't fit."
        )
        self.logger.info(f"Greeting a newcomer at {nearest.distance:.1f} m")
        self._event("greeting", distance=nearest.distance, looking=nearest.looking)
        task = asyncio.create_task(self._react(prompt, now, kind="greeting"), name="greeting")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _react(self, prompt: str, scheduled_at: float, kind: str = "event"):
        # Re-check right before taking the lock: a user turn may have started in the meantime.
        # A dropped touch reaction frees its cooldown; a dropped greeting keeps it (a late greeting is worse).
        if self._lock.locked() or self.robot.direct_commands_running or self.robot.halted:
            self.logger.debug(f"Dropping {kind}: robot busy")
            if kind == "event":
                self._last_event_reaction = None
            return
        if self._clock() - scheduled_at > self.EVENT_MAX_AGE:
            self.logger.debug(f"Dropping {kind}: stale")
            if kind == "event":
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
            f"Local date and time: {now.strftime('%A %d %B %Y, %H:%M')}."
        )
        around = self.world.summary() if self.world is not None else ""
        if around:
            dynamic += f"\n{around}"
        photo_line = self._last_photo_line()
        if photo_line:
            dynamic += f"\n{photo_line}"
        return [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic},
        ]

    def _last_photo_line(self) -> str:
        """How old the last photo is and where it looked, so an old photo is not described as the view now.

        On the robot, "what do you see?" was answered from a photo taken half a minute earlier with the head
        turned left, while face tracking had since turned the head to the person on the right.
        """
        photo = getattr(self.robot, "last_photo", None)
        taken_at = getattr(photo, "taken_at", None)
        if photo is None or taken_at is None:
            return ""
        age = time.monotonic() - taken_at
        yaw = photo.head_yaw
        if yaw is None:
            where = ""
        elif yaw > 15:
            where = " with your head turned left"
        elif yaw < -15:
            where = " with your head turned right"
        else:
            where = " looking straight ahead"
        moved_at = getattr(self.robot, "last_head_move_at", None)
        moved = self.face_tracking or (moved_at is not None and moved_at > taken_at)
        line = f"Your last photo was taken {_age(age)}{where}."
        if moved:
            line += " Your head has moved since, so it does not show what is in front of you now."
        return line

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
