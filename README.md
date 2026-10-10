# PepperEvolution

A cloud-AI control system for SoftBank Pepper robots. A small bridge server on the robot wraps NAOqi as HTTP/WebSocket endpoints; the host application connects over the network, drives conversations with Anthropic Claude using native tool calling, and speaks the reply through the robot as it streams in.

## What it does

- **Talks like a person in the room** – the model's reply is streamed, split into sentences, and spoken by Pepper (animated speech, with inline gesture tags) while the rest is still being generated. Subtitles appear on the chest tablet.
- **Acts** – Claude calls tools to wave, bow, nod, look around, turn, drive short distances, change eye colour, use the tablet, or stop.
- **Sees** – `take_photo` returns the camera image to the model, so "what do you see?" gets a real answer.
- **Feels** – head/hand touches and bumpers stream from the robot and trigger short reactions.
- **Listens** – optional voice input: hold-to-talk in the browser, or the robot's own microphone streamed from the bridge (muted while Pepper speaks), recognised on the host with sherpa-onnx or faster-whisper.
- **Reacts without thinking** – "stop", "be quiet", "wake up", "look at me" are handled locally in milliseconds, mid-reply; the eyes show listening / thinking / speaking; a short filler covers long model pauses; the head turns to newcomers and follows people through NAOqi's own face tracking.
- **Notices people and remembers** – a world model on the host keeps who is in view (distance, direction, gaze) and where people were after they leave view (odometry), greets someone who walks up, and tells the model each turn who is around and where. With `MEMORY_DIR`, Pepper remembers people who agree to it, and forgets them when asked ([docs/MEMORY.md](docs/MEMORY.md)).
- **Judges the situation fast (optional, a local GPU machine)** – small local decision models decide in about 0.15 s whether speech was meant for Pepper (side talk gets no reply), which physical action to start at once (Pepper moves about 0.25 s after the transcript while Claude prepares the words), and whether someone in view is waving or showing something. Without the GPU machine everything still works, slower. See [docs/SETUP_PROFILES.md](docs/SETUP_PROFILES.md).
- **Is recorded for review** – with `SESSION_DIR`, every run keeps its log, audio, photos and a per-turn record locally; `scripts/review_session.py` turns it into a timed transcript with flags.
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
python robot_bridge/deploy.py --install-autostart   # once: the bridge starts at every boot
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
│ REST + /ws/events + /ws/audio│ WebSocket │ AudioStream → VoiceInput (STT)     │
│ + /ws/camera + tablet page;  │◄─────────►│ PepperRobot, WorldModel            │
│ NAOqi calls run on worker    │           │ AIManager + intents + ToolExecutor │
│ threads                      │           │ FastAPI :8000 (REST, /ws, web UI)  │
└──────────────────────────────┘           └────────────────────────────────────┘
                                             ▲ streaming + tool calling   ▲ /v1/systemone (optional)
                                       Anthropic Claude API      GPU machine: Ollama decision models
