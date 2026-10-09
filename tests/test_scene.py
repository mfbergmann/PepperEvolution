"""
Scene notes (#11, src/perception/scene.py): who is where from the decision model, the place from a small vision
model, into working memory; failing open.
"""

import json

import httpx

from src.decide import DecisionClient
from src.perception.scene import PEOPLE_QUESTIONS, ScenePass
from src.world import WorldModel


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def decider(people="one", where="left", p=0.92):
    def handler(request):
        questions = json.loads(request.content)["questions"]
        assert set(questions) == set(PEOPLE_QUESTIONS)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "people": {"choice": people, "probabilities": {people: p}},
                    "where": {"choice": where, "probabilities": {where: p}},
                }
            },
        )

    return DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler))


def place_server(note="a meeting room with a round orange table", fail=False):
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        if fail:
            return httpx.Response(404, json={"error": "model not found"})
        content = json.dumps({"place": "meeting room", "objects": ["round table", "lamp"], "note": note})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    return httpx.MockTransport(handler), seen


async def test_a_look_becomes_a_scene_note():
    clock = Clock()
    world = WorldModel(clock=clock)
    world.handle_people(1, [{"id": 1, "yaw": 20, "distance": 1.5}])
    transport, seen = place_server()
    scene = ScenePass(decider(), world, ollama_url="http://alien3:11435", clock=clock, transport=transport)
    data = await scene.look(b"jpeg")
    assert data["people"] == 1 and data["where"] == "left" and data["note"].startswith("a meeting room")
    assert seen["body"]["think"] is False and seen["body"]["format"]["required"] == ["place", "objects", "note"]
    assert "Your last look around (just now): a meeting room with a round orange table." in world.summary()
    recall = world.recall()
    assert recall["last_look"]["objects"] == ["round table", "lamp"]
    assert any("you looked around: a meeting room" in line for line in recall["recently"])
    clock.t += 100
    assert "last look" not in world.summary()  # stale notes leave the line


async def test_fails_open_and_keeps_what_answered():
    world = WorldModel(clock=Clock())
    transport, _ = place_server(fail=True)
    scene = ScenePass(decider(people="none", where="nobody"), world, ollama_url="http://x:1", transport=transport)
    data = await scene.look(b"jpeg")
    assert data == {"people": 0, "people_p": 0.92, "where": None}  # no place note this time

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    nothing = ScenePass(DecisionClient("http://x:1", transport=httpx.MockTransport(down)), world)
    assert await nothing.look(b"jpeg") is None


async def test_looks_when_someone_arrives_then_every_so_often():
    clock = Clock()
    world = WorldModel(clock=clock)
    transport, _ = place_server()
    scene = ScenePass(decider(), world, ollama_url="http://alien3:11435", every=20, clock=clock, transport=transport)
    world.handle_people(1, [{"id": 1}])
    scene.offer(b"a")
    await _settle(scene)
    clock.t += 5
    scene.offer(b"b")  # not due yet
    await _settle(scene)
    world.handle_people(2, [{"id": 1}, {"id": 2}])
    scene.offer(b"c")  # someone new: look now
    await _settle(scene)
    clock.t += 21
    scene.offer(b"d")
    await _settle(scene)
    assert scene.looks == 3


async def _settle(scene):
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)
    if scene._tasks:
        await asyncio.gather(*scene._tasks)
