"""
AI tool definitions for Anthropic Claude tool-calling.

Each tool maps to a bridge endpoint. Parameter schemas include
safety limits that the executor will enforce.
"""

TOOLS = [
    {
        "name": "speak",
        "description": "Make Pepper say something out loud. Use this whenever you want the robot to verbally communicate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text for Pepper to speak aloud.",
                },
                "animated": {
                    "type": "boolean",
                    "description": "Whether to use animated speech with gestures. Default false.",
                    "default": False,
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "move_forward",
        "description": "Move Pepper forward or backward by a distance in meters. Positive = forward, negative = backward. Max 2m.",
        "input_schema": {
            "type": "object",
            "properties": {
                "distance": {
                    "type": "number",
                    "description": "Distance in meters (-2.0 to 2.0).",
                },
                "speed": {
                    "type": "number",
                    "description": "Speed factor (0.1 to 0.8). Default 0.3.",
                    "default": 0.3,
                },
            },
            "required": ["distance"],
        },
    },
    {
        "name": "turn",
        "description": "Turn Pepper left or right by an angle in degrees. Positive = counter-clockwise (left), negative = clockwise (right).",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle": {
                    "type": "number",
                    "description": "Angle in degrees (-180 to 180).",
                },
            },
            "required": ["angle"],
        },
    },
    {
        "name": "move_head",
        "description": "Move Pepper's head to look in a direction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "yaw": {
                    "type": "number",
                    "description": "Horizontal angle in degrees. Positive = left, negative = right. Range: -119 to 119.",
                    "default": 0,
                },
                "pitch": {
                    "type": "number",
                    "description": "Vertical angle in degrees. Positive = down, negative = up. Range: -40 to 36.",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "set_posture",
        "description": "Set Pepper's body posture.",
        "input_schema": {
            "type": "object",
            "properties": {
                "posture": {
                    "type": "string",
                    "enum": ["Stand", "StandInit", "StandZero", "Crouch"],
                    "description": "Target posture.",
                },
            },
            "required": ["posture"],
        },
    },
    {
        "name": "play_animation",
        "description": "Play a built-in gesture animation on Pepper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Animation path, e.g. 'animations/Stand/Gestures/Hey_1' for waving, "
                    "'animations/Stand/Gestures/Enthusiastic_4' for nodding.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "set_eye_color",
        "description": "Set the color of Pepper's eye LEDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "color": {
                    "type": "string",
                    "enum": ["red", "green", "blue", "yellow", "purple", "cyan", "white", "off"],
                    "description": "Color name.",
                },
            },
            "required": ["color"],
        },
    },
    {
        "name": "take_photo",
        "description": "Take a photo with Pepper's camera and return it. Use this to see what Pepper sees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "camera": {
                    "type": "integer",
                    "enum": [0, 1],
                    "description": "0 = top camera, 1 = bottom camera. Default 0.",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "get_sensors",
        "description": "Read Pepper's current sensor data: battery level, touch sensors, sonar distances, and people count.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "emergency_stop",
        "description": "Immediately stop all movement and disable motors. Use only in emergencies.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
