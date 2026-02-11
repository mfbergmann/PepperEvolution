"""
Tests for AI tool definitions.
"""

from src.ai.tools import TOOLS


class TestToolDefinitions:

    def test_all_tools_have_name(self):
        for tool in TOOLS:
            assert "name" in tool
            assert isinstance(tool["name"], str)

    def test_all_tools_have_description(self):
        for tool in TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 10

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_expected_tools_present(self):
        names = {t["name"] for t in TOOLS}
        expected = {
            "speak", "move_forward", "turn", "move_head", "set_posture",
            "play_animation", "set_eye_color", "take_photo", "get_sensors",
            "emergency_stop",
        }
        assert expected == names

    def test_speak_requires_text(self):
        speak = next(t for t in TOOLS if t["name"] == "speak")
        assert "text" in speak["input_schema"]["properties"]
        assert "text" in speak["input_schema"]["required"]

    def test_move_forward_schema(self):
        move = next(t for t in TOOLS if t["name"] == "move_forward")
        props = move["input_schema"]["properties"]
        assert "distance" in props
        assert props["distance"]["type"] == "number"

    def test_set_posture_enum(self):
        posture = next(t for t in TOOLS if t["name"] == "set_posture")
        props = posture["input_schema"]["properties"]
        assert "Stand" in props["posture"]["enum"]
        assert "Crouch" in props["posture"]["enum"]
