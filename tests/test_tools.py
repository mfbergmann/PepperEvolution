"""
Tests for AI tool definitions.
"""

from src.ai.tools import KNOWN_ANIMATIONS, TOOL_NAMES, TOOLS


class TestToolDefinitions:

    def test_all_tools_well_formed(self):
        for tool in TOOLS:
            assert isinstance(tool["name"], str)
            assert len(tool["description"]) > 10
            assert tool["input_schema"]["type"] == "object"
            assert "properties" in tool["input_schema"]

    def test_expected_tools_present(self):
        expected = {
            "speak",
            "play_animation",
            "move_head",
            "turn",
            "move_forward",
            "set_posture",
            "set_eye_color",
            "take_photo",
            "get_sensors",
            "show_on_tablet",
            "emergency_stop",
        }
        assert expected == set(TOOL_NAMES)

    def test_speak_requires_text(self):
        speak = next(t for t in TOOLS if t["name"] == "speak")
        assert "text" in speak["input_schema"]["required"]

    def test_move_forward_schema(self):
        move = next(t for t in TOOLS if t["name"] == "move_forward")
        assert move["input_schema"]["properties"]["distance"]["type"] == "number"
        assert "distance" in move["input_schema"]["required"]

    def test_set_posture_enum(self):
        posture = next(t for t in TOOLS if t["name"] == "set_posture")
        assert {"Stand", "Crouch"} <= set(posture["input_schema"]["properties"]["posture"]["enum"])

    def test_animation_help_in_description(self):
        anim = next(t for t in TOOLS if t["name"] == "play_animation")
        for path in KNOWN_ANIMATIONS:
            assert path in anim["description"]

    def test_names_are_unique(self):
        assert len(TOOL_NAMES) == len(set(TOOL_NAMES))
