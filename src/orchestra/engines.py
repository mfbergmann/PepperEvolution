"""
Engines and the data each may receive (docs/ORCHESTRATOR.md, docs/DATA_FLOWS.md).

Default-deny: an engine that is not registered accepts nothing, and a registered engine accepts only the data classes
it lists. Every engine's ``destination`` names its row in DATA_FLOWS.md; a test fails if the two drift apart.

Data classes:

- ``text``: words heard, Pepper's replies, state lines.
- ``image``: camera frames and photos.
- ``audio``: recordings of voices.
- ``identity``: names, voice and face matches, recalled facts about people.
- presence of the session the data comes from: ``solo`` (the tester alone with Pepper), ``unknown``, ``others``.
"""

from dataclasses import dataclass
from typing import FrozenSet, Iterable, Optional
from urllib.parse import urlparse

TEXT, IMAGE, AUDIO, IDENTITY = "text", "image", "audio", "identity"
SOLO, UNKNOWN, OTHERS = "solo", "unknown", "others"
PRESENCE = (SOLO, UNKNOWN, OTHERS)
ANY_PRESENCE = frozenset(PRESENCE)

# where an engine runs
THIS_HOST, LAB, HOSTED_CA, HOSTED_US = "this host", "lab", "hosted, Canada", "hosted, US"


@dataclass(frozen=True)
class Engine:
    name: str
    role: str  # rule, judge, reader, mind
    runs: str  # THIS_HOST, LAB, HOSTED_CA, HOSTED_US
    destination: str  # the row of docs/DATA_FLOWS.md this engine's data goes to
    accepts: FrozenSet[str]  # data classes
    presence: FrozenSet[str]  # sessions it may receive data from

    def allows(self, classes: Iterable[str], presence: str) -> bool:
        return presence in self.presence and set(classes) <= self.accepts


REGISTRY = {
    e.name: e
    for e in (
        Engine(
            "rules", "rule", THIS_HOST, "**tripl-desktop**", frozenset({TEXT, IMAGE, AUDIO, IDENTITY}), ANY_PRESENCE
        ),
        Engine(
            "local-ollama", "mind", THIS_HOST, "**tripl-desktop**", frozenset({TEXT, IMAGE, IDENTITY}), ANY_PRESENCE
        ),
        # the lab's GPU machine: decision models (:11435), speech and voice readings (:11436), a mind (:11434) when
        # tried. "We can use Alien3 for processing, but I wouldn't trust safe storage there" (tester, 2026-11-05)
        Engine("alien3", "judge", LAB, "**Alien3**", frozenset({TEXT, IMAGE, AUDIO, IDENTITY}), ANY_PRESENCE),
        # the tester's decision (2026-09): words, photos and recalled facts go to Anthropic for every answered turn
        Engine("anthropic", "mind", HOSTED_US, "**Anthropic API**", frozenset({TEXT, IMAGE, IDENTITY}), ANY_PRESENCE),
        # testing only (2026-11-10): text from sessions where the tester was alone; Brine's "no sensitive data" term
        Engine("brine", "mind", HOSTED_CA, "**SHARCNET Brine**", frozenset({TEXT}), frozenset({SOLO})),
    )
}

HOSTS = {
    "brine.sharcnet.ca": "brine",
    "alien3": "alien3",
    "localhost": "local-ollama",
    "127.0.0.1": "local-ollama",
}
MIND_SENDS = (TEXT, IMAGE, IDENTITY)  # a live mind gets the words, the photos it takes and the names it is told


def engine(name: Optional[str]) -> Optional[Engine]:
    return REGISTRY.get(name or "")


def allowed(name: Optional[str], classes: Iterable[str], presence: str = UNKNOWN) -> bool:
    """May data of these classes, from a session of this presence, go to this engine? Unknown engines: never."""
    found = engine(name)
    return found is not None and found.allows(classes, presence)


def for_model(model: str, base_url: Optional[str] = None) -> Optional[str]:
    """The engine a configured mind (AI_MODEL, OLLAMA_URL) sends to, or None when it is not registered."""
    if model.startswith("claude"):
        return "anthropic"
    if base_url:
        host = (urlparse(base_url if "//" in base_url else "//" + base_url).hostname or "").lower()
        if host in HOSTS:
            return HOSTS[host]
        if host.startswith("alien3."):  # its tailnet name
            return "alien3"
    return None  # e.g. OpenAI: not in DATA_FLOWS.md, so not cleared


class PolicyError(ValueError):
    """Data would go where it is not cleared to go."""
