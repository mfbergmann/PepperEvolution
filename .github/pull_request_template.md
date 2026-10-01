**What this changes and why**
Link the issue if there is one (`Fixes #...`).

**Tested on**
- [ ] Unit tests: `pytest tests/ -q`
- [ ] Bridge under Python 2.7 (`PEPPER_BRIDGE_PYTHON=... pytest tests/test_bridge_integration.py`), if the bridge changed
- [ ] NAOqi's desktop build (`scripts/virtual_pepper.sh`, `tests/test_virtual_naoqi.py`)
- [ ] A physical Pepper: body version, NAOqi version, what you saw

**Checklist**
- [ ] black, flake8 and mypy pass
- [ ] Bridge changes are Python 2.7 compatible and updated in all four places (handler, `docs/BRIDGE_API.md`, `bridge_client.py`, `fake_bridge.py`)
- [ ] Anything the robot does by itself, or a changed bound, has its row in `docs/SAFETY.md`
- [ ] No API keys, personal data, photos or recordings in the change
- [ ] Anything that stores or recognises people was discussed in an issue first (see `CONTRIBUTING.md`)
