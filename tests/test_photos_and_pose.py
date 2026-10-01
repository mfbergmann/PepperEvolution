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

    async def test_no_pose_change_after_a_stop(self, mock_robot, mock_ai_provider, monkeypatch):
        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.02)
        mock_robot.connection.bridge.neutral_pose = AsyncMock()
        mock_ai_provider.chat = AsyncMock(side_effect=reply("^start(animations/Stand/Gestures/Hey_1) Hello!"))
        manager = AIManager(mock_robot, mock_ai_provider, speak_responses=True, tablet_subtitles=False)
        await manager.process_user_input("Hi Pepper")
        await manager.process_user_input("stop")  # during the delay: the arms stay where they are
        await asyncio.sleep(0.05)
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


def look_left_then_answer():
    """A model that turns the head left with a tool, then answers (streamed)."""
    from src.ai.models import ToolCall

    calls = {"n": 0}

    async def chat(messages, tools=None, system=None, on_text=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return AIResponse(
                text="",
                tool_calls=[ToolCall(id="t1", name="move_head", input={"yaw": 60, "pitch": 0})],
                stop_reason="tool_use",
                model="claude-sonnet-5-5",
            )
        if on_text is not None:
            await on_text("There's a whiteboard over there.")
        return AIResponse(text="There's a whiteboard over there.", stop_reason="end_turn", model="claude-sonnet-5-5")

    return chat


class TestLookBack:
    async def manager(self, mock_robot, mock_ai_provider, monkeypatch, people=(), face_tracking=False):
        from src.world import WorldModel

        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.0)
        world = WorldModel()
        await world.handle_event("sensors", {"people_count": len(people), "people": list(people)})
        mock_ai_provider.chat = AsyncMock(side_effect=look_left_then_answer())
        return AIManager(
            mock_robot,
            mock_ai_provider,
            speak_responses=True,
            tablet_subtitles=False,
            world=world,
            face_tracking=face_tracking,
        )

    async def test_faces_the_person_again_after_looking_away(self, mock_robot, mock_ai_provider, monkeypatch):
        person = {"id": 1, "distance": 1.4, "looking": True, "yaw": 10.0, "pitch": -15.0}
        manager = await self.manager(mock_robot, mock_ai_provider, monkeypatch, people=[person])
        await manager.process_user_input("Look to your left and tell me what's there.")
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.move_head.assert_awaited_with(10.0, -15.0, 0.3, wait=False)

    async def test_resumes_face_tracking_at_once_when_it_is_on(self, mock_robot, mock_ai_provider, monkeypatch):
        person = {"id": 1, "distance": 1.4, "looking": True, "yaw": 10.0, "pitch": -15.0}
        manager = await self.manager(mock_robot, mock_ai_provider, monkeypatch, people=[person], face_tracking=True)
        await manager.process_user_input("Look to your left and tell me what's there.")
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.set_awareness.assert_awaited_with(
            True, tracking_mode="Head", engagement_mode="SemiEngaged", stimuli=["People", "Touch"]
        )

    async def test_looks_out_at_the_room_when_nobody_is_there(self, mock_robot, mock_ai_provider, monkeypatch):
        manager = await self.manager(mock_robot, mock_ai_provider, monkeypatch)
        await manager.process_user_input("Look to your left and tell me what's there.")
        await asyncio.sleep(0.01)
        mock_robot.connection.bridge.move_head.assert_awaited_with(0.0, -18.0, 0.3, wait=False)

    async def test_stays_put_after_a_stop(self, mock_robot, mock_ai_provider, monkeypatch):
        manager = await self.manager(mock_robot, mock_ai_provider, monkeypatch)
        monkeypatch.setattr(AIManager, "NEUTRAL_POSE_DELAY", 0.02)  # self.manager set 0; a stop must fit in the wait
        await manager.process_user_input("Look to your left and tell me what's there.")
        await manager.process_user_input("stop")
        await asyncio.sleep(0.05)
        assert mock_robot.connection.bridge.move_head.await_count == 1  # only the model's own turn to the left


class TestLastPhotoInTheState:
    def manager(self, mock_robot, mock_ai_provider, face_tracking=False):
        return AIManager(
            mock_robot, mock_ai_provider, speak_responses=False, tablet_subtitles=False, face_tracking=face_tracking
        )

    def test_no_photo_no_line(self, mock_robot, mock_ai_provider):
        assert "last photo" not in self.manager(mock_robot, mock_ai_provider)._build_system_prompt()[1]["text"]

    async def test_old_photo_taken_looking_left_after_the_head_moved(self, mock_robot, mock_ai_provider):
        import time

        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=picture())
        await mock_robot.move_head(70, 0)
        photo = await mock_robot.take_picture()
        photo.taken_at = time.monotonic() - 30  # half a minute ago, as on the robot
        await mock_robot.move_head(-30, 0)  # the head turned to the person on the right since
        line = self.manager(mock_robot, mock_ai_provider)._last_photo_line()
        assert line.startswith("Your last photo was taken 30 seconds ago with your head turned left.")
        assert "does not show what is in front of you now" in line

    async def test_fresh_photo_with_the_head_still(self, mock_robot, mock_ai_provider):
        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=picture())
        await mock_robot.take_picture()
        line = self.manager(mock_robot, mock_ai_provider)._last_photo_line()
        assert line == "Your last photo was taken just now."

    async def test_face_tracking_means_the_head_has_moved(self, mock_robot, mock_ai_provider):
        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=picture())
        await mock_robot.take_picture()
        line = self.manager(mock_robot, mock_ai_provider, face_tracking=True)._last_photo_line()
        assert "Your head has moved since" in line


class TestPhotoLog:
    async def test_photos_are_saved_with_what_is_known_about_them(self, mock_robot, tmp_path):
        import json

        mock_robot.photo_record_dir = str(tmp_path / "photos")
        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=dict(picture(), head=[58.9, -1.0]))
        await mock_robot.move_head(60, 0)
        await mock_robot.take_picture()
        files = sorted(p.suffix for p in (tmp_path / "photos").iterdir())
        assert files == [".jpg", ".json"]
        meta = json.loads(next((tmp_path / "photos").glob("*.json")).read_text())
        assert meta["head_commanded_yaw"] == 60 and meta["head_measured"] == [58.9, -1.0] and meta["sharpness"] > 0

    async def test_no_log_unless_asked(self, mock_robot, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_robot.connection.bridge.take_picture = AsyncMock(return_value=picture())
        await mock_robot.take_picture()
        assert list(tmp_path.iterdir()) == []


class TestHeadDidNotArrive:
    async def test_the_model_hears_where_the_head_really_points(self, mock_robot):
        mock_robot.connection.bridge.move_head = AsyncMock(
            return_value={"yaw": 60.0, "pitch": 0.0, "settled": False, "measured": [4.5, -12.0]}
        )
        outcome = await ToolExecutor(mock_robot).execute("move_head", {"yaw": 60, "pitch": 0})
        assert outcome.ok and outcome.data["settled"] is False and outcome.data["measured_yaw"] == 4.5
        assert "did not reach" in outcome.data["note"]

    async def test_nothing_extra_when_it_arrived(self, mock_robot):
        mock_robot.connection.bridge.move_head = AsyncMock(
            return_value={"yaw": 60.0, "pitch": 0.0, "settled": True, "measured": [59.6, 0.2]}
        )
        outcome = await ToolExecutor(mock_robot).execute("move_head", {"yaw": 60, "pitch": 0})
        assert outcome.data == {"yaw": 60.0, "pitch": 0.0}