```

The design it is building towards (reflexes, continuous perception, a shared world model, one mind that speaks) is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project structure

```
PepperEvolution/
├── robot_bridge/
│   ├── pepper_bridge.py    # Bridge server (runs on robot, Python 2.7 + Tornado 3.1.1)
│   └── deploy.py           # SSH deploy / restart / stop / status / logs
├── src/
│   ├── pepper/             # BridgeClient, FakeBridgeClient, EventStream, AudioStream, PepperRobot
│   ├── ai/                 # tools, Anthropic/OpenAI providers, speech streaming, intents, ToolExecutor, AIManager
│   ├── audio/              # voice input: PCM helpers, endpointer, sherpa/whisper transcribers, VoiceInput
│   ├── world/              # working memory: observations, WorldModel (who is around), timeline, views
│   ├── memory/             # long-term memory: SQLite store (people with consent, facts, episodes), memory tools
│   ├── decide/             # local decision models: client (fails open), addressee gate, command router
│   ├── perception/         # camera stream judged about once a second
│   ├── session.py          # session records (SESSION_DIR)
│   ├── communication/      # FastAPI app: REST, /ws hub, direct commands
│   ├── sensors/, actuators/# thin convenience wrappers
├── web/index.html          # Browser control panel (served at /)
├── examples/               # basic_chat.py (terminal), event_monitor.py, mic_monitor.py
├── tests/                  # ~590 tests incl. running the real bridge with tests/fakenaoqi
├── docs/                   # HANDOFF, ARCHITECTURE, MEMORY, ROADMAP, SETUP_PROFILES, GETTING_STARTED, BRIDGE_API, SAFETY, research notes, signs/
├── scripts/                # start.sh, review_session.py, smoke_host.py, compare_models.py, virtual_pepper.sh, ...
└── main.py                 # Host application entry point
```

## AI tools

| Tool | Description |
|------|-------------|
| `speak` | Say something now, before a slow action or in another language (normal replies are spoken automatically) |
| `play_animation` | Wave, bow, nod, shake head, think, explain, happy, ... |
| `offer_hand` | Hold the right hand out for a handshake (up to 15 s), shake it when taken, then lower it |
| `move_head` | Look in a direction |
| `look_at` | Turn the head to a spot in the last photo |
| `point_at` | Aim the arm at a spot in the last photo, the person in view, or a direction (holds 3 s) |
| `turn` / `move_forward` | Turn in place, drive short distances (sonar-checked, max 2 m) |
| `set_posture` | Stand, StandInit, StandZero, Crouch |
| `set_eye_color` | Eye LED colour |
| `take_photo` | Camera snapshot, returned to the model as an image |
| `get_sensors` | Battery, touch, bumpers, sonar, people count |
| `show_on_tablet` | Text or a web page on the chest tablet |
| `emergency_stop` | Kill all movement and speech, then rest (motors off) |
| `remember_person`, `remember`, `recall`, `forget_person` | With `MEMORY_DIR`: remember someone who agreed (consent is checked against what Pepper heard), keep facts, look things up, forget someone at once |

## Configuration

Running it yourself: [docs/SETUP_PROFILES.md](docs/SETUP_PROFILES.md) explains the cloud-only setup (an Anthropic key and a laptop) and the lab setup (plus a GPU machine for fast local judgements), and what each piece adds.


| Variable | Default | Description |
|----------|---------|-------------|
| `PEPPER_IP` | `10.0.100.100` | Robot IP |
| `BRIDGE_PORT` | `8888` | Bridge port |
| `BRIDGE_API_KEY` | | Optional bridge auth |
| `PEPPER_FAKE_BRIDGE` | `false` | Simulated robot |
| `PEPPER_AUTONOMOUS_LIFE` | `disabled` | Autonomous Life state set on connect (`keep` to leave it) |
| `AI_MODEL` | `claude-sonnet-5-5` | AI model (`claude-*`, `gpt-*`, or a local model with `OLLAMA_URL`) |
| `AI_EFFORT` | `medium` | Claude reasoning effort |
| `ANTHROPIC_API_KEY` | | Required for Claude |
| `OPENAI_API_KEY` | | Required for GPT |
| `SPEAK_RESPONSES` / `TABLET_SUBTITLES` / `REACT_TO_TOUCH` | `true` | Behaviour switches |
| `GREET_NEWCOMERS` / `GREET_COOLDOWN` | `true` / `90` | Greet someone who walks up (within 3 m looking at Pepper, or 1.8 m) after nobody was in view for 20 s; seconds between greetings |
| `LED_STATE_SIGNALS` / `BACKCHANNEL_AFTER` | `true` / `2.0` | Eye colour state signals; seconds before a spoken filler |
| `DECIDE_URL` | | Fast local judgements on a GPU machine (Ollama with `nimble`, `clef-flash`): who is Pepper being spoken to, which action to start at once, what the camera shows. Empty = off; see [docs/SETUP_PROFILES.md](docs/SETUP_PROFILES.md) |
| `ADDRESSEE_GATE` / `ROUTER` / `VISION_STREAM` | `true` | With `DECIDE_URL`: answer only speech meant for Pepper; start simple actions at once; judge camera frames while someone is in view (`ADDRESSEE_THRESHOLD`, `ROUTER_THRESHOLD`, `VISION_FPS` tune them) |
| `SESSION_DIR` | | One folder per run with everything needed to review a session (`scripts/review_session.py`) |
| `SOUND_DIRECTION` | `false` | With bridge 0.6: each spoken turn gets the direction the voice came from (NAOqi sound localisation), for "come to me" out of the camera's view |
| `MEMORY_DIR` | | Long-term memory: people who agreed to be remembered, facts, session episodes ([docs/MEMORY.md](docs/MEMORY.md); `scripts/memory_admin.py`); empty = off |
| `SCENE_NOTES` / `SCENE_MODEL` | `false` / `qwen3.5:4b` | With `DECIDE_URL`: every 20 s while someone is in view, who is where and a short note on the place |
| `WAIT_FOR_UNFINISHED` | `true` | Voice: when an utterance clearly stops mid-sentence ("How do you feel about"), wait up to 2.5 s for the rest and answer the whole sentence |
| `INITIATIVE` | `false` | Pepper may speak unprompted: to someone lingering quietly nearby, or once more after an unanswered question (at most every 3 minutes) |
| `PHOTO_RECORD_DIR` / `VOICE_RECORD_DIR` | | Keep photos (with head angle and sharpness) / utterance audio for testing; inside `SESSION_DIR` when that is set |
| `PEPPER_AWARENESS` | `false` | `true`: head-only face tracking while someone is in view (off for an empty room, so the head can look out for the next person); `keep` leaves it alone; head moves pause it for 8 s |
| `STT_BACKEND` / `STT_MODEL` | `none` | Voice input: `sherpa` + model directory, or `whisper` + `base`/`small` (see `requirements-voice.txt`) |
| `VOICE_INPUT` | `false` | Also stream the robot's microphone (hold-to-talk in the UI works without it) |
| `API_PORT` | `8000` | Host port (REST + WebSocket + UI) |

## Testing

```bash
pytest tests/ -q    # no robot needed; starts the real bridge process with a fake NAOqi
scripts/virtual_pepper.sh start && scripts/virtual_pepper.sh bridge   # optional: NAOqi's own desktop build as a headless Pepper
PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:8899 pytest tests/test_virtual_naoqi.py -v
python scripts/smoke_host.py --fake            # the whole host stack with the real model, no robot
```

Version 0.7.0; see [CHANGELOG.md](CHANGELOG.md). Milestones and progress are also tracked in the [wiki](https://github.com/mfbergmann/PepperEvolution/wiki) and under [GitHub milestones](https://github.com/mfbergmann/PepperEvolution/milestones).

Picking the work up in a new session: start with [docs/HANDOFF.md](docs/HANDOFF.md). The design the project is building towards is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Contributing

Bug reports, test reports from other Peppers, and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report and what a change must keep (the Python 2.7 bridge, the safety ledger, people's data); AI coding assistants should start with [AGENTS.md](AGENTS.md).

## Credits

PepperEvolution is a research project from [TRiPL Lab](https://tripl.ca/), Toronto Metropolitan University.

## License

MIT License. See [LICENSE](LICENSE).
