"""
Tests for EventStream - WebSocket listener for robot events.
"""

import json
from unittest.mock import AsyncMock


from src.pepper.event_stream import EventStream


class TestEventStream:

    def test_register_callbacks(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb, any_cb = AsyncMock(), AsyncMock()
        es.on("touch", cb)
        es.on_any(any_cb)
        assert cb in es._callbacks["touch"] and any_cb in es._global_callbacks

    async def test_dispatch_specific_and_global(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb, any_cb = AsyncMock(), AsyncMock()
        es.on("touch", cb)
        es.on_any(any_cb)
        await es._dispatch("touch", {"sensor": "head_front", "touched": True})
        cb.assert_called_once_with("touch", {"sensor": "head_front", "touched": True})
        any_cb.assert_called_once()

    async def test_dispatch_no_match(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on("touch", cb)
        await es._dispatch("sonar", {"front": 0.5})
        cb.assert_not_called()

    async def test_dispatch_callback_error_does_not_propagate(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock(side_effect=ValueError("boom"))
        es.on("touch", cb)
        await es._dispatch("touch", {})
        cb.assert_called_once()

    async def test_handle_raw_parses_and_ignores_control_messages(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on_any(cb)
        await es._handle_raw(json.dumps({"type": "hello", "data": {"version": "2.1.0"}}))
        await es._handle_raw(json.dumps({"type": "pong"}))
        await es._handle_raw("not json")
        cb.assert_not_called()
        await es._handle_raw(json.dumps({"type": "battery", "data": {"level": 50}, "timestamp": 1.0}))
        cb.assert_called_once_with("battery", {"level": 50})
        assert es.last_event_at == 1.0

    def test_api_key_in_url(self):
        es = EventStream("ws://localhost:8888/ws/events", api_key="secret")
        assert es.url == "ws://localhost:8888/ws/events?api_key=secret"
        assert EventStream("ws://localhost:8888/ws/events").url == "ws://localhost:8888/ws/events"

    async def test_stop_without_start(self):
        es = EventStream("ws://localhost:8888/ws/events")
        await es.stop()
        assert es.connected is False
