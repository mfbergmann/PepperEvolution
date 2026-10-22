"""
Tests for the camera change check (src/perception/change.py) and the watch mode of the camera stream (#33).
"""

import io

from PIL import Image, ImageDraw

from src.perception.change import ChangeDetector
from src.perception.vision import IDLE_FPS, FrameWatcher
from src.world.model import WorldModel


def jpeg(person_at=None, shade=90):
    """A plain room, optionally with a dark figure standing at x = person_at (0-319)."""
    image = Image.new("RGB", (320, 240), (shade, shade, shade))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 180, 319, 239), fill=(140, 120, 100))  # the floor
    if person_at is not None:
        draw.rectangle((person_at, 60, person_at + 50, 220), fill=(20, 20, 30))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


class TestChangeDetector:
    def test_someone_walking_in_is_a_change_and_a_still_view_is_not(self):
        d = ChangeDetector()
        assert d.feed(jpeg()) is False  # the first frame only sets the reference
        assert d.feed(jpeg()) is False
        assert d.feed(jpeg(person_at=140)) is True
        assert d.feed(jpeg(person_at=140)) is False  # standing still: no new change
        assert d.changes == 1

    def test_a_small_flicker_is_not_a_change(self):
        d = ChangeDetector()
        d.feed(jpeg(shade=90))
        assert d.feed(jpeg(shade=96)) is False  # light level drifts a little

    def test_a_head_turn_or_a_base_move_restarts_the_comparison(self):
        d = ChangeDetector()
        d.feed(jpeg(), head=[0.0, 0.0])
        assert d.feed(jpeg(person_at=140), head=[30.0, 0.0]) is False  # a different view, not a change in the room
        assert d.feed(jpeg(person_at=140), head=[30.5, 0.0]) is False
        d.feed(jpeg(), pose=(0.0, 0.0, 0.0))
        assert d.feed(jpeg(person_at=140), pose=(0.5, 0.0, 0.0)) is False
        assert d.feed(jpeg(person_at=10), pose=(0.5, 0.0, 0.0)) is True

    def test_a_broken_frame_is_ignored(self):
        d = ChangeDetector()
        d.feed(jpeg())
        assert d.feed(b"not a jpeg") is False


class Decider:
    def __init__(self):
        self.calls = []

    async def ask(self, model, state, questions, images=None, timeout=None):
        self.calls.append(model)
        return {}


class TestWatchMode:
    def make(self, watch=True, count=0):
        clock = [100.0]
        world = WorldModel(clock=lambda: clock[0])
        world.count = count

        async def on_event(kind, data):
            pass

        decider = Decider()
        w = FrameWatcher("ws://x", decider, world, on_event, clock=lambda: clock[0], watch=watch)
        return w, world, decider, clock

    def test_the_stream_rate(self):
        w, world, _, _ = self.make(watch=True)
        assert w._rate() == IDLE_FPS
        world.count = 1
        assert w._rate() == w.fps
        w, world, _, _ = self.make(watch=False)
        assert w._rate() is None
        assert w._stream_url(0.5) == "ws://x?fps=0.5"

    async def test_an_empty_unchanged_room_costs_no_model_call(self):
        w, world, decider, _ = self.make()
        w.frame(jpeg())
        w.frame(jpeg())
        assert decider.calls == []

    async def test_a_change_asks_where_the_person_is_and_is_remembered(self):
        w, world, decider, _ = self.make()
        w.frame(jpeg())
        w.frame(jpeg(person_at=140))
        for task in list(w._judging):
            await task
        assert decider.calls == ["clef-flash"]  # the people question, not the waving/showing judgements
        assert world.timeline.last("view_changed") is not None
