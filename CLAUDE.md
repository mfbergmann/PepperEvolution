# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PepperEvolution v2.1 is a cloud-AI control system for SoftBank Pepper robots. A **bridge server** (Python 2.7 + the Tornado 3.1.1 that ships with NAOqi 2.5) runs on the robot and wraps NAOqi as JSON-over-HTTP endpoints plus a WebSocket event push. The **host application** (Python 3.12+) talks to the bridge with `httpx`, drives conversations with Anthropic Claude using native tool calling, and speaks the model's reply through the robot sentence by sentence as it streams in.

Built at TRiPL Lab, Toronto Metropolitan University. Robot is at `10.0.100.100` (ssh `nao`/`nao`).

## Commands

### Install
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Run
```bash
python robot_bridge/deploy.py             # upload + start the bridge on the robot, wait for /health
python robot_bridge/deploy.py --status    # is the bridge up?
python robot_bridge/deploy.py --logs      # tail bridge.log on the robot
python main.py                            # host app: REST + WebSocket + web UI on http://localhost:8000
./scripts/start.sh                        # deploy bridge, then start host
./scripts/start.sh --fake                 # no robot: FakeBridgeClient + real AI + web UI
python examples/basic_chat.py             # terminal chat (PEPPER_FAKE_BRIDGE=true works here too)
python examples/event_monitor.py          # print live touch/sonar/battery events
```

### Test
```bash
pytest tests/ -q                                   # ~200 tests, no robot needed (~7 s)
pytest tests/test_bridge_server.py -q              # bridge Robot facade against a fake NAOqi
pytest tests/test_bridge_integration.py -q         # starts the real bridge process with tests/fakenaoqi
PEPPER_BRIDGE_PYTHON=/path/to/python2.7 pytest tests/test_bridge_integration.py   # under the robot's interpreter
PEPPER_SKIP_INTEGRATION=1 pytest tests/            # unit tests only
```
A Python 2.7.18 with Tornado 3.1.1 for the last command can be built with `mise install python@2.7.18` then `pip install tornado==3.1.1 "pillow<7"` into it.

### Lint / Format / Type-check
```bash
black src/ main.py examples/ robot_bridge/deploy.py tests/ --line-length=120
flake8 src/ main.py examples/ robot_bridge/deploy.py tests/      # config in .flake8
mypy src/ --ignore-missing-imports --no-strict-optional
```

## Architecture

```
Pepper Robot (NAOqi 2.5, Python 2.7)         Host (Python 3.12+)
┌────────────────────────────────┐   HTTP    ┌──────────────────────────────────────┐
│ robot_bridge/pepper_bridge.py  │◄─────────►│ BridgeClient (httpx)                 │
│ Tornado 3.1.1 on :8888         │           │ EventStream (websockets)             │
│  - every NAOqi call on a       │ WebSocket │ PepperRobot ─ SensorManager/Actuators│
│    worker thread (never blocks)│◄─────────►│ AIManager ─ ToolExecutor ─ Speech    │
│  - /ws/events push (touch,     │           │ FastAPI on :8000 (REST + /ws + UI)   │
│    bumper, sonar, battery,     │           └──────────────────────────────────────┘
│    people, speech)             │                           ▲ streaming + tool calling
│  - /tablet/page served to the  │                    Anthropic Claude API
│    chest tablet                │
└────────────────────────────────┘
```

### Source layout

