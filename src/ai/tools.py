"""
AI tool definitions for Anthropic Claude tool-calling.

Each tool maps to a bridge endpoint. Parameter schemas include safety
limits that the executor enforces again before anything reaches the robot.
"""

from typing import Any, Dict, List

# Gestures that exist on every Pepper running NAOqi 2.5. The executor also
# checks the robot's installed list at runtime and suggests close matches.
KNOWN_ANIMATIONS: Dict[str, str] = {
    "animations/Stand/Gestures/Hey_1": "wave hello",
    "animations/Stand/Gestures/Hey_3": "big wave",
    "animations/Stand/Gestures/BowShort_1": "short bow",
    "animations/Stand/Gestures/Yes_1": "nod yes",
    "animations/Stand/Gestures/No_1": "shake head no",
    "animations/Stand/Gestures/Thinking_1": "thinking pose",
    "animations/Stand/Gestures/Explain_1": "explaining gesture",
    "animations/Stand/Gestures/Enthusiastic_4": "enthusiastic arms",
    "animations/Stand/Gestures/Excited_1": "excited",
    "animations/Stand/Gestures/ShowTablet_3": "point at the tablet",
    "animations/Stand/Gestures/ShowSky_1": "point up at the sky",
    "animations/Stand/Gestures/ShowFloor_1": "point down at the floor",
    "animations/Stand/Gestures/Me_1": "point at self",
    "animations/Stand/Gestures/You_1": "point at you",
    "animations/Stand/Gestures/IDontKnow_1": "shrug",
    "animations/Stand/Gestures/Please_1": "please / offer",
    "animations/Stand/Gestures/CalmDown_1": "calm down",
    "animations/Stand/Gestures/Everything_1": "sweeping everything gesture",
    "animations/Stand/Gestures/Desperate_1": "sad, frustrated",
    "animations/Stand/Gestures/Give_3": "give / present something",
    "animations/Stand/Emotions/Positive/Happy_4": "happy",
    "animations/Stand/Emotions/Positive/Winner_1": "winner celebration",
    "animations/Stand/Emotions/Positive/Laugh_1": "laugh",
    "animations/Stand/Emotions/Negative/Sad_1": "sad",
    "animations/Stand/Emotions/Neutral/Embarrassed_1": "shy, embarrassed",
    "animations/Stand/Waiting/AirGuitar_1": "play air guitar",
}

EYE_COLORS = ["red", "green", "blue", "yellow", "purple", "cyan", "white", "orange", "pink", "off"]
POSTURES = ["Stand", "StandInit", "StandZero", "Crouch"]


def _animation_help() -> str:
    return "; ".join(f"{path} ({meaning})" for path, meaning in KNOWN_ANIMATIONS.items())


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "speak",
        "description": (
            "Say something out loud right now, before your reply is finished - for example 'Let me take a "
            "look' before a slow action, or a sentence in another language. Your normal reply text is "
            "spoken automatically, so do not repeat it here. You may embed a gesture inline with "
            "^start(animations/Stand/Gestures/Hey_1) placed just before the words it goes with."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text for Pepper to speak aloud."},
                "animated": {
                    "type": "boolean",
                    "description": "Add automatic body language while speaking. Default true.",
                    "default": True,
                },
                "language": {
                    "type": "string",
                    "description": "Language name if not English, e.g. 'French', 'German', 'Japanese'.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "play_animation",
        "description": "Play a full-body gesture animation. Known animations: " + _animation_help() + ".",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Animation path, e.g. 'animations/Stand/Gestures/Hey_1'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "move_head",
        "description": "Turn the head to look in a direction. Yaw: positive = left, negative = right. "
        "Pitch: positive = down, negative = up. Use 0,0 to look straight ahead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "yaw": {"type": "number", "description": "Horizontal angle in degrees, -119 to 119.", "default": 0},
                "pitch": {
                    "type": "number",
                    "description": "Vertical angle in degrees, -40 (up) to 25 (down); "
                    "less when the head is turned far sideways.",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "turn",
        "description": "Turn the whole body in place. Positive = counter-clockwise (left), negative = clockwise "
        "(right). Blocks until done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle": {"type": "number", "description": "Angle in degrees, -180 to 180."},
            },
            "required": ["angle"],
        },
    },
    {
        "name": "move_forward",
        "description": "Drive forward (positive) or backward (negative) by a distance in metres. Max 2 m per "
        "call. The robot's own collision avoidance is on, but check get_sensors for obstacles before long moves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "distance": {"type": "number", "description": "Distance in metres, -2.0 to 2.0."},
                "speed": {
                    "type": "number",
                    "description": "Speed in metres per second, 0.1 to 0.5. Default 0.3.",
                    "default": 0.3,
                },
            },
            "required": ["distance"],
        },
    },
    {
        "name": "set_posture",
        "description": "Go to a predefined posture. 'Stand' is the normal upright pose; 'Crouch' is the rest pose.",
        "input_schema": {
            "type": "object",
            "properties": {
                "posture": {"type": "string", "enum": POSTURES, "description": "Target posture."},
            },
            "required": ["posture"],
        },
    },
    {
        "name": "set_eye_color",
        "description": "Change the eye LED colour, e.g. to express a mood or acknowledge something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": EYE_COLORS, "description": "Colour name."},
            },
            "required": ["color"],
        },
    },
    {
        "name": "take_photo",
        "description": "Take a photo with the head camera and look at it. The image is returned to you, so use "
        "this whenever you need to see the room, a person, or an object. Turn the head first if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "camera": {
                    "type": "integer",
                    "enum": [0, 1],
                    "description": "0 = forehead camera (default, looks ahead), 1 = mouth camera (looks down).",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "get_sensors",
        "description": "Read battery, head/hand touch sensors, bumpers, front/back sonar distances in metres, "
        "and how many people are visible.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "show_on_tablet",
        "description": "Show short text or a web page on the chest tablet. Give either text or url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to display (keep it short)."},
                "title": {"type": "string", "description": "Optional small heading above the text."},
                "url": {"type": "string", "description": "A web page to display instead of text."},
            },
        },
    },
    {
        "name": "emergency_stop",
        "description": "Immediately stop all movement and speech and put the robot to rest (motors off). Only for "
        "emergencies; the robot needs a wake-up afterwards.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]
