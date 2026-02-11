"""
Tests for EventStream - WebSocket listener for robot events.
"""

import pytest
from unittest.mock import AsyncMock

from src.pepper.event_stream import EventStream


class TestEventStream:

    def test_register_callback(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on("touch", cb)
        assert cb in es._callbacks["touch"]

    def test_register_global_callback(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on_any(cb)
        assert cb in es._global_callbacks

    @pytest.mark.asyncio
    async def test_dispatch_specific(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on("touch", cb)
        await es._dispatch("touch", {"head_front": True})
        cb.assert_called_once_with("touch", {"head_front": True})

    @pytest.mark.asyncio
    async def test_dispatch_global(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on_any(cb)
        await es._dispatch("battery", {"level": 50})
        cb.assert_called_once_with("battery", {"level": 50})

    @pytest.mark.asyncio
    async def test_dispatch_no_match(self):
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock()
        es.on("touch", cb)
        await es._dispatch("sonar", {"left": 0.5})
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_callback_error(self):
        """Errors in callbacks should not propagate."""
        es = EventStream("ws://localhost:8888/ws/events")
        cb = AsyncMock(side_effect=ValueError("boom"))
        es.on("touch", cb)
        # Should not raise
        await es._dispatch("touch", {"head_front": True})
        cb.assert_called_once()

    def test_api_key_in_url(self):
        es = EventStream("ws://localhost:8888/ws/events", api_key="secret")
        assert es.api_key == "secret"
