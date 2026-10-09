"""
Tests for situation judgements (src/decide): the client fails open, the addressee gate in the manager.
"""

import json

import httpx

from src.ai.manager import AIManager
from src.decide import DecisionClient, addressed
from src.decide.addressee import build_state
from src.world import WorldModel


def client_answering(handler, timeout=0.8):
    return DecisionClient("http://alien3:11435/", timeout=timeout, transport=httpx.MockTransport(handler))


def noul(p, key="to_pepper"):
    return lambda request: httpx.Response(200, json={"answers": {key: {"type": "noul", "noul": p}}})


class TestClient:
    async def test_answers_and_request_shape(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return noul(0.9)(request)

        client = client_answering(handler)
        answers = await client.ask("nimble", "state", {"q": {"type": "noul", "instructions": "?"}}, images=["abc"])
        assert answers["to_pepper"]["noul"] == 0.9
        assert seen["url"] == "http://alien3:11435/v1/systemone"
        assert seen["body"]["model"] == "nimble" and seen["body"]["images"] == ["abc"]

    async def test_fails_open_on_errors(self):
        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        for handler in (
            timeout,
            lambda r: httpx.Response(500, text="model not found"),
            lambda r: httpx.Response(200, text="not json"),
            lambda r: httpx.Response(200, json={"something": "else"}),
        ):
            client = client_answering(handler)
            assert await client.ask("nimble", "s", {}) is None
            assert client.failures == 1 and client.last_error

    async def test_unreachable_server_is_skipped_until_the_retry(self):
        now = [0.0]
        tries = []

        def refused(request):
            tries.append(now[0])
            raise httpx.ConnectError("All connection attempts failed", request=request)

        client = DecisionClient("http://alien3:11435", transport=httpx.MockTransport(refused), clock=lambda: now[0])
        assert await client.ask("clef-flash", "s", {}) is None and client.down
        for t in (1.0, 2.0, 29.0):  # a camera frame a second: answered at once, nothing sent, nothing logged
            now[0] = t
            assert await client.ask("clef-flash", "s", {}) is None
        assert tries == [0.0] and client.skipped == 3
        now[0] = 31.0
        assert await client.ask("nimble", "s", {}) is None
        assert tries == [0.0, 31.0] and client.status()["down"] is True

    async def test_back_after_the_outage(self):
        now = [0.0]
        up = [False]

        def handler(request):
            if not up[0]:
                raise httpx.ConnectError("refused", request=request)
            return noul(0.9)(request)

        client = DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler), clock=lambda: now[0])
        await client.ask("nimble", "s", {})
        up[0], now[0] = True, 40.0
        assert (await client.ask("nimble", "s", {}))["to_pepper"]["noul"] == 0.9
        assert not client.down and client.down_until is None

    async def test_one_slow_answer_is_not_an_outage(self):
        slow = [True]

        def handler(request):
            if slow[0]:
                raise httpx.ReadTimeout("slow", request=request)
            return noul(0.9)(request)

        client = client_answering(handler)
        await client.ask("nimble", "s", {})
        await client.ask("nimble", "s", {})
        assert not client.down
        slow[0] = False
        assert await client.ask("nimble", "s", {}) is not None
        slow[0] = True
        for _ in range(3):
            await client.ask("nimble", "s", {})
        assert client.down  # three in a row: treat as down

    async def test_warm_loads_each_model(self):
        models = []

        def handler(request):
            models.append(json.loads(request.content)["model"])
            return noul(1.0, "ok")(request)

        ready = await client_answering(handler).warm(["nimble", "clef-flash"])
        assert models == ["nimble", "clef-flash"] and ready == {"nimble": True, "clef-flash": True}


class TestAddressee:
    def test_state_is_the_benchmarked_wording(self):
        state = build_state(["a", "b", "c", "d"], "Turn left")
        assert "Recent things said near Pepper, oldest first:\n- b\n- c\n- d\n" in state
        assert state.endswith('Just said: "Turn left"')
        assert "(nothing yet)" in build_state([], "hello")

    def test_threshold_rule(self):
        assert addressed(None, 0.6, False, 0.4) is True  # no judgement: answer, as before
        assert addressed(0.5, 0.6, False, 0.4) is False
        assert addressed(0.5, 0.6, True, 0.4) is True  # someone looking at Pepper lowers the bar
        assert addressed(0.3, 0.6, True, 0.4) is False


async def gated_manager(mock_robot, mock_ai_provider, p, people=()):
    world = WorldModel()
    await world.handle_event("sensors", {"people_count": len(people), "people": list(people)})
    manager = AIManager(
        mock_robot,
        mock_ai_provider,
        speak_responses=False,
        tablet_subtitles=False,
        world=world,
        decider=client_answering(noul(p) if p is not None else (lambda r: httpx.Response(503))),
    )
    return manager


