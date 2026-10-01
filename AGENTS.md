# Notes for AI coding assistants

This file is for AI coding assistants (Grok, Codex, Cursor, Claude and others) working on PepperEvolution. The full project guide is [`CLAUDE.md`](CLAUDE.md): architecture, commands, configuration, and what has been verified on the robot. The contribution rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md). Read both before changing code. The points below are the ones most often missed.

1. **Two Pythons.** `robot_bridge/pepper_bridge.py` runs on the robot under Python 2.7 and Tornado 3.1.1: no f-strings, no `async`/`await`, no type hints, no `pathlib`. Everything else is Python 3.12 or later.
2. **The bridge contract lives in four places.** A bridge endpoint change must update the handler, `docs/BRIDGE_API.md`, `src/pepper/bridge_client.py` and `src/pepper/fake_bridge.py` together.
3. **Safety ledger.** Anything the robot does by itself, and every motion bound, has a row in `docs/SAFETY.md`. Add or update it with the change. "Stop" must stop everything; nothing may move after it.
4. **People's data.** Frames and audio stay in memory. Recognising or remembering people (faces, voices, names) needs a design issue first and must be opt-in, local, minimal and forgettable. Never commit `.env`, API keys, photos, recordings or personal data.
5. **Versions.** Host `src/__init__.py` and bridge `BRIDGE_VERSION` must match (a test checks). Leave version bumps and `CHANGELOG.md` to the maintainers unless asked.
6. **Run the checks** before proposing a change: `pytest tests/ -q`, black (120 columns), flake8, mypy; for bridge changes also the Python 2.7 bridge tests (`PEPPER_BRIDGE_PYTHON=... pytest tests/test_bridge_integration.py`).
7. **Say what was tested where.** In a pull request, state whether the change ran on unit tests only, on NAOqi's desktop build, or on a physical Pepper (which one).
