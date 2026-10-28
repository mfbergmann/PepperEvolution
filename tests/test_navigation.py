"""
Tests for the map and remembered places (#36): the store's maps and places, the world model's map positions, the
place readers, the navigator against the fake bridge, the mind's place tools and the map panel's API.
"""

import io
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from src.ai.tool_executor import ToolExecutor
from src.ai.tools import MAP_TOOL_NAMES
from src.communication.api import APIServer
from src.memory import MemoryStore
from src.navigation import Navigator
from src.world import Observation, WorldModel
from src.world.observations import MAP, MOTION
from src.world.places import place_at, place_line
from src.world.views import recall


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store():
    s = MemoryStore(":memory:", now=lambda: datetime(2026, 10, 28, 12, 0))
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock()


@pytest_asyncio.fixture
async def nav(fake_robot, store, clock):
    world = WorldModel(clock=clock)
    navigator = Navigator(fake_robot, world, store, clock=clock)
    await navigator.start()
    yield navigator
    await navigator.stop()


class TestStore:
    def test_maps_and_the_active_one(self, store):
        assert store.active_map() is None
        first = store.add_map("/home/nao/.local/share/Explorer/a.explo", radius=3.0)
        second = store.add_map("/home/nao/.local/share/Explorer/b.explo", radius=5.0)
        assert store.active_map().id == second.id and [m.active for m in store.maps()] == [False, True]
        assert store.activate_map(first.id).path.endswith("a.explo")
        assert store.activate_map(999) is None and store.active_map().id == first.id
        store.save_map_pose(first.id, 1.5, -0.5, 90.0)
        assert store.active_map().last_pose == (1.5, -0.5, 90.0)

    def test_places_belong_to_a_map(self, store):
        office = store.add_map("/x/office.explo")
        place = store.set_place(office.id, "  The   Office ", 0.5, 0.2, theta=180.0)
        assert place.name == "The Office" and place.radius == 1.5
        assert store.find_place(office.id, "the office").id == place.id  # names match ignoring case and spaces
        moved = store.set_place(office.id, "the office", 1.0, 1.0)
        assert moved.id == place.id and (moved.x, moved.y, moved.theta) == (1.0, 1.0, None)
        other = store.add_map("/x/other.explo")
        assert store.places(other.id) == [] and len(store.places(office.id)) == 1
        assert store.delete_place(office.id, "THE OFFICE") and not store.delete_place(office.id, "the office")
        with pytest.raises(ValueError):
            store.set_place(office.id, "   ", 0, 0)

    def test_an_older_file_gains_the_tables(self, tmp_path):
        import sqlite3

        from src.memory import store as store_module

        path = str(tmp_path / "pepper.sqlite")
        old = sqlite3.connect(path)
        old.executescript(store_module.SCHEMA.split("CREATE TABLE IF NOT EXISTS maps")[0])  # a version-1 file
        old.execute("PRAGMA user_version = 1")
        old.commit()
        old.close()
        s = MemoryStore(path)
        assert s.counts()["maps"] == 0 and s.add_map("/x/a.explo").active
        s.close()


class Spot:
    def __init__(self, name, x, y, radius=1.5):
        self.name, self.x, self.y, self.radius = name, x, y, radius


class TestReaders:
    places = [Spot("the office", 0.0, 0.0), Spot("the open space", 5.0, 0.0, radius=2.5)]

    def test_place_at_picks_the_nearest_containing_place(self):
        assert place_at(0.5, 0.5, self.places).name == "the office"
        assert place_at(3.0, 0.0, self.places).name == "the open space"  # within its wider radius
        assert place_at(2.0, 3.0, self.places) is None

    def test_place_line(self):
        assert place_line((99.0, {"x": 0.2, "y": 0.0, "place": "the office"}), self.places, 100.0) == (
            "Where you are on your map: at the office; places you know: the office, the open space."
        )
        between = (99.0, {"x": 2.0, "y": 3.0, "place": None, "uncertainty": [1.4, 0.3]})
        assert "probably about 4 m from the office" in place_line(between, self.places, 100.0)
        assert "not known right now" in place_line((0.0, {"x": 0, "y": 0}), self.places, 100.0)  # stale
        assert "not named any places" in place_line(None, [], 100.0)
        many = [Spot(f"p{i}", i * 10.0, 0.0) for i in range(10)]
        assert "p7 and 2 more." in place_line(None, many, 0.0)


