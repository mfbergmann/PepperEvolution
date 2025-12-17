# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

PepperEvolution is a cloud-based AI control system for SoftBank Pepper robots. It uses a **bridge service architecture** where a Python 2.7 HTTP server runs on Pepper (handling NAOqi communication), while a modern Python 3.8+ application runs locally with FastAPI, OpenAI/Anthropic integration, and WebSocket support.

## Commands

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp env.example .env  # Then edit with your API keys
```

### Running the Application
```bash
python main.py  # Starts REST API (8000), WebSocket (8765), and AI conversation loop
```

### Bridge Service (on Pepper robot)
```bash
./deploy_bridge.sh          # Deploy bridge to Pepper via SCP
./start_bridge.exp          # Start bridge service on Pepper (requires expect)
./check_bridge.sh           # Check if bridge is running
curl http://10.0.100.100:8888/health  # Manual health check
```

### Testing
```bash
pytest tests/                          # Run all tests
pytest tests/test_basic_imports.py     # Run specific test file
pytest -v                              # Verbose output
```

### Linting
```bash
black src/ tests/ main.py              # Format code
flake8 src/ tests/ main.py             # Lint
mypy src/                              # Type checking
```

## Architecture

### Bridge Service Pattern
The codebase uses two connection modes controlled by `USE_PEPPER_BRIDGE` env var:
- **Bridge mode (default)**: `src/pepper/bridge_client.py` sends HTTP requests to `pepper_bridge.py` running on Pepper
- **Direct mode**: `src/pepper/connection.py` connects via NAOqi SDK (requires SDK installed locally)

### Key Components

**`main.py`** - Application entry point. Initializes:
- `PepperRobot` (robot interface)
- `AIManager` (conversation handling)
- `APIServer` (FastAPI on port 8000)
- `WebSocketServer` (port 8765)

**`src/pepper/`** - Robot interface layer
- `robot.py` - High-level robot API (`speak()`, `listen()`, `move()`)
- `bridge_client.py` - HTTP client for bridge service
- `connection.py` - Connection manager (switches between bridge/direct)

**`src/ai/`** - AI integration
- `manager.py` - Conversation orchestration, action parsing, context management
- `models.py` - `OpenAIProvider` and `AnthropicProvider` implementations

**`src/communication/`** - External interfaces (REST API, WebSocket)

**`pepper_bridge.py`** - Bridge service that runs on Pepper (Python 2.7). Deploys to `/home/nao/` on robot.

### Action Tag System
AI responses can include action tags that the `AIManager` parses and executes:
- `[MOVE:forward:0.5]` - Move robot
- `[GESTURE:wave]` - Play animation
- `[LED:eyes:blue]` - Set LED color
- `[STOP]` - Stop motion

## Environment Variables

Required:
- `OPENAI_API_KEY` - For GPT models
- `USE_PEPPER_BRIDGE=true` - Enable bridge mode
- `PEPPER_BRIDGE_URL=http://10.0.100.100:8888` - Bridge service URL

Optional:
- `AI_MODEL=gpt-4` - AI model selection (gpt-* or claude-*)
- `ENABLE_SPEECH_CONVERSATION=true` - Enable continuous speech listening
- `API_PORT=8000`, `WEBSOCKET_PORT=8765` - Server ports

## Testing Notes

- Tests use mock NAOqi via `tests/conftest.py` since the real SDK isn't available in CI
- Bridge client tests should mock HTTP responses
- Use `pytest-asyncio` for async test functions
