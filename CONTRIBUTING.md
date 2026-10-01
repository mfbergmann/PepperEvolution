# Contributing to PepperEvolution

Thank you for helping. PepperEvolution gives a SoftBank Pepper (NAOqi 2.5) an off-board AI: a small bridge runs on the robot, and everything else (the AI model, speech recognition, the world model) runs on a host computer. Reports from other robots are especially valuable, because every Pepper differs a little (body version, NAOqi build, voices, installed animations, room acoustics).

This guide covers how to report what you find, how to send a change, and the rules a change must keep. If you use an AI coding assistant, point it at [`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md) as well; they hold the same rules in more detail.

## Report what you find (issues)

Open an issue for anything you notice on your robot, even if you are not sure it is a bug. There are templates for:

- **Bug report**: something broke or behaved wrongly.
- **Robot test report**: you ran a session on your Pepper and want to share what worked and what did not.
- **Feature or idea**: something you want Pepper to do, or a change you want to make before writing code.

Useful in every report:

- **Robot**: Pepper body version (e.g. 1.8A), NAOqi version (`curl http://<robot>:8888/health` shows it), bridge version (same output).
- **Host**: OS, Python version, the PepperEvolution version or commit (`git log --oneline -1`).
- **AI model** (`AI_MODEL`, and the provider if it is not Claude) and the speech-to-text backend (`STT_BACKEND`, `STT_MODEL`).
- **Logs**: the host log (`pepper_evolution.log`) and the bridge log (`python robot_bridge/deploy.py --logs`). Remove API keys and anything personal first.
- **The room**: an office, a large open room, how far away people were. People detection, gaze and speech recognition all depend on it.

For a bigger change (a new capability, a new dependency, anything that stores data about people), please open an issue describing the idea first, so we can agree on the design before you spend time on code.

## Send a change (pull requests)

1. Fork the repository on GitHub and clone your fork:
   ```bash
   git clone https://github.com/<your-account>/PepperEvolution.git
   cd PepperEvolution
   ```
2. Set up the host (Python 3.12 or later):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp env.example .env          # then set your API key; .env is never committed
   pytest tests/ -q             # everything should pass without a robot
   ```
3. Create a branch (`git checkout -b fix/head-after-photo`), make the change, add tests.
4. Run the checks below, then open a pull request against `main` and fill in the template. Small, focused pull requests are much easier to review and test on the robot than large ones.

### Checks before a pull request

```bash
pytest tests/ -q                                                   # host and bridge unit tests
black src/ main.py examples/ robot_bridge/ tests/ scripts/ --line-length=120
flake8 src/ main.py examples/ robot_bridge/ tests/ scripts/
mypy src/ main.py --ignore-missing-imports --no-strict-optional
```

If you changed the bridge, also run its tests under Python 2.7 with Tornado 3.1.1, as on the robot (for example a `mise install python@2.7.18` interpreter):

```bash
PEPPER_BRIDGE_PYTHON=/path/to/python2.7 pytest tests/test_bridge_integration.py -q
```

and, if you can, against NAOqi's own desktop build (`scripts/virtual_pepper.sh`, see the comments at the top of the script):

```bash
scripts/virtual_pepper.sh start && scripts/virtual_pepper.sh bridge &
PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:8899 pytest tests/test_virtual_naoqi.py -q
```

Finally, say in the pull request whether you tried it on a physical Pepper, which one, and what you saw. Many problems (timing, sensors, the camera, the microphone) only show up on the hardware.

## Rules a change must keep

### The bridge runs on the robot

`robot_bridge/pepper_bridge.py` runs on Pepper under **Python 2.7 and Tornado 3.1.1**: no f-strings, no `async`/`await`, no type hints, no `pathlib` (a test checks this). Every NAOqi call runs on a worker thread, never on the event loop. When you change what the bridge offers, change it everywhere: the handler, [`docs/BRIDGE_API.md`](docs/BRIDGE_API.md), `src/pepper/bridge_client.py` and `src/pepper/fake_bridge.py`.

### Safety

Pepper is a 28 kg robot that moves among people. [`docs/SAFETY.md`](docs/SAFETY.md) lists every bound the code enforces (speeds, distances, the sonar guard, what the emergency stop does, what runs automatically). A change that adds or alters something the robot does on its own, or changes a bound, must add or update its row there. Never disable NAOqi's collision protection, never wake the robot implicitly, and keep "stop" meaning stop: nothing may move after it.

### Privacy and people's data

Pepper sees and hears people who did not agree to anything, often in public rooms. The rules (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):

- Camera frames and audio stay in memory at runtime. Development recordings may be saved locally for testing, never committed (`results/` and `recordings/` are git-ignored).
- Anything that **recognises or remembers people** (faces, voices, names) needs a design issue first, and should be opt-in per person ("remember me"), off by default, store as little as possible (for example embeddings, not photos), stay local, and have a way to forget ("forget me"). Face and voice data are biometric data, a special category under the GDPR and similar laws.
- No API keys, passwords, personal data, photos or recordings in commits, issues or logs you share.

### Versions

The project is pre-1.0 (`0.MINOR.PATCH`). The host (`src/__init__.py`) and the bridge (`BRIDGE_VERSION`) always carry the same number; a test checks it. Maintainers bump the version and update [`CHANGELOG.md`](CHANGELOG.md) when releasing, so you do not need to.

### Style

Black at 120 columns, flake8, type hints and docstrings in host code, `loguru` for logging. Comments explain why, not what. Keep user-facing text (what Pepper says, the web UI, the docs) plain and short.

## Where help is wanted

The plan is in [`docs/ROADMAP.md`](docs/ROADMAP.md), tracked as [GitHub milestones](https://github.com/mfbergmann/PepperEvolution/milestones) with one issue per piece of work, and summarised in the [wiki](https://github.com/mfbergmann/PepperEvolution/wiki). Good places to start:

- **Testing on your Pepper** and reporting what differs: detection ranges, gaze, voices, animations, timing.
- **Other AI providers**: the host talks to Claude, OpenAI, and any OpenAI-compatible server (`OLLAMA_URL`, e.g. Ollama). Reports on how other models handle Pepper's tools are welcome, especially whether a model describes the room without taking a photo first.
- Anything open under the current milestones.

## Code of conduct

Be kind, assume good intent, and keep discussion about the work. Questions are welcome in issues.

## Security

Please report security problems privately, as described in [`SECURITY.md`](SECURITY.md), not in a public issue.
