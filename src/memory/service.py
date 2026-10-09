"""
Memory service: what the mind's memory tools do, over working memory (the world model) and the long-term store.

The tools are ``remember_person``, ``remember``, ``recall`` and ``forget_person`` (src/ai/tools.py). The rules
that protect people are enforced here, not left to the model's judgement:

- Someone is stored only when they said yes. The consent is taken from what Pepper actually heard in the last two
  minutes (the world model's timeline), not from the model's word for it; the words are kept as the consent note.
- Facts about a person are only kept for someone stored that way.
- "Forget me" deletes at once.

People named in this session ("I'm Mira", or after being remembered) are linked to the conversation, so the state
line can say who Pepper is talking with and episodes can record who was there. A link ends when nobody has been in
view for a minute.
"""

import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from ..world.observations import MIND
from .store import ConsentRequired, MemoryStore, Person

CONSENT_WINDOW = 120.0  # seconds: the yes must have been heard this recently
LINK_ENDS_AFTER_EMPTY = 60.0  # seconds with nobody in view before a named person is no longer "here"

_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|okay|ok|of course|please do|go ahead|that's fine|that is fine|fine by me|"
    r"i agree|i'd like that|i would like that|absolutely|definitely|remember me|you can remember|"
    r"please remember)\b",
    re.I,
)
_NO = re.compile(r"\b(no|nope|don't|do not|rather not|not now)\b", re.I)


def says_yes(text: str) -> bool:
    """A plain yes to being remembered: an agreeing word and no refusal ("no thanks", "please don't")."""
    return bool(_YES.search(text)) and not _NO.search(text)


