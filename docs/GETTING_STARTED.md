# Getting Started with PepperEvolution v2

## Prerequisites

### Hardware
- SoftBank Pepper robot (NAOqi 2.5) on the network
- Host computer with Python 3.12+ (does NOT need NAOqi SDK)

### Software
- Python 3.12 or 3.13
- Anthropic API key (or OpenAI API key)
- SSH access to the robot (default: `nao@<PEPPER_IP>`, password: `nao`)

## Installation

### 1. Clone and Install

```bash
git clone https://github.com/mfbergmann/PepperEvolution.git
cd PepperEvolution
pip install -r requirements.txt
```

### 2. Configure

```bash
cp env.example .env
# Edit .env with your settings:
#   PEPPER_IP=10.0.100.100
#   ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Deploy the Bridge to the Robot

The bridge server is a single Python 2.7 script that runs on the robot and wraps NAOqi as HTTP endpoints.

```bash
python robot_bridge/deploy.py
```

This will:
1. SSH into the robot
2. Copy `pepper_bridge.py` to `/home/nao/pepper_bridge/`
3. Start the bridge on port 8888

Verify it's running:
```bash
curl http://10.0.100.100:8888/health
# Should return: {"ok": true, "bridge": "pepper_bridge", "version": "2.0.0", ...}
```

### 4. Start the Host Application

```bash
python main.py
```

Or use the convenience script that deploys the bridge and starts the host:
```bash
./scripts/start.sh
```

## Quick Test

```bash
# Make the robot speak
curl -X POST http://10.0.100.100:8888/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, I am Pepper!"}'

# Chat with AI (through host app)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Wave at me and say hello!"}'
```

## Web Interface

Open `examples/web_interface.html` in your browser. It connects to:
- `http://localhost:8000` — REST API for chat and commands
- `ws://localhost:8765` — WebSocket for real-time robot events

Features:
- AI chat with tool-call visualization
- Direct robot controls (speak, move, gestures, LEDs)
- Live status and sensor data
- Robot event stream (touch, sonar, battery)

## Architecture

```
You (Web UI / curl)
    │
    ▼
Host Application (Python 3.12+)
    ├── FastAPI on :8000 (REST API)
    ├── WebSocket on :8765 (real-time events)
    ├── AIManager (Anthropic Claude with tool calling)
    └── BridgeClient (httpx → robot)
            │
            ▼
    Bridge Server on Robot (Python 2.7, Tornado :8888)
            │
            ▼
    NAOqi 2.5 Services (motion, TTS, sensors, camera, ...)
```

The AI uses **tool calling** (not regex parsing). When you say "wave at me", Claude calls the `play_animation` tool with the wave animation path. The `ToolExecutor` sends the command to the bridge, which calls NAOqi.

## AI Tools Available

| Tool | Description |
|------|-------------|
| `speak` | Text-to-speech |
| `move_forward` | Move forward/backward |
| `turn` | Turn left/right |
| `move_head` | Look in a direction |
| `set_posture` | Stand, Crouch, etc. |
| `play_animation` | Wave, nod, gestures |
| `set_eye_color` | Change eye LED color |
| `take_photo` | Camera snapshot |
| `get_sensors` | Battery, touch, sonar |
| `emergency_stop` | Stop everything |

## Running Tests

```bash
pytest tests/ -v --tb=short
```

Tests use `respx` to mock HTTP calls to the bridge. No robot or NAOqi needed.

## Troubleshooting

**Bridge not reachable:**
- Check `PEPPER_IP` in `.env`
- SSH into robot and check: `ps aux | grep pepper_bridge`
- Check bridge log: `cat /home/nao/pepper_bridge/bridge.log`

**AI not responding:**
- Verify `ANTHROPIC_API_KEY` is set and valid
- Check `AI_MODEL` matches your API key (Claude models need Anthropic key)

**Robot not moving:**
- Run `curl -X POST http://10.0.100.100:8888/wake_up` to enable motors
- Check battery: `curl http://10.0.100.100:8888/status`

## Safety

- Always keep the emergency stop accessible
- Test movements in open areas first
- Monitor battery and temperature via the status endpoint
- The AI has safety limits: movement clamped to 2m, angles to 180 degrees
