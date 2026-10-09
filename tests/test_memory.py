"""
Long-term memory (src/memory): the store's rules, consent from what was actually heard, the tools, episodes.
"""

from datetime import datetime, timedelta

import pytest

from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall
from src.ai.tools import MEMORY_TOOL_NAMES
from src.memory import ConsentRequired, Memory, MemoryStore, says_yes
from src.memory.store import RETENTION_DAYS
from src.world import WorldModel
from src.world.observations import HEARD, SAID


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "pepper.sqlite"))
    yield s
    s.close()


def heard(world, text, via="voice"):
    world.note(HEARD, "heard", text=text, via=via, addressed=True)


def said(world, text):
    world.note(SAID, "said", text=text)


class TestStore:
    def test_nobody_without_consent(self, store):
        with pytest.raises(ConsentRequired):
            store.add_person("Mira", "")
        with pytest.raises(ConsentRequired):
            store.add_fact("likes jazz", person_id=42)  # no such (consenting) person
        assert store.counts() == {"people": 0, "facts": 0, "episodes": 0}

    def test_names_match_loosely(self, store):
        store.add_person("Mira  Chen", '"yes please"')
        assert [p.name for p in store.find_people("mira chen")] == ["Mira Chen"]
        assert store.find_people("Mira") == []

    def test_forget_takes_facts_and_episodes(self, store):
        mira = store.add_person("Mira", '"remember me"')
        store.add_fact("likes jazz", person_id=mira.id)
        store.add_fact("the lab moves to room 210 in November")
        now = datetime.now()
        episode = store.add_episode("s1", now - timedelta(minutes=10), now, "talked with Mira.", {}, [mira.id])
        assert store.episode_people(episode.id) == [mira.id]
        assert store.forget(mira.id) is True
        assert store.find_people("Mira") == [] and store.search("jazz") == []
        assert store.episode_people(episode.id) == []  # the episode stays, without her
        assert [f.text for f in store.search("room")] == ["the lab moves to room 210 in November"]

    def test_search_finds_words_about_a_person(self, store):
        mira = store.add_person("Mira", '"yes"')
        store.add_fact("Mira plays the cello in a quartet", person_id=mira.id)
        store.add_fact("The quartet rehearses on Thursdays")
        assert len(store.search("quartet")) == 2
        assert [f.text for f in store.search("quartet", person_id=mira.id)] == ["Mira plays the cello in a quartet"]
        assert store.search("?!") == []

    def test_plain_search_without_fts(self, store):
        store.fts = False
        store.add_fact("The quartet rehearses on Thursdays")
        assert [f.text for f in store.search("rehearses")] == ["The quartet rehearses on Thursdays"]

    def test_people_expire_unless_renewed(self, tmp_path):
        now = [datetime(2026, 10, 8, 12, 0)]
        store = MemoryStore(str(tmp_path / "m.sqlite"), now=lambda: now[0])
        mira = store.add_person("Mira", '"yes"')
        sam = store.add_person("Sam", '"yes"')
        store.add_fact("likes jazz", person_id=mira.id)
        store.add_fact("a lab fact")
        now[0] += timedelta(days=RETENTION_DAYS - 1)
        store.renew(sam.id, '"yes, keep me"')
        assert store.sweep() == {"people": 0, "facts": 0, "episodes": 0}
        now[0] += timedelta(days=2)
        assert store.sweep()["people"] == 1
        assert [p.name for p in store.people()] == ["Sam"]
        assert store.search("jazz") == [] and len(store.search("lab")) == 1  # lab facts do not expire
        store.close()