- **robot_bridge/** — `pepper_bridge.py` (runs on the robot; `Robot` facade + Tornado handlers) and `deploy.py` (paramiko upload/start/stop/status/logs).
- **src/pepper/** — `BridgeClient` (async HTTP, one method per endpoint), `FakeBridgeClient` (in-memory double), `EventStream` (reconnecting WebSocket listener), `PepperConnection`, `PepperRobot` (high-level API; methods raise `BridgeError`), `Photo`, `PrepareOptions`.
- **src/ai/** — `tools.py` (tool schemas + `KNOWN_ANIMATIONS`), `models.py` (`AnthropicProvider` with streaming/effort/caching, `OpenAIProvider`, `SYSTEM_PROMPT`), `speech.py` (sentence splitting, markdown/emoji cleanup, `SpeechStreamer`), `tool_executor.py` (`ToolOutcome` incl. image tool results), `manager.py` (`AIManager` turn loop, history trimming, touch reactions).
- **src/communication/** — `api.py`: `create_app()` (FastAPI routes, `/ws`, `WebSocketHub`), `execute_command()` (direct commands), `APIServer` (uvicorn).
- **src/sensors/**, **src/actuators/** — thin boolean-returning wrappers kept for convenience.
- **web/index.html** — single-file control panel served at `/`.
- **tests/fakenaoqi/qi.py** — fake `qi` module so the bridge can run off-robot.

### Key patterns

- **Bridge pattern** — No NAOqi bindings on host. All robot interaction goes through HTTP to the bridge. Endpoints are documented in `docs/BRIDGE_API.md`; keep `BridgeClient`, `FakeBridgeClient`, the bridge handlers and that doc in sync.
- **Non-blocking bridge** — Handlers call `run_in_thread()`; NAOqi work happens on a thread and the reply is finished via `IOLoop.add_callback`. `/emergency_stop` and `/sensors` therefore work while the robot is talking or driving. Only `main_ioloop()` may be touched from threads. The server listens before NAOqi is connected (`/health` → 503 `naoqi_connecting` meanwhile).
- **Safety invariants (bridge)** — motion never wakes the robot implicitly (`_ensure_awake` raises); `/move/*` refuse when the sonar in the direction of travel is under 0.45 m unless `force`; `/emergency_stop` stops behaviours + speech + motion, rests, and sets `halted` until `/wake_up` or `/prepare`; `/stop` also stops running animations; SIGTERM halts the robot before exit. The host mirrors `halted` on `PepperRobot` and ends the AI turn when it is set.
- **Reply text is speech** — The model's reply is streamed; `SpeechStreamer` splits it into sentences and sends each to `/speak` (animated) in order; the `speak` tool is for talking *before* a slow action. Animation tags like `^start(animations/Stand/Gestures/Hey_1)` are spoken by ALAnimatedSpeech and stripped for display.
- **Tool calling** — Anthropic native tool use; manual loop in `AIManager._run_turn()` (up to `MAX_TOOL_ROUNDS = 10`). The provider's raw content blocks (`AIResponse.content`, including thinking blocks) are replayed verbatim as the assistant turn so Claude's tool rounds stay valid. `take_photo` returns the JPEG as an image block inside the `tool_result`, so the model actually sees the picture. A "phantom" tool call (stop_reason `tool_use` with no `tool_use` blocks, or XML in the text) is never spoken and is retried once; a response cut off by `max_tokens` never has its tool calls executed; refusals are spoken. Speech is drained before tools run so the preamble finishes before the robot moves.
- **History** — Trimmed at user-text boundaries only (tool_use/tool_result pairs stay intact); older photos are replaced by a text placeholder (`image_history`).
- **Prompt caching** — `SYSTEM_PROMPT` is the first system block with `cache_control`; the dynamic robot-state block comes after it.
- **Effort** — `output_config.effort` is only sent for models that support it (`supports_effort()`); default `low` for snappy conversation.
- **Events** — Bridge pushes edge-triggered events; `AIManager.handle_event()` turns head/hand touches and bumpers into a short spoken reaction (`REACT_TO_TOUCH`, 8 s cooldown, skipped while a turn is running).
- **Python 2.7 constraint** — `robot_bridge/pepper_bridge.py` runs on the robot under Python 2.7 / Tornado 3.1.1: no f-strings, no `async/await`, no type hints, no `pathlib`, `%`-formatting only. `tests/test_bridge_server.py` enforces this with an AST check and the module must also import under Python 3 for tests.
- **pytest** — `asyncio_mode = "auto"`; fixtures in `tests/conftest.py`: `mock_connection`/`mock_robot` (AsyncMock bridge), `fake_robot` (real `PepperRobot` + `FakeBridgeClient`), `mock_ai_provider`, `mock_ai_manager`.

## Configuration

Copy `env.example` to `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PEPPER_IP` | `10.0.100.100` | Robot IP address |
| `PEPPER_USER` / `PEPPER_PASSWORD` | `nao` / `nao` | SSH login for `deploy.py` |
| `BRIDGE_PORT` | `8888` | Bridge server port |
| `BRIDGE_API_KEY` | (empty) | Optional bridge auth key (`X-API-Key` / `?api_key=`) |
| `BRIDGE_ACTION_TIMEOUT` | `120` | Seconds to wait for speech/motion/animation calls |
| `PEPPER_FAKE_BRIDGE` | `false` | Use `FakeBridgeClient` instead of a robot |
| `PREPARE_ON_CONNECT` | `true` | On connect: set Autonomous Life, wake up |
| `PEPPER_AUTONOMOUS_LIFE` | `disabled` | State to set on connect (`keep` = leave alone) |
| `REST_ON_EXIT` | `false` | Put the robot to rest when the host exits |
| `AI_MODEL` | `claude-opus-5` | `claude-*` (Anthropic) or `gpt-*` (OpenAI) |
| `AI_EFFORT` | `low` | Claude effort for 4.6+ models: low/medium/high |
| `AI_MAX_TOKENS` | `16000` | Per-response token cap (thinking counts against it; responses stream) |
| `ANTHROPIC_API_KEY` | | Required for Claude models |
| `SPEAK_RESPONSES` | `true` | Speak replies aloud while streaming |
| `TABLET_SUBTITLES` | `true` | Show spoken sentences on the chest tablet (auto-off after an error) |
| `REACT_TO_TOUCH` | `true` | React aloud to touch/bumper events |
| `API_PORT` | `8000` | Host port (REST + WebSocket + web UI) |

## Reference Docs

- `docs/BRIDGE_API.md` — Full HTTP/WebSocket endpoint reference for the bridge server
- `docs/GETTING_STARTED.md` — Setup guide, first-test checklist and troubleshooting
- `docs/ROADMAP.md` — Milestones (M0 first live session checklist, reactive layer, voice input, vision grounding, memory) and non-goals
- `docs/RESEARCH_2026-09.md` — Why the off-board bridge architecture, what robot foundation models do and do not offer Pepper, prior Pepper/NAO LLM work with sources

## Code Standards

- Python 3.12+ on the host (CI tests 3.12, 3.13); Python 2.7 for `robot_bridge/pepper_bridge.py`
- Max line length: **120**; formatter **black**; linter **flake8** (`.flake8`); type checker **mypy**
- Logging: **loguru** on the host (stdlib `logging` in the bridge)
- Tests: **pytest** + **pytest-asyncio** + **respx**; the bridge is tested off-robot with `tests/fakenaoqi`
- No NAOqi SDK on host — everything goes through the bridge
