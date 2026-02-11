# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PepperEvolution v2 is a cloud-based AI control system for SoftBank Pepper robots. A **bridge server** (Python 2.7/Tornado) runs on the robot and wraps NAOqi as HTTP/WebSocket endpoints. The **host application** (Python 3.12+) connects to the bridge via `httpx` and orchestrates AI-powered conversations using Anthropic Claude with tool calling.

Built at TRiPL Lab, Toronto Metropolitan University. Robot is at `10.0.100.100`.

## Commands

### Install
```bash
pip install -r requirements.txt
pip install -e .          # editable install
```

### Deploy Bridge to Robot
```bash
python robot_bridge/deploy.py --host 10.0.100.100
```

### Run
```bash
python main.py                        # full application (requires bridge running)
./scripts/start.sh                    # deploy bridge + start host
./scripts/start.sh --no-deploy        # start host only
```

### Test
```bash
pytest tests/ -v --tb=short           # all tests (107 tests, no robot needed)
pytest tests/ -k test_bridge_client   # specific tests by name
```

### Lint / Format / Type-check
```bash
flake8 src/ --max-line-length=120
black src/ --line-length=120
mypy src/ --ignore-missing-imports --no-strict-optional
```

## Architecture

```
Pepper Robot (NAOqi 2.5, Python 2.7)     Host (Python 3.12+)
┌─────────────────────────┐    HTTP     ┌───────────────────────────┐
│  pepper_bridge.py       │◄───────────►│  BridgeClient (httpx)     │
│  Tornado on port 8888   │             │  EventStream (websockets) │
│  - REST endpoints       │  WebSocket  │  PepperRobot              │
│  - /ws/events push      │◄───────────►│  AIManager + ToolExecutor │
└─────────────────────────┘             │  FastAPI on :8000         │
                                        │  WebSocket on :8765       │
                                        └───────────────────────────┘
                                                    ▲
                                                    │
                                          Anthropic Claude API
                                          (tool calling)
```

### Source layout

- **robot_bridge/** — Python 2.7 Tornado server (`pepper_bridge.py`) + deploy script (`deploy.py`). Runs on the robot.
- **src/pepper/** — `BridgeClient` (async HTTP), `EventStream` (WebSocket listener), `PepperConnection`, `PepperRobot` (high-level interface), `errors.py`.
- **src/ai/** — `AIProvider` ABC with `AnthropicProvider` (primary) and `OpenAIProvider`. `tools.py` defines tool schemas. `ToolExecutor` dispatches tool calls to robot. `AIManager` runs multi-turn tool-calling loops.
- **src/communication/** — `APIServer` (FastAPI REST) and `WebSocketServer` (websockets). Routes: `/chat`, `/status`, `/command/{cmd}`, `/tools`.
- **src/sensors/** — `SensorManager` reads from bridge `/sensors` endpoint.
- **src/actuators/** — `ActuatorManager` sends commands to bridge endpoints.

### Key patterns

- **Bridge pattern** — No NAOqi bindings on host. All robot interaction goes through HTTP to the bridge.
- **Tool calling** — AI uses Anthropic's native tool-use API. No regex parsing. Tools defined in `src/ai/tools.py`, executed by `ToolExecutor`.
- **Multi-turn loops** — `AIManager.process_user_input()` loops: AI calls tools → executor runs them → results fed back → AI responds.
- **Async throughout** — `httpx.AsyncClient`, `asyncio`, `websockets`.
- **respx mocking** — Tests mock HTTP calls with `respx`. No MockQi hack.

## Configuration

Copy `env.example` to `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PEPPER_IP` | `10.0.100.100` | Robot IP address |
| `BRIDGE_PORT` | `8888` | Bridge server port |
| `BRIDGE_API_KEY` | (empty) | Optional bridge auth key |
| `AI_MODEL` | `claude-sonnet-4-5-20250929` | AI model (`claude-*` or `gpt-*`) |
| `ANTHROPIC_API_KEY` | | Required for Claude models |
| `OPENAI_API_KEY` | | Required for GPT models |
| `API_PORT` | `8000` | Host FastAPI port |
| `WEBSOCKET_PORT` | `8765` | Host WebSocket port |

## Code Standards

- Python 3.12+ (CI tests 3.12, 3.13)
- Max line length: **120**
- Formatter: **black**; linter: **flake8**; type checker: **mypy**
- Logging: **loguru** (not stdlib logging)
- Tests: **pytest** + **pytest-asyncio** + **respx**
- No NAOqi SDK on host — everything goes through the bridge