class TestWorld:
    def test_map_positions_and_places_in_the_timeline(self, clock):
        world = WorldModel(clock=clock)
        world.observe(Observation(MAP, "position", 100.0, {"x": 0.1, "y": 0.0, "theta": 0.0, "place": "the office"}))
        world.observe(Observation(MAP, "position", 105.0, {"x": 3.0, "y": 0.0, "theta": 0.0, "place": None}))
        world.observe(Observation(MAP, "position", 110.0, {"x": 5.0, "y": 0.0, "theta": 0.0, "place": "the hall"}))
        world.observe(Observation(MAP, "lost", 115.0, {}))
        kinds = [(e.kind, e.data.get("place")) for e in world.timeline.recent()]
        assert kinds == [
            ("at_place", "the office"),
            ("left_place", "the office"),
            ("at_place", "the hall"),
            ("left_place", "the hall"),
        ]
        assert world.map_position is None
        assert world.pose.x == 0.0  # the map frame never touches the odometry pose
        clock.t = 116.0
        lines = recall(world)["recently"]
        assert lines[0].endswith("you arrived at the office") and lines[-1].endswith("(lost your place on the map)")


class TestNavigator:
    async def test_explore_name_places_and_go_between_them(self, nav, fake_robot, store):
        fake_robot.motion_observer = lambda kind, data: nav.world.note(MOTION, kind, **data)  # as main.py does
        assert nav.available is True and nav.map is None and nav.place_line() == ""
        assert nav.remember_place("the office")["ok"] is False  # no map yet
        explored = await nav.explore(radius=4.0)
        assert explored["ok"] and store.active_map().radius == 4.0
        assert nav.remember_place("the office") == {"ok": True, "place": "the office", "x": 0.0, "y": 0.0}
        assert nav.world.map_position[1]["place"] == "the office"

        fake_robot.bridge.map_pose = [5.0, 0.0, 180.0]  # someone drove Pepper into the open space
        await nav.refresh()
        assert nav.world.map_position[1]["place"] is None
        assert nav.remember_place("the open space")["ok"]
        assert nav.place_line() == (
            "Where you are on your map: at the open space; places you know: the office, the open space."
        )

        went = await nav.go_to("The Office")
        assert went == {"ok": True, "place": "the office", "distance_left": 0.0, "now_at": "the office"}
        go = [c for c in fake_robot.bridge.calls if c["action"] == "map_go"][-1]
        assert (go["x"], go["y"], go["theta"]) == (0.0, 0.0, 0.0)  # and facing the way it faced when named
        assert any(e.kind == "navigate" for e in nav.world.timeline.recent())  # a move: no "moved unexpectedly"
        assert (await nav.go_to("the office"))["already_there"] is True

        missing = await nav.go_to("the kitchen")
        assert missing["ok"] is False and missing["places"] == ["the office", "the open space"]
        assert nav.forget_place("the open space")["ok"] and not nav.forget_place("the open space")["ok"]

    async def test_go_refuses_when_halted_or_lost(self, nav, fake_robot):
        await nav.explore()
        nav.remember_place("home")
        fake_robot.bridge.map_pose = [3.0, 0.0, 0.0]
        fake_robot.halted = True
        assert "Halted" in (await nav.go_to("home"))["error"]
        fake_robot.halted = False
        fake_robot.bridge.map_localizing = False
        nav.store.save_map_pose(nav.map.id, 3.0, 0.0, 0.0)
        went = await nav.go_to("home")  # relocalizes around the last saved position, then goes
        assert went["ok"] and fake_robot.bridge.map_localizing

    async def test_too_unsure_to_drive(self, nav, fake_robot, monkeypatch):
        await nav.explore()
        nav.remember_place("home")
        original = fake_robot.bridge._fake_position
        monkeypatch.setattr(fake_robot.bridge, "_fake_position", lambda: {**original(), "uncertainty": [3.0, 1.0]})
        fake_robot.bridge.map_pose = [3.0, 0.0, 0.0]
        await nav.refresh()
        assert "not sure" in (await nav.go_to("home"))["error"]
        assert "probably" in nav.place_line()

    async def test_someone_says_where_pepper_is(self, nav, fake_robot):
        await nav.explore()
        fake_robot.bridge.map_pose = [4.0, 1.0, 90.0]
        await nav.refresh()
        nav.remember_place("the door")
        fake_robot.bridge.map_pose = [0.0, 0.0, 0.0]  # pushed away while off; the map has it wrong
        result = await nav.i_am_at("the door")
        assert result["ok"] and result["position"]["place"] == "the door"
        assert fake_robot.bridge.map_pose == [4.0, 1.0, 90.0]

    async def test_restart_relocalizes_where_pepper_last_was(self, fake_robot, store, clock):
        first = Navigator(fake_robot, WorldModel(clock=clock), store, clock=clock)
        await first.start()
        await first.explore()
        fake_robot.bridge.map_pose = [2.0, -1.0, 45.0]
        await first.refresh()
        await first.stop()
        fake_robot.bridge.map_localizing = False  # NAOqi restarted
        second = Navigator(fake_robot, WorldModel(clock=clock), store, clock=clock)
        await second.start()
        try:
            loads = [c for c in fake_robot.bridge.calls if c["action"] in ("map_load", "map_localize")]
            assert loads[-2]["action"] == "map_load" and second.localizing
            assert second.position()["x"] == 2.0 and second.position()["theta"] == 45.0
        finally:
            await second.stop()

    async def test_an_old_bridge_has_no_map(self, fake_robot, store, clock):
        fake_robot.bridge.map_status = AsyncMock(side_effect=Exception("404 /map"))
        navigator = Navigator(fake_robot, WorldModel(clock=clock), store, clock=clock)
        await navigator.start()
        assert navigator.available is False and navigator.place_line() == ""

    async def test_the_drawn_map(self, nav):
        await nav.explore()
        nav.remember_place("the office")
        image = Image.open(io.BytesIO(await nav.image_png()))
        assert image.size == (80 * 4, 50 * 4)
        cx, cy = 2.0 / 0.1 * 4, (50 - 2.5 / 0.1) * 4  # Pepper at the map's (0, 0)
        assert image.getpixel((int(cx), int(cy)))[0] > 200  # the red dot
        assert image.getpixel((5, 5)) == (255, 255, 255)  # free space


