"""
Is an utterance addressed to Pepper? (issue #19)

The state and question below are exactly what was benchmarked on 2026-10-03 (21 of 22 utterances from a real session
with two visitors right with Nimble, 0.05 s). Changing the wording means re-running that replay
(``results/system-one/replay_addressee.py``, local only because it holds the visitors' words).
"""

from typing import Optional, Sequence

MODEL = "nimble"
CONTEXT_UTTERANCES = 3

QUESTION = {
    "to_pepper": {
        "type": "noul",
        "instructions": "Was the last thing said addressed to Pepper the robot, "
        "as a request or a question it should answer, rather than said to another person?",
    }
}


def build_state(heard_before: Sequence[str], text: str) -> str:
    before = list(heard_before)[-CONTEXT_UTTERANCES:]
    lines = "\n".join(f"- {u}" for u in before) or "(nothing yet)"
    return (
        "Pepper is a humanoid robot in a university lab. Its developer and sometimes visitors are around it, "
        "and they talk both to Pepper and to each other. Speech recognition makes mistakes (Pepper's name is often "
        f"misheard, e.g. 'Epper', 'Hipper').\nRecent things said near Pepper, oldest first:\n{lines}\n"
        f'Just said: "{text}"'
    )


async def judge_addressee(client, heard_before: Sequence[str], text: str, model: str = MODEL) -> Optional[float]:
    """Probability that ``text`` was meant for Pepper, or ``None`` when the model gave no answer (fail open)."""
    answers = await client.ask(model, build_state(heard_before, text), QUESTION)
    try:
        return float(answers["to_pepper"]["noul"]) if answers else None
    except (KeyError, TypeError, ValueError):
        return None


def addressed(probability: Optional[float], threshold: float, someone_looking: bool, looking_threshold: float) -> bool:
    """Answer when the model is confident enough. Someone looking at Pepper lowers the bar: on 2026-10-01 the one
    miss ("Emer, can you come to me", name misheard) came from a person standing in front of Pepper."""
    if probability is None:
        return True  # no judgement: behave as before the gate existed
    return probability >= (looking_threshold if someone_looking else threshold)
