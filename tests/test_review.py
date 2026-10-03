"""
The review script on a synthetic session recorded by the real code paths.
"""

import importlib.util
import json
from pathlib import Path

import httpx

from src.ai.manager import AIManager
from src.ai.models import AIResponse, ToolCall
from src.decide import DecisionClient
from src.session import SessionRecorder

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_session.py"


def load_review():
    spec = importlib.util.spec_from_file_location("review_session", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decider(to_pepper, action, p_action=0.95):
    def handler(request):
        questions = json.loads(request.content)["questions"]
        if "to_pepper" in questions:
            return httpx.Response(200, json={"answers": {"to_pepper": {"noul": to_pepper.pop(0)}}})
        choice = action.pop(0)
        return httpx.Response(
            200, json={"answers": {"action": {"choice": choice, "probabilities": {choice: p_action}}}}
        )

    return DecisionClient("http://alien3:11435", transport=httpx.MockTransport(handler))


async def test_a_recorded_session_reads_as_a_timed_transcript(mock_robot, mock_ai_provider, tmp_path):
    replies = iter(
        [
            AIResponse(text="Turning left now.", stop_reason="end_turn"),
            AIResponse(
                text="",
                tool_calls=[ToolCall(id="t", name="set_posture", input={"posture": "Handstand"})],
                stop_reason="tool_use",
            ),
            AIResponse(text="One. Two. Three. Four. Five sentences is a lot.", stop_reason="end_turn"),
        ]
    )

    async def chat(messages, tools=None, system=None, on_text=None):
        r = next(replies)
        if r.text and on_text is not None:
            await on_text(r.text)
        return r

    mock_ai_provider.chat.side_effect = chat
    manager = AIManager(
        mock_robot,
        mock_ai_provider,
        speak_responses=True,
        tablet_subtitles=False,
        backchannel_after=0,
        decider=decider([0.05, 0.95, 0.9], ["talk", "turn_left", "talk"]),
    )
    rec = SessionRecorder(str(tmp_path))
    manager.recorder = rec
    rec.record_event("people", count=2, people=[])
    await manager.process_user_input("Pepper's basically an AI model", source="voice", open_mic=True)
    await manager.process_user_input("Turn left ninety degrees", source="voice", open_mic=True)
    await manager.process_user_input("Tell me about yourself", source="voice", open_mic=True)
    rec.record_event("camera", seconds=0.14, waving=0.9, showing=0.0, facing=0.8)
    rec.record_event("camera_event", what="waving", p=0.9)

    text = load_review().review(str(tmp_path))
    assert "[voice] Pepper's basically an AI model" in text and "NOT answered (side talk)" in text
    assert "router: turn_left p=0.95 -> turning left 90 degrees, started" in text
    assert "Pepper (" in text and "Turning left now." in text
    assert "· people in view: 2" in text and "· camera: waving (p=0.9)" in text
    assert "1 not answered as side talk" in text and "actions started by the router: 1" in text
    assert "long reply" in text and "tool set_posture failed" in text
    assert "not answered (check it was side talk)" in text


def test_an_empty_folder_still_reviews(tmp_path):
    text = load_review().review(str(tmp_path))
    assert "## Summary" in text and "- none" in text