class TestGateInTheManager:
    async def test_side_talk_gets_no_reply(self, mock_robot, mock_ai_provider):
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.07)
        result = await manager.process_user_input("Pepper's basically an AI model", source="voice", open_mic=True)
        assert result["stop_reason"] == "not_addressed"
        mock_ai_provider.chat.assert_not_called()
        assert manager._last_talk_at is None  # side talk does not hold off greetings
        assert list(manager._heard) == ["Pepper's basically an AI model"]  # but it is context for the next one

    async def test_meant_for_pepper_is_answered(self, mock_robot, mock_ai_provider):
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.95)
        result = await manager.process_user_input("Can you spin a circle", source="voice", open_mic=True)
        assert result["stop_reason"] != "not_addressed"
        mock_ai_provider.chat.assert_called()

    async def test_someone_looking_lowers_the_bar(self, mock_robot, mock_ai_provider):
        person = {"id": 1, "distance": 1.2, "looking": True}
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.5, people=[person])
        result = await manager.process_user_input("Emer, can you come to me", source="voice", open_mic=True)
        assert result["stop_reason"] != "not_addressed"

    async def test_model_down_means_answer_as_before(self, mock_robot, mock_ai_provider):
        manager = await gated_manager(mock_robot, mock_ai_provider, None)
        result = await manager.process_user_input("Hello Pepper", source="voice", open_mic=True)
        assert result["stop_reason"] != "not_addressed"

    async def test_stop_and_typed_and_push_to_talk_skip_the_gate(self, mock_robot, mock_ai_provider):
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.01)
        asked = []

        async def ask(model, state, questions, images=None, timeout=None):
            asked.append(next(iter(questions)))
            return {"action": {"choice": "talk", "probabilities": {"talk": 0.9}}}

        manager.decider.ask = ask
        stop = await manager.process_user_input("Pepper, stop", source="voice", open_mic=True)
        assert stop["intent"] == "stop" and asked == []  # the exact-phrase path comes before any judgement
        typed = await manager.process_user_input("what's the capital of France?", source="user")
        ptt = await manager.process_user_input("tell me a joke", source="voice", open_mic=False)
        assert typed["stop_reason"] != "not_addressed" and ptt["stop_reason"] != "not_addressed"
        assert "to_pepper" not in asked  # only the router judged them, not the addressee gate


class TestAlone:
    """Side talk needs someone to talk to (robot 2026-10-08: alone with Pepper, 12 of 38 replies went unanswered)."""

    async def test_most_in_view_remembers_a_crowd_for_a_while(self):
        now = [0.0]
        world = WorldModel(clock=lambda: now[0])
        assert world.most_in_view(60) == 0
        two = [{"id": 1}, {"id": 2}]
        world.update_people(2, two)
        now[0] = 10.0
        world.update_people(1, two[:1])
        assert world.most_in_view(60) == 2
        now[0] = 75.0
        assert world.most_in_view(60) == 1

    async def test_alone_in_a_conversation_is_answered(self, mock_robot, mock_ai_provider):
        person = {"id": 1, "distance": 1.4, "looking": True}
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.061, people=[person])
        manager._last_answered_at = manager._clock()  # "Am I facing you now?" ... "Yeah, pretty good, good enough"
        rec = {}
        assert await manager._meant_for_pepper([], "Yeah, pretty good good enough", rec) is True
        assert rec["addressee"]["alone"] is True and rec["addressee"]["p"] == 0.061

    async def test_alone_but_not_in_a_conversation_still_judged(self, mock_robot, mock_ai_provider):
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.07)
        result = await manager.process_user_input("That", source="voice", open_mic=True)
        assert result["stop_reason"] == "not_addressed"

    async def test_someone_else_seen_recently_keeps_the_gate(self, mock_robot, mock_ai_provider):
        people = [{"id": 1, "distance": 1.4, "looking": True}, {"id": 2, "distance": 2.0, "looking": False}]
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.061, people=people)
        manager.world.update_people(1, people[:1])  # the second visitor turned away: the detector lost them
        manager._last_answered_at = manager._clock()
        rec = {}
        assert await manager._meant_for_pepper([], "Is actually a good analogy", rec) is False
        assert rec["addressee"]["alone"] is False

    async def test_alone_a_pause_of_half_a_minute_is_the_same_conversation(self, mock_robot, mock_ai_provider):
        person = {"id": 1, "distance": 1.4, "looking": False}
        manager = await gated_manager(mock_robot, mock_ai_provider, 0.221, people=[person])
        manager._last_answered_at = manager._clock() - 32  # "It is a pretty cool space", 32 s after the reply
        rec = {}
        assert await manager._meant_for_pepper([], "It is a pretty cool space", rec) is True
        manager.world.update_people(2, [person, {"id": 2}])  # with someone else around, 32 s is too long
        rec = {}
        assert await manager._meant_for_pepper([], "It is a pretty cool space", rec) is False

    async def test_the_record_keeps_what_pepper_said_last(self, mock_robot, mock_ai_provider):
        # groundwork for #25: the 2026-10-01 log had no record of Pepper's sentences to re-benchmark with
        from src.world.observations import SAID

        manager = await gated_manager(mock_robot, mock_ai_provider, 0.9)
        manager.world.note(SAID, "said", text="Am I facing you now?")
        rec = {}
        await manager._meant_for_pepper([], "Yeah, pretty good", rec)
        assert rec["addressee"]["pepper_said"] == "Am I facing you now?"
        assert rec["addressee"]["pepper_said_ago"] is not None
