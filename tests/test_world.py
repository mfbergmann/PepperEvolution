"""
Tests for the world model's people tracking and the "around you" line.
"""

from src.ai.manager import AIManager
from src.world import WorldModel


def person(pid=1, distance=1.0, looking=True, zone=1, present_for=30):
    return {"id": pid, "distance": distance, "looking": looking, "zone": zone, "present_for": present_for}


def world_at(start=1000.0):
    clock = [start]
    return WorldModel(clock=lambda: clock[0]), clock


class TestWorldModel:
    async def test_nothing_known_yet(self):
        world, _ = world_at()
        assert world.summary() == ""
        await world.handle_event("touch", {"sensor": "head_front", "touched": True})
        assert world.summary() == ""

    async def test_snapshot_on_connect_seeds_the_model_without_an_arrival(self):
        world, _ = world_at()
        await world.handle_event("sensors", {"people_count": 1, "people": [person(present_for=150)]})
        assert world.summary() == (
            "Around you: one person, about 1.0 m away, looking at you, here for about 2 minutes."
        )

    async def test_arrival_is_mentioned_while_recent(self):
        world, clock = world_at()
        await world.handle_event("sensors", {"people_count": 0, "people": []})
        assert world.summary() == "Around you: nobody in view."
        clock[0] += 5
        await world.handle_event(
            "people", {"count": 1, "people": [person(distance=1.6, zone=2, looking=False, present_for=3)]}
        )
        clock[0] += 12
        assert world.summary() == (
            "Around you: one person, about 1.6 m away, not looking at you, here for under a minute."
            " Someone arrived 12 seconds ago."
        )
        clock[0] += 120
        assert "arrived" not in world.summary()

    async def test_departure_and_last_seen(self):
        world, clock = world_at()
        await world.handle_event("people", {"count": 1, "people": [person()]})
        clock[0] += 30
        await world.handle_event("people", {"count": 0, "people": []})
        clock[0] += 150
        assert world.summary() == "Around you: nobody in view (you last saw someone 2 minutes ago)."
        clock[0] -= 140
        assert world.summary() == "Around you: nobody in view. Someone left 10 seconds ago."  # said once, not twice

    async def test_several_people_nearest_first_and_the_rest_counted(self):
        world, _ = world_at()
        people = [person(1, 1.0, True, 1), person(2, 2.2, False, 2), person(3, 3.5, None, 3), person(4, 4.0, None, 3)]
        await world.handle_event("people", {"count": 4, "people": people})
        text = world.summary()
        assert text.startswith("Around you: one person, about 1.0 m away, looking at you")
        assert "another person, about 2.2 m away, not looking at you" in text
        assert text.count("another person") == 2 and text.endswith("and 1 more.")

    async def test_zone_words_when_distance_is_missing(self):
        world, _ = world_at()
        await world.handle_event(
            "people", {"count": 1, "people": [person(distance=None, zone=3, looking=None, present_for=None)]}
        )
        assert world.summary() == "Around you: one person, far away."

    async def test_count_without_details(self):
        world, _ = world_at()
        await world.handle_event("people", {"count": 2, "people": []})
        assert world.summary() == "Around you: 2 people in view."


class TestAroundYouInThePrompt:
    async def test_state_block_includes_the_summary(self, mock_robot, mock_ai_provider):
        world, _ = world_at()
        await world.handle_event("people", {"count": 1, "people": [person()]})
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False, world=world)
        system = manager._build_system_prompt()
        assert "Around you: one person, about 1.0 m away, looking at you" in system[1]["text"]
        assert "Around you" not in system[0]["text"].split('Around you" line')[0][-5:]  # the static block stays cached

    async def test_no_world_no_line(self, mock_ai_manager):
        assert "Around you:" not in mock_ai_manager._build_system_prompt()[1]["text"]