class TestTools:
    async def test_place_tools(self, nav, fake_robot):
        executor = ToolExecutor(fake_robot, world=nav.world)
        assert (await executor.execute("go_to_place", {"name": "x"})).data["error"] == "You have no map here."
        executor.navigator = nav
        await nav.explore()
        saved = await executor.execute("remember_place", {"name": "the office"})
        assert saved.ok and saved.data["place"] == "the office"
        lost = await executor.execute("go_to_place", {"name": "the moon"})
        assert not lost.ok and lost.data["places"] == ["the office"]
        assert (await executor.execute("i_am_at_place", {"name": "the office"})).ok
        assert (await executor.execute("forget_place", {"name": "the office"})).ok

    def test_offered_only_with_a_map(self, mock_robot, mock_ai_provider, store):
        from src.ai.manager import AIManager

        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False)
        names = [t["name"] for t in manager.tools]
        assert not set(MAP_TOOL_NAMES) & set(names) and "Places" not in manager.system_prompt
        navigator = Navigator(mock_robot, WorldModel(), store)
        manager.use_navigator(navigator)
        assert set(MAP_TOOL_NAMES) <= {t["name"] for t in manager.tools}
        assert "go_to_place" in manager.system_prompt and manager.system_prompt.endswith("then stop and listen.")
        assert manager.executor.navigator is navigator
        assert "explore" not in MAP_TOOL_NAMES  # an operator explores, never the mind


class TestMapPanel:
    async def test_endpoints(self, nav, fake_robot, mock_ai_manager, tmp_path):
        server = APIServer("127.0.0.1", 8000, mock_ai_manager, fake_robot, web_dir=tmp_path, navigator=nav)
        async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as client:
            assert (await client.get("/map")).json()["map"] is None
            assert (await client.get("/map.png")).status_code == 409  # nothing to draw yet
            assert (await client.post("/map/explore", json={"radius": 3})).json()["ok"]
            assert (await client.post("/map/place", json={"name": "the office"})).json()["ok"]
            status = (await client.get("/map")).json()
            assert status["enabled"] and status["places"][0]["name"] == "the office" and status["localizing"]
            png = await client.get("/map.png")
            assert png.status_code == 200 and png.headers["content-type"] == "image/png"
            assert (await client.post("/map/here", json={"name": "the office"})).json()["ok"]
            assert (await client.post("/map/go", json={"name": "the office"})).json()["ok"]
            assert (await client.delete("/map/place/the office")).json()["ok"]
            assert (await client.post("/map/use", json={"map_id": 99})).status_code == 404
            assert fake_robot.direct_commands_running == 0

    async def test_without_a_map(self, mock_robot, mock_ai_manager, tmp_path):
        server = APIServer("127.0.0.1", 8000, mock_ai_manager, mock_robot, web_dir=tmp_path)
        async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as client:
            assert (await client.get("/map")).json() == {"enabled": False}
            assert (await client.post("/map/go", json={"name": "x"})).status_code == 404
