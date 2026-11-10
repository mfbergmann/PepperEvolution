"""
A session's declared presence (docs/ORCHESTRATOR.md): ``session.json`` in each session folder.

Presence is declared, never assumed: a new session is ``unknown`` until the tester reviews it and marks it ``solo``
(the tester alone with Pepper) or ``others`` (anyone else was there, or may have been). Anything that sends session
data to a destination cleared for solo sessions only must check it here first.

    .venv/bin/python scripts/mark_session.py results/sessions/2026-11-05_154622 solo --note "alone in the office"
"""

import json
import os
from datetime import datetime
from typing import Any, Dict

from .engines import PRESENCE, UNKNOWN, PolicyError, allowed

NAME = "session.json"


def read(folder: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(folder, NAME), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"presence": UNKNOWN}
    if data.get("presence") not in PRESENCE:
        data["presence"] = UNKNOWN
    return data


def presence(folder: str) -> str:
    return read(folder)["presence"]


def start(folder: str, **data: Any):
    """At the start of a run: an ``unknown`` manifest, unless one is already there."""
    path = os.path.join(folder, NAME)
    if os.path.exists(path):
        return
    _write(folder, {"presence": UNKNOWN, "started": datetime.now().isoformat(timespec="seconds"), **data})


def mark(folder: str, value: str, note: str = "", by: str = "tester") -> Dict[str, Any]:
    if value not in PRESENCE:
        raise ValueError(f"presence must be one of {PRESENCE}")
    data = read(folder)
    history = data.get("history", [])
    history.append({"presence": data["presence"], "until": datetime.now().isoformat(timespec="seconds")})
    data.update(presence=value, marked=datetime.now().isoformat(timespec="seconds"), marked_by=by, history=history)
    if note:
        data["note"] = note
    _write(folder, data)
    return data


def require(folder: str, engine: str, classes=("text",)):
    """Raise PolicyError unless this session's data of these classes may go to this engine."""
    where = presence(folder)
    if not allowed(engine, classes, where):
        raise PolicyError(
            f"{os.path.basename(folder.rstrip('/'))}: presence {where!r}; {engine} is not cleared for "
            f"{'/'.join(classes)} from it (docs/DATA_FLOWS.md; mark it with scripts/mark_session.py after review)"
        )


def _write(folder: str, data: Dict[str, Any]):
    path = os.path.join(folder, NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