class Memory:
    def __init__(
        self,
        store: MemoryStore,
        world: Any = None,
        session: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.world = world
        self.session = session or datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.started_at = datetime.now()
        self._clock = clock
        self._here: Dict[int, float] = {}  # person id -> when linked to this conversation
        self.met: Dict[int, str] = {}  # everyone linked this session (for the episode)
        self.logger = logger.bind(module="Memory")

    # -- tools ------------------------------------------------------------------------------------------------------

    def remember_person(self, name: str) -> Dict[str, Any]:
        """Store (or renew) the person Pepper is talking with, if they said yes in the last two minutes."""
        name = " ".join(str(name or "").split())
        if not name:
            return {"ok": False, "error": "Say whose name to remember."}
        consent = self._recent_yes()
        if consent is None:
            return {
                "ok": False,
                "error": 'They have not said yes yet. Ask first, for example "Shall I remember you next time?", '
                "and call this again only after they agree. If they say no, that's fine: don't store anything.",
            }
        known = self.store.find_people(name)
        if len(known) == 1:
            person = known[0]
            self.store.renew(person.id, consent)
            self._link(person)
            return {"ok": True, "person": person.name, "renewed": True, "facts": self._facts(person.id)}
        person = self.store.add_person(name, consent)
        self._link(person)
        return {
            "ok": True,
            "person": person.name,
            "new": True,
            "note": "Stored with their consent. They can ask you to forget them at any time.",
        }

    def remember(self, text: str, about: Optional[str] = None) -> Dict[str, Any]:
        """Keep a fact, about the lab (``about`` empty) or about a person who agreed to be remembered."""
        text = " ".join(str(text or "").split())
        if not text:
            return {"ok": False, "error": "Nothing to remember."}
        person_id = None
        if about:
            person = self._resolve(about)
            if person is None:
                return {
                    "ok": False,
                    "error": f"You don't remember anyone called {about}. Facts about a person are only kept for "
                    "people who agreed to be remembered: ask them first (remember_person).",
                }
            person_id = person.id
        try:
            fact = self.store.add_fact(text, person_id=person_id, source="mind", session=self.session)
        except ConsentRequired as exc:
            return {"ok": False, "error": str(exc)}
        if self.world is not None:
            self.world.note(MIND, "remembered", about=about or "the lab")
        return {"ok": True, "remembered": fact.text, "about": about or "the lab"}

    def recall(self, query: str = "", about: Optional[str] = None, here: bool = False) -> Dict[str, Any]:
        """What Pepper knows: working memory (who is around, what just happened) and the long-term store."""
        out: Dict[str, Any] = {}
        if self.world is not None:
            out["now"] = self.world.recall()
        person = None
        if about:
            matches = self.store.find_people(about)
            if not matches:
                out["about"] = f"You don't remember anyone called {about}."
            elif len(matches) > 1 and not here:
                out["about"] = f"You remember {len(matches)} people called {about}; ask which one they mean."
            else:
                person = matches[-1] if len(matches) > 1 else matches[0]
                if here:
                    self.store.seen(person.id)
                    self._link(person)
                out["person"] = self._describe(person)
        if query:
            facts = self.store.search(query, person_id=person.id if person else None)
            out["facts"] = [f.text for f in facts]
        elif person is None and not about:
            out["lab"] = [f.text for f in self.store.facts_about(None, limit=10)]
        episodes = self.store.episodes(person_id=person.id if person else None, limit=3)
        if episodes:
            out["past_sessions"] = [f"{e.started_at[:10]}: {e.summary}" for e in episodes]
        return out

    def forget_person(self, name: str) -> Dict[str, Any]:
        """Delete someone at once: their record, facts and place in past sessions."""
        name = " ".join(str(name or "").split())
        matches = self.store.find_people(name) if name else []
        if not matches:
            return {"ok": True, "forgotten": False, "note": f"You had nothing stored about anyone called {name}."}
        if len(matches) > 1:
            here = [p for p in matches if p.id in self._here]
            if len(here) != 1:
                return {
                    "ok": False,
                    "error": f"You remember {len(matches)} people called {name} and can't tell which one this "
                    "is; the lab team can remove the right one (scripts/memory_admin.py).",
                }
            matches = here
        person = matches[0]
        self.store.forget(person.id)
        self._here.pop(person.id, None)
        self.met.pop(person.id, None)
        if self.world is not None:
            self.world.note(MIND, "forgot_person")
        return {"ok": True, "forgotten": True, "note": f"Everything you had stored about {person.name} is deleted."}

    # -- for the state block and the end of the session --------------------------------------------------------------

    def here_line(self) -> str:
        """Who Pepper is talking with, by name, when they told it this session; empty otherwise."""
        self._expire_links()
        if not self._here:
            return ""
        names = [self.met[pid] for pid in sorted(self._here, key=lambda p: self._here[p])]
        return (
            f"You know who you are talking with: {', '.join(names)} (you remember them; " "recall has what you know)."
        )

    def end_session(self, stats: Dict[str, Any]) -> Optional[Any]:
        """Write a short episode for this session: who (by id, only people who agreed), when, and counts."""
        if not stats.get("turns") and not self.met:
            return None
        names = sorted(self.met.values())
        parts = [f"{stats.get('turns', 0)} turns over {stats.get('minutes', 0)} minutes"]
        if names:
            parts.append("talked with " + ", ".join(names))
        if stats.get("most_people"):
            parts.append(f"up to {stats['most_people']} people in view")
        if stats.get("greetings"):
            parts.append(f"{stats['greetings']} greetings")
        summary = "; ".join(parts) + "."
        episode = self.store.add_episode(
            self.session, self.started_at, datetime.now(), summary, stats, people=list(self.met)
        )
        self.logger.info(f"Session episode stored: {summary}")
        return episode

    # -- internals ---------------------------------------------------------------------------------------------------

    def _recent_yes(self) -> Optional[str]:
        """The consent, quoted, or None: an agreeing utterance in the consent window that either asks to be
        remembered itself ("please remember me") or answers Pepper's question about remembering them (a bare "okay"
        said about something else must not count)."""
        if self.world is None:
            return None
        now = self.world.now()
        events = self.world.timeline.recent(kinds=["heard", "said"], within=CONSENT_WINDOW, now=now)
        asked_at = None  # when Pepper last asked about remembering
        for event in events:
            text = str(event.data.get("text", ""))
            if event.kind == "said" and "remember" in text.lower() and "?" in text:
                asked_at = event.at
        for event in reversed(events):
            if event.kind != "heard":
                continue
            text = str(event.data.get("text", ""))
            if not says_yes(text):
                continue
            if "remember" in text.lower() or (asked_at is not None and event.at >= asked_at):
                return f'"{text}" ({event.data.get("via", "voice")}, {datetime.now().strftime("%Y-%m-%d %H:%M")})'
        return None

    def _resolve(self, name: str) -> Optional[Person]:
        matches = self.store.find_people(name)
        if len(matches) == 1:
            return matches[0]
        here = [p for p in matches if p.id in self._here]
        return here[0] if len(here) == 1 else None

    def _link(self, person: Person):
        self._here[person.id] = self._clock()
        self.met[person.id] = person.name

    def _expire_links(self):
        world = self.world
        if world is None or not self._here:
            return
        empty_since = getattr(world, "empty_since", None)
        if world.count == 0 and empty_since is not None and world.now() - empty_since >= LINK_ENDS_AFTER_EMPTY:
            self._here.clear()

    def _facts(self, person_id: int) -> List[str]:
        return [f.text for f in self.store.facts_about(person_id, limit=10)]

    def _describe(self, person: Person) -> Dict[str, Any]:
        return {
            "name": person.name,
            "first_met": person.created_at[:10],
            "last_seen": (person.last_seen_at or "")[:10],
            "facts": self._facts(person.id),
        }
