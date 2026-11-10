"""
The orchestrator (docs/ORCHESTRATOR.md): which engine handles what in a turn, on the record, within the data policy.

- ``engines``: the registry of destinations and what data each is cleared for (default-deny).
- ``manifest``: a session's declared presence (solo, others, unknown).
- ``ledger``: turn ids, what the mind was shown and what it cost, and outcomes that become known later.
- ``route``: the per-turn decision as pure functions.
"""