class TestConsent:
    def test_says_yes(self):
        for text in ["Yes", "yeah sure", "Okay, please do", "Please remember me", "Sure, go ahead"]:
            assert says_yes(text), text
        for text in ["No thanks", "Please don't", "I'd rather not", "Maybe later", "What?"]:
            assert not says_yes(text), text

    def setup(self, store):
        clock = Clock()
        world = WorldModel(clock=clock)
        return Memory(store, world=world, session="test"), world, clock

    def test_refused_without_a_yes(self, store):
        memory, world, _ = self.setup(store)
        heard(world, "I'm Mira")
        result = memory.remember_person("Mira")
        assert result["ok"] is False and "Ask first" in result["error"]
        assert store.counts()["people"] == 0

    def test_a_yes_to_peppers_question(self, store):
        memory, world, clock = self.setup(store)
        heard(world, "I'm Mira")
        said(world, "Nice to meet you, Mira! Shall I remember you next time?")
        clock.t += 3
        heard(world, "Yeah, sure")
        result = memory.remember_person("Mira")
        assert result["ok"] and result["new"]
        person = store.find_people("Mira")[0]
        assert person.consent_note.startswith('"Yeah, sure" (voice,')

    def test_an_okay_about_something_else_is_not_consent(self, store):
        memory, world, clock = self.setup(store)
        heard(world, "Okay, turn left")
        clock.t += 2
        heard(world, "I'm Mira")
        assert memory.remember_person("Mira")["ok"] is False

    def test_a_no_is_respected_and_old_yeses_expire(self, store):
        memory, world, clock = self.setup(store)
        said(world, "Shall I remember you next time?")
        heard(world, "No thanks")
        assert memory.remember_person("Mira")["ok"] is False
        heard(world, "Please remember me, I'm Mira")
        clock.t += 200  # outside the two-minute window
        assert memory.remember_person("Mira")["ok"] is False

    def test_asking_to_be_remembered_is_consent(self, store):
        memory, world, _ = self.setup(store)
        heard(world, "Please remember me, I'm Mira")
        assert memory.remember_person("Mira")["ok"] is True

    def test_returning_person_renews(self, store):
        memory, world, _ = self.setup(store)
        heard(world, "Remember me please")
        memory.remember_person("Mira")
        memory.remember("Mira plays the cello", about="Mira")
        heard(world, "Yes, remember me again")
        result = memory.remember_person("Mira")
        assert result["renewed"] and result["facts"] == ["Mira plays the cello"]
        assert store.counts()["people"] == 1


class TestConsentByVoice:
    """The recogniser cuts at pauses (#26), so the name and the request often arrive as separate finals."""

    def setup(self, store):
        clock = Clock()
        world = WorldModel(clock=clock)
        return Memory(store, world=world, session="test"), world, clock

    def test_request_first_then_the_name(self, store):
        memory, world, clock = self.setup(store)
        heard(world, "Please remember me")
        assert memory.remember_person("")["ok"] is False  # the mind tried without a name: it must ask
        said(world, "Of course! What's your name?")
        clock.t += 4
        heard(world, "Alex")
        assert memory.remember_person("Alex")["ok"] is True

    def test_name_first_then_the_request(self, store):
        memory, world, clock = self.setup(store)
        heard(world, "I'm Alex")
        clock.t += 2
        heard(world, "you can remember me")
        assert memory.remember_person("Alex")["ok"] is True

    def test_side_talk_is_never_consent(self, store):
        memory, world, clock = self.setup(store)
        said(world, "Shall I remember you next time?")
        clock.t += 2
        world.note(HEARD, "heard", text="Yeah sure, let's get coffee", via="voice", addressed=False)
        assert memory.remember_person("Alex")["ok"] is False


