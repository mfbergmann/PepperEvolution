# PepperEvolution v2.1

A cloud-AI control system for SoftBank Pepper robots. A small bridge server on the robot wraps NAOqi as HTTP/WebSocket endpoints; the host application connects over the network, drives conversations with Anthropic Claude using native tool calling, and speaks the reply through the robot as it streams in.

## What it does

- **Talks like a person in the room** – the model's reply is streamed, split into sentences, and spoken by Pepper (animated speech, with inline gesture tags) while the rest is still being generated. Subtitles appear on the chest tablet.
- **Acts** – Claude calls tools to wave, bow, nod, look around, turn, drive short distances, change eye colour, use the tablet, or stop.
- **Sees** – `take_photo` returns the camera image to the model, so "what do you see?" gets a real answer.
- **Feels** – head/hand touches and bumpers stream from the robot and trigger short reactions.
- **Stays safe** – moves are clamped and sonar-checked, the bridge never blocks so emergency stop always gets through, and the robot is put into a known state (Autonomous Life off, motors on) when the host connects.
- **Works without the robot** – `PEPPER_FAKE_BRIDGE=true` runs the whole stack with a simulated robot.

## Prerequisites

- Pepper robot with NAOqi 2.5 on the network (default `10.0.100.100`, ssh `nao`/`nao`)
- Python 3.12+ on the host (no NAOqi SDK needed)
- Anthropic API key (or OpenAI API key)

## Quick start

```bash
git clone https://github.com/mfbergmann/PepperEvolution.git
cd PepperEvolution
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env            # set PEPPER_IP and ANTHROPIC_API_KEY

./scripts/start.sh --fake      # try it without a robot: http://localhost:8000
./scripts/start.sh             # deploy the bridge to Pepper and start the host
```

Or step by step:

```bash
python robot_bridge/deploy.py                  # upload + start the bridge, wait for /health
curl http://10.0.100.100:8888/status           # bridge answers directly
python main.py                                 # host app on http://localhost:8000
python examples/basic_chat.py                  # or chat from the terminal
```

Then open the web UI, or:

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "Wave at me and say hello!"}'
```

See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for a first-test checklist and troubleshooting, [docs/BRIDGE_API.md](docs/BRIDGE_API.md) for every bridge endpoint, and [docs/ROADMAP.md](docs/ROADMAP.md) for where the project is going.

## Why this architecture

Pepper's brain stays off-board: the robot exposes its hardware as documented, safety-bounded tools over the bridge, Claude plans and talks, and NAOqi's own skills and reflexes do the fast low-level work. A September 2026 survey of robot foundation models (NVIDIA GR00T, ByteDance GR-3, Gemini Robotics, pi0 and others) and of every recent Pepper/NAO language-model system confirmed this is the right shape for Pepper: those models drive manipulators with joint-level policies, need per-robot demonstration data and an on-board GPU, and none of that applies to a wheeled social robot with gesture arms. What does transfer (a reactive layer, point-based vision grounding, voice input, memory) is on the roadmap. Details and sources: [docs/RESEARCH_2026-09.md](docs/RESEARCH_2026-09.md).

## Architecture

```
Pepper Robot (NAOqi 2.5, Python 2.7)        Host (Python 3.12+)
┌──────────────────────────────┐   HTTP    ┌────────────────────────────────────┐
│ robot_bridge/pepper_bridge.py│◄─────────►│ BridgeClient (httpx)               │
│ Tornado 3.1.1 :8888          │           │ EventStream (websockets)           │
│ REST + /ws/events + tablet   │ WebSocket │ PepperRobot                        │
│ page; NAOqi calls run on     │◄─────────►│ AIManager + ToolExecutor + Speech  │
│ worker threads               │           │ FastAPI :8000 (REST, /ws, web UI)  │
└──────────────────────────────┘           └────────────────────────────────────┘
                                                        ▲ streaming + tool calling
                                                 Anthropic Claude API
```

## Project structure

```
PepperEvolution/
├── robot_bridge/
│   ├── pepper_bridge.py    # Bridge server (runs on robot, Python 2.7 + Tornado 3.1.1)
│   └── deploy.py           # SSH deploy / restart / stop / status / logs
├── src/
│   ├── pepper/             # BridgeClient, FakeBridgeClient, EventStream, PepperRobot
│   ├── ai/                 # tools, Anthropic/OpenAI providers, speech streaming, ToolExecutor, AIManager
│   ├── communication/      # FastAPI app: REST, /ws hub, direct commands
│   ├── sensors/, actuators/# thin convenience wrappers
├── web/index.html          # Browser control panel (served at /)
├── examples/               # basic_chat.py (terminal), event_monitor.py
├── tests/                  # ~200 tests incl. running the real bridge with tests/fakenaoqi
├── docs/                   # GETTING_STARTED.md, BRIDGE_API.md
├── scripts/start.sh        # deploy + start (or --fake)
└── main.py                 # Host application entry point
```

## AI tools

| Tool | Description |
|------|-------------|
| `speak` | Say something now, before a slow action or in another language (normal replies are spoken automatically) |
| `play_animation` | Wave, bow, nod, shake head, think, explain, happy, ... |
| `move_head` | Look in a direction |
| `turn` / `move_forward` | Turn in place, drive short distances (sonar-checked, max 2 m) |
| `set_posture` | Stand, StandInit, StandZero, Crouch |
| `set_eye_color` | Eye LED colour |
| `take_photo` | Camera snapshot, returned to the model as an image |
| `get_sensors` | Battery, touch, bumpers, sonar, people count |
| `show_on_tablet` | Text or a web page on the chest tablet |
| `emergency_stop` | Kill all movement and speech, then rest (motors off) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PEPPER_IP` | `10.0.100.100` | Robot IP |
| `BRIDGE_PORT` | `8888` | Bridge port |
| `BRIDGE_API_KEY` | | Optional bridge auth |
| `PEPPER_FAKE_BRIDGE` | `false` | Simulated robot |
| `PEPPER_AUTONOMOUS_LIFE` | `disabled` | Autonomous Life state set on connect (`keep` to leave it) |
| `AI_MODEL` | `claude-opus-5` | AI model (`claude-*` or `gpt-*`) |
| `AI_EFFORT` | `low` | Claude reasoning effort |
| `ANTHROPIC_API_KEY` | | Required for Claude |
| `OPENAI_API_KEY` | | Required for GPT |
| `SPEAK_RESPONSES` / `TABLET_SUBTITLES` / `REACT_TO_TOUCH` | `true` | Behaviour switches |
| `API_PORT` | `8000` | Host port (REST + WebSocket + UI) |

## Testing

```bash
pytest tests/ -q    # no robot needed; starts the real bridge process with a fake NAOqi
```

## Credits

PepperEvolution is a research project from [TRiPL Lab](https://tripl.ca/), Toronto Metropolitan University.

## License

MIT License. See [LICENSE](LICENSE).
