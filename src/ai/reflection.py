"""
Initiative: the first slice of the reflection loop (docs/ARCHITECTURE.md, "Beyond the four layers").

Every few seconds the loop reads working memory and asks whether Pepper might do something unprompted. When it
might, the mind gets a short "[Initiative]" turn and decides, saying one short thing or nothing. There is still one
voice: the loop never speaks itself.

Two rules, deliberately few. ``consider()`` is a pure function of the world model (tracks and timeline), so it can
be replayed on recorded sessions (``scripts/replay_world.py --initiative``):

- **Lingering:** someone close (2.5 m or less), looking at Pepper, in view for 30 s or more, while nobody has spoken
  and Pepper has said nothing for a minute, there was no greeting in the last 90 s, and at most two people are in view.
  Once per visit (until the room has been empty).
- **Unanswered question:** Pepper's last sentence was a question, 20-60 s ago; nobody has said anything since (side
  talk counts as someone speaking), and someone is still in view. Once per question.

Replayed on the twelve robot sessions of 2026-10-08 (58 minutes): three initiatives, none during the two real
conversations: one "lingering" while the tester stood quietly through a load measurement, two "unanswered" during
tests where the tester moved on without answering.

Guards: at most one initiative every 3 minutes; never while a turn or a command runs, while halted, within 5 minutes
of "be quiet" or "stop", or with three or more people in view. Off by default (``INITIATIVE``): it speaks unprompted,
which is judged on the robot.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Set

LINGER_FOR = 30.0
LINGER_MAX_DISTANCE = 2.5
QUIET_FOR = 60.0  # nobody spoke and Pepper said nothing for this long
AFTER_GREETING = 90.0
QUESTION_AFTER = (20.0, 60.0)
EVERY = 180.0  # at most one initiative this often
AFTER_HUSH = 300.0  # no initiative this long after "be quiet" or "stop"
MAX_PEOPLE = 2
TICK = 5.0


@dataclass(frozen=True)
class Initiative:
    rule: str  # "lingering" | "unanswered"
    prompt: str
    key: str  # for "once per question"


def _ago(seconds: float) -> str:
    return f"{int(seconds)} seconds" if seconds < 90 else f"{int(seconds // 60)} minutes"


def consider(world: Any, now: float, done: Optional[Set[str]] = None) -> Optional[Initiative]:
    """Whether Pepper might act unprompted now, from working memory alone. ``done`` holds keys already acted on."""
    done = done or set()
    if not world.count or world.count > MAX_PEOPLE:
        return None
    timeline = world.timeline
    last_initiative = timeline.last("initiative")
    if last_initiative is not None and now - last_initiative.at < EVERY:
        return None
    hushed = any(
        e.data.get("intent") in ("quiet", "stop", "emergency_stop")
        for e in timeline.recent(kinds=["heard"], within=AFTER_HUSH, now=now)
    )
    if hushed:
        return None
    heard = timeline.last("heard")
    said = timeline.last("said")

    # unanswered question
    if said is not None and str(said.data.get("text", "")).rstrip().endswith("?"):
        age = now - said.at
        answered = heard is not None and heard.at > said.at
        key = f"unanswered:{said.data.get('text')}"
        if QUESTION_AFTER[0] <= age <= QUESTION_AFTER[1] and not answered and key not in done:
            return Initiative(
                "unanswered",
                f'[Initiative] You asked "{said.data.get("text")}" {_ago(age)} ago and nobody has answered; someone '
                "is still in view. You may try once more in other, simpler words, or let it go.",
                key,
            )

    # lingering
    quiet_since = max((e.at for e in (heard, said) if e is not None), default=None)
    if quiet_since is not None and now - quiet_since < QUIET_FOR:
        return None
    greeting = timeline.last("greeting")
    if greeting is not None and now - greeting.at < AFTER_GREETING:
        return None
    for track in world.tracker.present():
        if track.looking is not True or track.distance is None or track.distance > LINGER_MAX_DISTANCE:
            continue
        if now - track.first_seen < LINGER_FOR:
            continue
        # once per visit: the detector starts new tracks for the same person, so key on when the room last filled
        arrived = timeline.last("arrived")
        key = f"lingering:{arrived.at if arrived is not None else 'start'}"
        if key in done:
            continue
        return Initiative(
            "lingering",
            f"[Initiative] Someone about {track.distance:.1f} m away has been looking at you for "
            f"{_ago(now - track.first_seen)} without saying anything. If it feels right, say one short, friendly "
            "thing to invite them in; or stay quiet and let them be.",
            key,
        )
    return None


class Reflection:
    """Runs ``consider()`` every TICK seconds and hands an initiative to the mind as an event turn."""

    def __init__(self, world: Any, manager: Any, tick: float = TICK):
        self.world = world
        self.manager = manager
        self.tick = tick
        self.done: Set[str] = set()
        self.fired = 0
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="reflection")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self):
        while True:
            await asyncio.sleep(self.tick)
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001 - initiative must never break the host
                self.manager.logger.debug(f"reflection step failed: {exc}")

    async def step(self) -> Optional[Initiative]:
        manager, robot = self.manager, self.manager.robot
        if manager.busy or robot.direct_commands_running or robot.halted:
            return None
        now = self.world.now()
        initiative = consider(self.world, now, self.done)
        if initiative is None:
            return None
        self.done.add(initiative.key)
        self.fired += 1
        self.world.timeline.add("initiative", now, rule=initiative.rule)
        manager.logger.info(f"Initiative ({initiative.rule})")
        await manager.initiate(initiative.prompt, initiative.rule)
        return initiative
