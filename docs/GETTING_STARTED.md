# Getting Started with PepperEvolution v2.1

## Prerequisites

- SoftBank Pepper robot with NAOqi 2.5 on the network (default `10.0.100.100`, ssh `nao`/`nao`)
- Host computer with Python 3.12+ (no NAOqi SDK needed)
- Anthropic API key (or OpenAI API key)

## Installation

```bash
git clone https://github.com/mfbergmann/PepperEvolution.git
cd PepperEvolution
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env      # then edit: PEPPER_IP, ANTHROPIC_API_KEY
```

## Try it without the robot first

```bash
./scripts/start.sh --fake          # or: PEPPER_FAKE_BRIDGE=true python main.py
```

Open http://localhost:8000. Everything works (chat, streaming speech, photos, events) except that the robot actions are only logged. This is the quickest way to check the API key and the model, and to get a feel for the tools. `python examples/basic_chat.py` gives the same in a terminal.

## First test with the real robot

1. **Turn Pepper on** and wait for it to boot. Check reachability: `ping 10.0.100.100`.
2. **Deploy the bridge** (uploads `robot_bridge/pepper_bridge.py`, starts it, polls `/health`):
   ```bash
   python robot_bridge/deploy.py
   ```
   Expected last lines: `Bridge is healthy: robot=<name> naoqi=2.5.x version=2.1.0`. Right after a robot boot
   this can take up to about two minutes while NAOqi finishes starting (deploy prints `waiting for NAOqi` meanwhile).
   If it fails, `python robot_bridge/deploy.py --logs` shows `bridge.log` from the robot.
3. **Sanity-check the bridge directly** (no AI involved):
   ```bash
   curl http://10.0.100.100:8888/status
   curl http://10.0.100.100:8888/sensors
   curl -X POST http://10.0.100.100:8888/prepare -H 'Content-Type: application/json' -d '{"autonomous_life":"disabled","wake_up":true}'
   curl -X POST http://10.0.100.100:8888/speak -H 'Content-Type: application/json' -d '{"text":"Hello, I am Pepper"}'
   curl -X POST http://10.0.100.100:8888/animation -H 'Content-Type: application/json' -d '{"name":"animations/Stand/Gestures/Hey_1"}'
   curl -X POST http://10.0.100.100:8888/leds/eyes -H 'Content-Type: application/json' -d '{"color":"blue"}'
   ```
   `python examples/event_monitor.py` prints touch/bumper/sonar/battery events; tap Pepper's head to see one.
4. **Start the host application:** `python main.py` and open http://localhost:8000.
   On connect it disables Autonomous Life and wakes the robot (`PREPARE_ON_CONNECT`); set `PEPPER_AUTONOMOUS_LIFE=keep` if you'd rather leave Pepper's own behaviours running.
5. **Talk.** Good first prompts: *"Wave at me and say hello"*, *"What do you see?"*, *"Look to your left"*, *"Turn your eyes green and bow"*. Tap the head to trigger a reaction.
6. **Stop.** Ctrl-C in the terminal shuts the host down; `REST_ON_EXIT=true` also puts the robot to rest. The EMERGENCY STOP button (also the Escape key, or `POST /command/emergency_stop`) stops all animations, speech and motion and puts the robot to rest (motors off). The robot then refuses every move until you press *Wake up* or *Prepare*.

## What the AI can do

| Tool | Effect |
|------|--------|
| `speak` | Say something now (before a slow action, or in another language). Normal replies are spoken automatically. |
| `play_animation` | Wave, bow, nod, shake head, think, explain, happy, ... |
| `move_head` | Look left/right/up/down |
| `turn`, `move_forward` | Turn in place, drive short distances (sonar-checked, clamped to 2 m) |
| `set_posture` | Stand / Crouch |
| `set_eye_color` | Eye LEDs |
| `take_photo` | Take a picture and *see* it (the image goes back to the model) |
| `get_sensors` | Battery, touch, bumpers, sonar, people count |
| `show_on_tablet` | Text or a web page on the chest tablet |
| `emergency_stop` | Stop everything |

The reply text is spoken sentence by sentence as it streams from the model, with subtitles on the tablet. Gesture tags like `^start(animations/Stand/Gestures/Hey_1)` inside the reply make Pepper gesture while talking.

## Architecture

```
You (web UI / curl / terminal)
    │
    ▼
Host application (Python 3.12+)  —  main.py
    ├── FastAPI on :8000  (REST API, /ws live events, web UI)
    ├── AIManager  (Claude with tool calling; streams reply -> SpeechStreamer -> /speak)
    └── PepperRobot / BridgeClient (httpx) + EventStream (websockets)
            │
            ▼
Bridge server on the robot (Python 2.7, Tornado 3.1.1, :8888)  —  robot_bridge/pepper_bridge.py
            │
            ▼
NAOqi 2.5 services (ALMotion, ALTextToSpeech, ALAnimatedSpeech, ALVideoDevice, ALMemory, ALLeds, ALTabletService, ...)
```

## Running tests

```bash
pytest tests/ -q          # ~200 tests, no robot; includes starting the real bridge with a fake NAOqi
```

## Troubleshooting

**`deploy.py` cannot connect** — check `PEPPER_IP`, that the robot is on the same network, and that ssh `nao@10.0.100.100` (password `nao`) works.

**Bridge starts but `/health` never answers** — NAOqi may still be booting; the bridge retries for ~2 minutes. `python robot_bridge/deploy.py --logs` shows why.

**Speech works but the robot does not move** — motors are off (`robot is resting` / `robot is halted` in the error). `POST /wake_up`, or the *Wake up* / *Prepare* buttons in the UI; the bridge never wakes the robot implicitly. If Autonomous Life is `solitary`/`interactive`, its own behaviours fight external control (the head drifts back after `move_head`); the default `PEPPER_AUTONOMOUS_LIFE=disabled` avoids that.

**"not moving: front sonar shows an obstacle"** — something is within 0.45 m in the direction of travel. Move it, drive the other way, or pass `force: true` to `/move/forward` for a direct command.

**Prepare reports problems** (`prepare: partly done - autonomous_life: ...`) — the most common cause is NAOqi refusing `setState` because the robot's first-boot setup wizard was never completed on the tablet; the other steps (wake up, posture) still run.

**Photos are slow** — use QVGA: `POST /photo?resolution=1`, or set `robot.photo_resolution = 1` (resolution 0=QQVGA 1=QVGA 2=VGA 3=4VGA). If the robot has no PIL the bridge sends raw RGB and the host converts it to JPEG.

**Language errors** — `/speak` with `language` only works for languages installed on the robot (`ALTextToSpeech.getAvailableLanguages`); the error message lists them.

**"Phantom tool call" warning in the log** — the model wrote a tool call as text; the host retries automatically. If it happens often, try `AI_EFFORT=medium`.

**Tablet subtitles off** — the first tablet error disables them for the session (see the log); check that ALTabletService works and that the tablet can reach `http://198.18.0.1:8888/tablet/page`.

## Safety

- Keep the emergency stop within reach (UI button, `POST /command/emergency_stop`, or `curl -X POST http://10.0.100.100:8888/emergency_stop`).
- Movement is clamped (2 m, 180°, 0.5 m/s) and refused by the bridge itself when the sonar sees something closer than 0.45 m in that direction (for every caller: AI tools, UI, curl); NAOqi's own collision avoidance stays on and `completed: false` tells the AI when a move was cut short.
- Motion never wakes the robot implicitly, and after an emergency stop everything is refused until an explicit wake-up.
- Test moves in an open area first, and watch the battery in the status panel.