class TestTools:
    def setup(self, store):
        clock = Clock()
        world = WorldModel(clock=clock)
        return Memory(store, world=world, session="test"), world, clock

    def test_facts_only_about_people_who_agreed(self, store):
        memory, world, _ = self.setup(store)
        refused = memory.remember("Sam is on holiday", about="Sam")
        assert refused["ok"] is False and "agreed" in refused["error"]
        assert memory.remember("The lab is closed on Monday")["ok"] is True

    def test_recall_here_links_the_person(self, store):
        memory, world, clock = self.setup(store)
        heard(world, "Please remember me")
        memory.remember_person("Mira")
        memory.remember("Mira plays the cello", about="Mira")
        fresh = Memory(store, world=world, session="next")  # the next day
        assert fresh.here_line() == ""
        result = fresh.recall(about="mira", here=True)
        assert result["person"]["facts"] == ["Mira plays the cello"]
        assert "Mira" in fresh.here_line()
        assert fresh.recall(about="Nobody")["about"].startswith("You don't remember")

    def test_recall_has_working_memory(self, store):
        memory, world, clock = self.setup(store)
        world.update_people(1, [{"id": 1, "distance": 1.2, "looking": True}])
        heard(world, "What's the weather like")
        said(world, "Sunny and cold.")
        clock.t += 30
        now = memory.recall()["now"]
        assert now["around"].startswith("Around you: one person")
        assert '30 s ago: heard "What\'s the weather like"' in now["recently"]
        assert '30 s ago: you said "Sunny and cold."' in now["recently"]

    def test_forget_person(self, store):
        memory, world, _ = self.setup(store)
        heard(world, "Remember me")
        memory.remember_person("Mira")
        assert memory.forget_person("Mira")["forgotten"] is True
        assert memory.here_line() == "" and store.counts()["people"] == 0
        assert memory.forget_person("Mira")["forgotten"] is False

    def test_link_ends_when_the_room_has_been_empty(self, store):
        memory, world, clock = self.setup(store)
        world.update_people(1, [{"id": 1}])
        heard(world, "Remember me")
        memory.remember_person("Mira")
        world.update_people(0, [])
        clock.t += 30
        assert "Mira" in memory.here_line()
        clock.t += 31
        assert memory.here_line() == ""

    def test_episode_names_only_people_who_agreed(self, store):
        memory, world, _ = self.setup(store)
        heard(world, "Remember me")
        memory.remember_person("Mira")
        episode = memory.end_session({"turns": 12, "minutes": 9.5, "most_people": 2, "greetings": 1})
        assert episode.summary == "12 turns over 9.5 minutes; talked with Mira; up to 2 people in view; 1 greetings."
        assert store.episode_people(episode.id) == [store.find_people("Mira")[0].id]
        assert Memory(store, world=world).end_session({"turns": 0}) is None  # nothing happened: no episode


def tool_then_words(name, args, words="Done."):
    """A model that calls one tool, then says something."""
    calls = {"n": 0}

    async def chat(messages, tools=None, system=None, on_text=None):
        calls["n"] += 1
        chat.tools = tools
        chat.system = system
        if calls["n"] == 1:
            return AIResponse(tool_calls=[ToolCall(id="t1", name=name, input=args)], stop_reason="tool_use")
        if on_text is not None:
            await on_text(words)
        return AIResponse(text=words, stop_reason="end_turn")

    return chat


class TestInTheManager:
    async def test_memory_tools_only_when_memory_is_on(self, mock_robot, mock_ai_provider, store):
        mock_ai_provider.chat.side_effect = tool_then_words("recall", {})
        plain = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        assert not set(MEMORY_TOOL_NAMES) & {t["name"] for t in plain.tools}
        assert "Memory\n" not in plain.system_prompt
        world = WorldModel()
        manager = AIManager(
            mock_robot,
            mock_ai_provider,
            speak_responses=False,
            tablet_subtitles=False,
            world=world,
            memory=Memory(store, world=world),
        )
        assert set(MEMORY_TOOL_NAMES) <= {t["name"] for t in manager.tools}
        assert "Shall I remember you next time?" in manager.system_prompt

    async def test_a_spoken_yes_lets_the_mind_remember(self, mock_robot, mock_ai_provider, store):
        world = WorldModel()
        memory = Memory(store, world=world)
        manager = AIManager(
            mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False, world=world, memory=memory
        )
        mock_ai_provider.chat.side_effect = tool_then_words("remember_person", {"name": "Mira"})
        await manager.process_user_input("I'm Mira", source="voice")
        assert store.counts()["people"] == 0  # the mind tried before asking: refused
        said(world, "Shall I remember you next time?")
        mock_ai_provider.chat.side_effect = tool_then_words("remember_person", {"name": "Mira"}, "Got it, Mira!")
        await manager.process_user_input("Yes please", source="voice")
        assert [p.name for p in store.people()] == ["Mira"]
        assert "You know who you are talking with: Mira" in manager._build_system_prompt()[1]["text"]
        assert manager.stats["turns"] == 2

    async def test_heard_and_said_reach_the_timeline(self, mock_robot, mock_ai_provider):
        world = WorldModel()
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False, world=world)

        async def chat(messages, tools=None, system=None, on_text=None):
            await on_text("Hello there.")
            return AIResponse(text="Hello there.", stop_reason="end_turn")

        mock_ai_provider.chat.side_effect = chat
        await manager.process_user_input("Hi Pepper", source="voice")
        kinds = [(e.kind, e.data.get("text")) for e in world.timeline.recent()]
        assert ("heard", "Hi Pepper") in kinds and ("said", "Hello there.") in kinds
