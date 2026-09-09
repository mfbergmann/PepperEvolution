"""
Tests for PepperRobot against the FakeBridgeClient (whole host stack, no robot).
"""

import base64

import pytest

from src.pepper import ConnectionConfig, PepperRobot, PrepareOptions
from src.pepper.bridge_client import BridgeError
from src.pepper.robot import _to_jpeg_photo


class TestPepperRobotWithFakeBridge:

    async def test_initialize_prepares_and_loads_state(self, fake_bridge):
        robot = PepperRobot(ConnectionConfig(ip="fake"), bridge=fake_bridge)
        assert await robot.initialize(prepare=PrepareOptions(autonomous_life="disabled", posture="Stand"))
        assert fake_bridge.calls[0]["action"] == "prepare"
        assert robot.state.robot_name == "FakePepper"
        assert robot.state.autonomous_life == "disabled"
        assert robot.state.posture == "Stand"
        assert robot.state.awake is True
        assert len(robot.animations) > 0
        await robot.shutdown()

    async def test_initialize_without_prepare(self, fake_bridge):
        robot = PepperRobot(ConnectionConfig(ip="fake"), bridge=fake_bridge)
        assert await robot.initialize(prepare=None)
        assert not any(c["action"] == "prepare" for c in fake_bridge.calls)
        await robot.shutdown()

    async def test_actions_record_calls(self, fake_robot, fake_bridge):
        await fake_robot.speak("hi")
        await fake_robot.turn(45)
        await fake_robot.set_eye_color("blue")
        actions = [c["action"] for c in fake_bridge.calls]
        assert actions[-3:] == ["speak", "turn", "set_eye_leds"]
        assert fake_bridge.state["eye_color"] == "blue"

    async def test_take_picture_returns_jpeg_photo(self, fake_robot):
        photo = await fake_robot.take_picture()
        assert photo.media_type in ("image/jpeg", "image/png")
        assert base64.b64decode(photo.base64_data)
        assert fake_robot.last_photo is photo

    async def test_bridge_error_propagates(self, fake_robot, fake_bridge):
        fake_bridge.fail_next = "TTS busy"
        with pytest.raises(BridgeError, match="TTS busy"):
            await fake_robot.speak("hi")

    async def test_events_dispatch_and_update_battery(self, fake_robot):
        seen = []

        async def cb(event_type, data):
            seen.append((event_type, data))

        fake_robot.on_event(cb)
        await fake_robot._on_bridge_event("battery", {"level": 42, "charging": True})
        assert seen == [("battery", {"level": 42, "charging": True})]
        assert fake_robot.state.battery_level == 42 and fake_robot.state.charging is True

    async def test_shutdown_can_rest(self, fake_bridge):
        robot = PepperRobot(ConnectionConfig(ip="fake"), bridge=fake_bridge)
        await robot.initialize(prepare=None)
        await robot.shutdown(rest=True)
        assert fake_bridge.calls[-1]["action"] == "rest"
        assert robot.state.is_connected is False


class TestPhotoConversion:

    def test_jpeg_passthrough(self):
        photo = _to_jpeg_photo({"image": "abc", "width": 2, "height": 1, "format": "jpeg", "camera": 1})
        assert photo.media_type == "image/jpeg" and photo.base64_data == "abc" and photo.camera == 1

    def test_raw_rgb_is_converted(self):
        raw = bytes([255, 0, 0, 0, 255, 0])  # 2x1 pixels
        photo = _to_jpeg_photo({"image": base64.b64encode(raw).decode(), "width": 2, "height": 1, "format": "rgb"})
        assert photo.media_type == "image/jpeg"
        assert base64.b64decode(photo.base64_data)[:2] == b"\xff\xd8"  # JPEG magic
