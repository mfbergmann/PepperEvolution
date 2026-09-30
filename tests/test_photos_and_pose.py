"""
Tests for sharp photos (retake when blurry, tell the model) and the neutral pose after gestures.
"""

import asyncio
import base64
import io
from unittest.mock import AsyncMock

from PIL import Image, ImageFilter

from src.ai.manager import AIManager
from src.ai.models import AIResponse
from src.ai.tool_executor import ToolExecutor
from src.pepper.robot import BLURRY_BELOW, Photo, photo_sharpness


def jpeg(blur: float = 0.0) -> str:
    """A 640x480 checkerboard, optionally blurred, as base64 JPEG."""
    img = Image.new("L", (640, 480))
    for x in range(0, 640, 20):
        for y in range(0, 480, 20):
            if (x // 20 + y // 20) % 2:
                img.paste(255, (x, y, x + 20, y + 20))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def picture(blur: float = 0.0):
    return {"image": jpeg(blur), "width": 640, "height": 480, "format": "jpeg", "camera": 0}


class TestSharpness:
    def test_blur_lowers_the_score(self):
        sharp = photo_sharpness(Photo("image/jpeg", jpeg(), 640, 480))
        blurry = photo_sharpness(Photo("image/jpeg", jpeg(blur=6), 640, 480))
        assert sharp is not None and blurry is not None
        assert sharp > BLURRY_BELOW > blurry

    def test_unreadable_image_has_no_score(self):
        assert photo_sharpness(Photo("image/jpeg", "bm90IGFuIGltYWdl", 1, 1)) is None


class TestRetake:
    async def test_a_sharp_photo_is_taken_once(self, mock_robot):
        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=picture())
        photo = await mock_robot.take_picture()
        assert mock_robot.connection.bridge.take_picture.await_count == 1
        assert photo.blurry is False and photo.sharpness > BLURRY_BELOW

    async def test_a_blurry_photo_is_retaken_and_the_sharper_kept(self, mock_robot):
        mock_robot.connection.bridge.take_picture = AsyncMock(side_effect=[picture(blur=6), picture()])
        photo = await mock_robot.take_picture()
        assert mock_robot.connection.bridge.take_picture.await_count == 2
        assert photo.blurry is False and mock_robot.last_photo is photo

    async def test_still_blurry_is_said_in_the_tool_result(self, mock_robot):
        mock_robot.connection.bridge.take_picture = AsyncMock(side_effect=[picture(blur=6), picture(blur=8)])
        outcome = await ToolExecutor(mock_robot).execute("take_photo", {})
        assert outcome.ok and outcome.image.blurry is True
        assert "blurry" in outcome.data["note"]


def reply(text):
    """A model call that streams ``text`` to the speaker, as the real providers do."""

    async def chat(messages, tools=None, system=None, on_text=None):
        if on_text is not None:
            await on_text(text)
        return AIResponse(text=text, tool_calls=[], stop_reason="end_turn", model="claude-sonnet-5-5")

    return chat


class TestNeutralPose:
    async def test_arms_come_down_after_a_gesture(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.0)
        mock_robot.connection.bridge.neutral_pose = AsyncMock(return_value={"joints": 15})
        mock_ai_provider.chat = AsyncMock(side_effect=reply("^start(animations/Stand/Gestures/Hey_1) Hi there!"))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        await manager.process_user_input("[Sensor event] Someone just walked up to you.", source="event")
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.neutral_pose.assert_awaited_once()

    async def test_no_pose_change_after_plain_speech(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.0)
        mock_robot.connection.bridge.neutral_pose = AsyncMock()
        mock_ai_provider.chat = AsyncMock(side_effect=reply("The moon is about 384,000 km away."))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        await manager.process_user_input("How far is the moon?")
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.neutral_pose.assert_not_awaited()

    async def test_a_new_turn_keeps_the_body(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.02)
        mock_robot.connection.bridge.neutral_pose = AsyncMock()
        mock_ai_provider.chat = AsyncMock(side_effect=reply("^start(animations/Stand/Gestures/Hey_1) Hello!"))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        await manager.process_user_input("Hi Pepper")
        async with manager._lock:  # the next turn is running when the delay ends
            await asyncio.sleep(0.05)
        mock_robot.connection.bridge.neutral_pose.assert_not_awaited()


class TestDirectAnimation:
    async def test_arms_come_down_after_an_animation_from_the_ui(self, mock_robot):
        from src.communication.api import execute_command

        response = await execute_command(mock_robot, "animation", {"name": "animations/Stand/Gestures/Hey_1"})
        assert response["success"] and response["result"]["neutral"] == {"joints": 15}
        mock_robot.connection.bridge.neutral_pose.assert_awaited_once()
