
# PepperEvolution v2

A cloud-based AI control system for SoftBank Pepper robots. A bridge server on the robot wraps NAOqi as HTTP/WebSocket endpoints; the host application connects via async HTTP and drives conversations with Anthropic Claude using native tool calling.

## Overview

PepperEvolution transforms Pepper into an AI-powered companion:

- **Bridge architecture** - No NAOqi SDK needed on the host. A Python 2.7 Tornado server on the robot exposes all hardware as REST endpoints.
- **AI tool calling** - Anthropic Claude uses structured tool calls (speak, move, gesture, photo, sensors) instead of regex-parsed action tags.
- **Real-time events** - WebSocket push from the robot for touch, sonar, battery, and people detection events.
- **Web interface** - Browser-based control panel with chat, direct controls, and live event stream.

## Prerequisites

- Pepper robot with NAOqi 2.5 on the network
- Python 3.12+ on the host (no NAOqi SDK needed)
- Anthropic API key (or OpenAI API key)
- SSH access to the robot (nao/nao)

## Quick Start

```bash
# Install
git clone https://github.com/mfbergmann/PepperEvolution.git
cd PepperEvolution
pip install -r requirements.txt

# Configure
cp env.example .env
# Edit .env: set PEPPER_IP, ANTHROPIC_API_KEY

# Deploy bridge and start
./scripts/start.sh
```

Or manually:
```bash
# Deploy bridge to robot
python robot_bridge/deploy.py

# Verify bridge
curl http://10.0.100.100:8888/health

# Start host application
python main.py
```

Then open `examples/web_interface.html` or use the API:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Wave at me and say hello!"}'
```

## Architecture

```
Pepper Robot (NAOqi 2.5)          Host (Python 3.12+)
┌──────────────────────┐  HTTP   ┌──────────────────────────┐
│ pepper_bridge.py     │◄───────►│ BridgeClient (httpx)     │
│ Tornado :8888        │         │ EventStream (websockets)  │
│ REST + /ws/events    │  WS     │ AIManager + ToolExecutor  │
└──────────────────────┘◄───────►│ FastAPI :8000             │
                                 │ WebSocket :8765           │
                                 └──────────────────────────┘
                                         ▲
                                         │ Tool calling
                                  Anthropic Claude API
```

## Project Structure

```
PepperEvolution/
├── robot_bridge/
│   ├── pepper_bridge.py    # Bridge server (runs on robot, Python 2.7)
│   └── deploy.py           # SCP + SSH deploy script
├── src/
│   ├── pepper/             # BridgeClient, EventStream, PepperConnection, PepperRobot
│   ├── ai/                 # AIProvider, tools.py, ToolExecutor, AIManager
│   ├── communication/      # FastAPI server, WebSocket server
│   ├── sensors/            # SensorManager (reads from bridge)
│   └── actuators/          # ActuatorManager (sends to bridge)
├── tests/                  # 107 tests (respx-mocked, no robot needed)
├── examples/
│   └── web_interface.html  # Browser control panel
├── scripts/
│   └── start.sh            # Deploy + start convenience script
├── docs/
│   ├── GETTING_STARTED.md
│   └── BRIDGE_API.md       # Full bridge endpoint reference
└── main.py                 # Host application entry point
```

## AI Tools

The AI can call these tools during conversation:

| Tool | Description |
|------|-------------|
| `speak` | Text-to-speech (with optional animated gestures) |
| `move_forward` | Move forward/backward (clamped to 2m) |
| `turn` | Turn left/right (clamped to 180 degrees) |
| `move_head` | Look in a direction |
| `set_posture` | Stand, Crouch, StandInit, StandZero |
| `play_animation` | Wave, nod, and other gestures |
| `set_eye_color` | Change eye LED color |
| `take_photo` | Camera snapshot |
| `get_sensors` | Battery, touch, sonar, people count |
| `emergency_stop` | Stop all movement, disable motors |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PEPPER_IP` | `10.0.100.100` | Robot IP |
| `BRIDGE_PORT` | `8888` | Bridge port |
| `BRIDGE_API_KEY` | | Optional auth |
| `AI_MODEL` | `claude-sonnet-4-5-20250929` | AI model |
| `ANTHROPIC_API_KEY` | | Required for Claude |
| `OPENAI_API_KEY` | | Required for GPT |
| `API_PORT` | `8000` | Host REST port |
| `WEBSOCKET_PORT` | `8765` | Host WS port |

## Testing

```bash
pytest tests/ -v --tb=short    # 107 tests, all pass without a robot
```

## Credits

PepperEvolution is a research project from [TRiPL Lab](https://tripl.ca/), Toronto Metropolitan University.

## License

MIT License. See [LICENSE](LICENSE).
