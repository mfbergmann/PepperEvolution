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
        await es._handle_raw(json.dumps({"type": "hello", "data": {"version": "0.3.0"}}))
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


class TestReconnect:
    async def test_reconnect_callbacks_run_after_a_bridge_restart_only(self, monkeypatch):
        # 2026-10-20: a redeployed bridge forgot sound localisation and the host never asked again
        import asyncio

        import src.pepper.event_stream as module

        class FakeWs:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration  # the bridge went away at once

        class FakeConnect:
            async def __aenter__(self):
                return FakeWs()

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(module, "ws_connect", lambda *a, **k: FakeConnect())
        es = EventStream("ws://localhost:8888/ws/events")
        es.RECONNECT_DELAY = 0.0
        resubscribe = AsyncMock()
        es.on_reconnect(resubscribe)
        await es.start()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if es._connections >= 3:
                break
        await es.stop()
        assert es._connections >= 3
        assert 1 <= resubscribe.await_count < es._connections  # never on the first connection
