"""
The evaluation set (docs/ORCHESTRATOR.md, phase 2): which turns a replay of the routing may later be judged on.

Only reviewed-solo sessions (``session.json`` says ``solo``, src/orchestra/manifest.py) and turns a person
addressed: a turn enters when it has outcomes in ``outcomes.jsonl``, when a reader marked it, or when Pepper
acted on it (any tool called; such turns carry the extra weight, because they are the ones a routing change
can undo). Each key sits on one side of the split, decided once and for all from the key itself
(``split_for``); a key already in a built set keeps that side and stays in the set for all later builds —
the held-out share must not move out from under a comparison that was made on it.

The set holds keys and flags only, never what anyone said: ``EvalTurn`` has no field for the text, so
``write`` cannot put it in. Session records stay on the host machine (CLAUDE.md, "Session records"); this
file is the aggregate the orchestrator keeps and the public repo may carry.
"""

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List, Optional, Set

from .engines import SOLO
from .manifest import presence

HELD_OUT = "held_out"
DEV = "dev"
PERSON_SOURCES = ("voice", "user")  # sensor events and initiatives are not a person addressing Pepper


@dataclass(frozen=True)
class EvalTurn:
    key: str
    session: str
    source: str
    has_outcomes: bool
    marked: bool
    action: bool
    weight: float
    split: str


def turn_key(session: str, record: Dict[str, Any]) -> str:
    """The turn's key: its ``turn_id`` when the ledger gave it one, else the old ``<session>@<time>`` form."""
    return record.get("turn_id") or f"{session}@{record.get('at', '')}"


def _lines(path: str) -> List[Dict[str, Any]]:
    """JSON lines of a record file; a missing file is an empty list, a torn line is skipped, never fatal."""
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_turns(folder: str) -> List[Dict[str, Any]]:
    return _lines(os.path.join(folder, "turns.jsonl"))


def load_outcomes(folder: str) -> Dict[str, List[str]]:
    """Outcome kinds by turn key; a missing file is simply nothing known yet."""
    out: Dict[str, List[str]] = {}
    for row in _lines(os.path.join(folder, "outcomes.jsonl")):
        key = row.get("turn_id")
        if key:
            out.setdefault(key, []).append(row.get("kind", ""))
    return out


def load_marked_keys(path: str) -> Set[str]:
    """Turn keys a reader marked (marks.jsonl); a missing file is an empty set."""
    return {row["turn_id"] for row in _lines(path) if row.get("turn_id")}


def split_for(key: str, fraction: float = 0.3, salt: str = "evalset-v1") -> str:
    """Which side of the split a key sits on, decided once from the key and the salt, the same way on
    every machine and every run. A salted SHA-256 read as an integer, scaled against ``fraction`` — so
    about that fraction of keys lands on the held-out side, and the same key always lands on the same side.
    """
    value = int.from_bytes(hashlib.sha256(f"{salt}{key}".encode("utf-8")).digest()[:4], "big")
    return HELD_OUT if value < fraction * 2**32 else DEV


def build(
    root: str,
    marks_path: str,
    previous: Optional[List[EvalTurn]] = None,
    action_weight: float = 2.0,
    fraction: float = 0.3,
) -> List[EvalTurn]:
    """The evaluation set for a tree of session folders (results/sessions), sorted by key.

    Only ``solo`` sessions and person turns enter, on an outcome, a reader's mark, or an action. With
    ``previous`` the earlier set's keys keep their splits and stay in the set even when they no longer
    qualify (the session left solo, the folder moved); new keys are added, keys are never dropped.
    """
    fixed = {t.key: t for t in previous or []}
    marked = load_marked_keys(marks_path)
    turns: List[EvalTurn] = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            folder = os.path.join(root, name)
            if not os.path.isdir(folder) or presence(folder) != SOLO:
                continue  # presence is declared, never assumed: solo only until the tester says so
            outcomes = load_outcomes(folder)
            for record in load_turns(folder):
                source = record.get("source")
                if source not in PERSON_SOURCES:
                    continue
                key = turn_key(name, record)
                action = bool(record.get("tools"))
                has_outcomes = key in outcomes
                is_marked = key in marked
                if key in fixed:
                    base = fixed[key]
                    if has_outcomes or is_marked or action:
                        turns.append(
                            replace(
                                base,
                                session=name,
                                source=source,
                                has_outcomes=has_outcomes,
                                marked=is_marked,
                                action=action,
                                weight=action_weight if action else 1.0,
                            )
                        )
                    else:
                        turns.append(base)  # the set never shrinks: it stays even if the session left solo
                    continue
                if has_outcomes or is_marked or action:
                    turns.append(
                        EvalTurn(
                            key,
                            name,
                            source,
                            has_outcomes,
                            is_marked,
                            action,
                            action_weight if action else 1.0,
                            split_for(key, fraction),
                        )
                    )
    by_key: Dict[str, EvalTurn] = {}
    for t in turns:
        by_key.setdefault(t.key, t)
    for key, base in fixed.items():
        by_key.setdefault(key, base)  # keys never drop, even when their session is gone from the tree
    return [by_key[key] for key in sorted(by_key)]


def write(turns: List[EvalTurn], path: str):
    """The set as JSONL, one key and its flags per line, mode 0o600, parent folder created.

    ``EvalTurn`` has no field for the text of what anyone said, so it cannot end up here — that stays in
    the session records, on the host machine.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read(path: str) -> List[EvalTurn]:
    """A set written by ``write``; a missing file is an empty set, a bad line is skipped."""
    fields = {f.name for f in EvalTurn.__dataclass_fields__.values()}
    out: List[EvalTurn] = []
    for row in _lines(path):
        data = {k: row[k] for k in fields if k in row}
        if len(data) == len(fields):
            out.append(EvalTurn(**data))
    return out


def summary(turns: List[EvalTurn]) -> Dict[str, Any]:
    """Aggregates only — counts by side and by reason, and the weight per side: nothing that names a turn.

    A turn can count under several reasons at once; the reasons are what put it in, not a partition.
    """
    by_split = {HELD_OUT: 0, DEV: 0}
    weight = {HELD_OUT: 0.0, DEV: 0.0}
    reasons = {"outcomes": 0, "marked": 0, "action": 0}
    for t in turns:
        if t.split in by_split:
            by_split[t.split] += 1
            weight[t.split] += t.weight
        if t.has_outcomes:
            reasons["outcomes"] += 1
        if t.marked:
            reasons["marked"] += 1
        if t.action:
            reasons["action"] += 1
    return {"total": len(turns), "by_split": by_split, "weight": weight, "by_reason": reasons}
