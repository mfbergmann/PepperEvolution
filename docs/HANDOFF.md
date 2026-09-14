# Handoff: where the work stopped and how to pick it up

Written 2026-09-13 at the end of a long session. Read this first in a new session; it says what exists, what has been verified where, and exactly how to resume testing. `ROADMAP.md` has the plan, `SAFETY.md` the bounds, `BRIDGE_API.md` the endpoints.

## State of the code

- `main` at commit `070bd5d` (pushed, CI green on Python 3.12 and 3.13). The two big commits are `0e9ff49` (v2.2: reactive layer + voice input) and `070bd5d` (bridge tested against NAOqi's desktop build, three fixes).
- Host: FastAPI app on one port (REST, `/ws`, web UI with hold-to-talk), Claude Opus 5 with streaming tool calls, replies spoken sentence by sentence, local intents ("stop" cancels the model call and remaining tools in milliseconds), eye-LED state signals, backchannel fillers, optional voice input (`src/audio/`, sherpa-onnx or faster-whisper).
- Bridge (`robot_bridge/pepper_bridge.py`, Python 2.7 + Tornado 3.1.1, runs on the robot): every NAOqi call on a worker thread, `/ws/events`, `/ws/audio` microphone stream muted while the robot speaks, awareness options, sonar guard, emergency stop that rests the robot and blocks motion until wake-up.
- 415 tests (`pytest tests/`), plus the bridge suite under the robot's interpreter, plus opt-in tests against a real NAOqi.

## What has been verified, and where

| Layer | Fake NAOqi (`tests/fakenaoqi`) | Virtual Pepper (NAOqi 2.5.10 desktop build) | Physical robot |
|-------|-------------------------------|---------------------------------------------|----------------|
| Bridge HTTP/WebSocket behaviour, threading, auth, shutdown | yes (also under Python 2.7.18 + Tornado 3.1.1) | yes | **no** |
| NAOqi method names and argument shapes (motion, posture, speech, animated speech, awareness, autonomous life, LEDs, memory, video, qi service registration) | n/a (fake answers everything) | yes, all 12 opt-in tests pass, every endpoint swept | **no** |
| Microphone stream (`/ws/audio`) | yes (fake pumps frames) | no (`ALAudioDevice` absent on the desktop build; the error path is clean) | **no** |
| Sonar / touch / bumpers / battery values, camera content, tablet, installed animations | fake values only | no hardware layer (values are 0, no packages) | **no** |
| Host stack with the real model (tool calls, streaming speech, "stop" mid-turn, LED sequence, fillers) | yes (`scripts/smoke_host.py --fake`) | yes (`scripts/smoke_host.py --bridge http://127.0.0.1:8899`) | **no** |
| Voice input with a real recogniser (sherpa-onnx / faster-whisper) | fake transcriber only | no | **no** |

**Nothing has run on the physical robot yet.** Milestone 0 in `ROADMAP.md` is the 15-step first-contact checklist.

## Things learned that are not obvious from the docs

- NAOqi 2.5 docs are only reachable over plain `http://doc.aldebaran.com/2-5/`.
- Pepper, not NAO: sonar keys are `Device/SubDeviceList/Platform/{Front,Back}/Sonar/Sensor/Value`; HeadPitch envelope is yaw-coupled (max 25.5° down straight ahead); no manual body stiffness (use `rest()`); `ALSonar` and `ALPeoplePerception` only publish while subscribed.
- Microphone audio reaches a client only through a qi service the client registers (`session.registerService(name, obj)` with a `processRemote` method, then `ALAudioDevice.setClientPreferences(name, 16000, 3, 0)` and `subscribe(name)`); the callback runs on a libqi thread. Pepper has no echo cancellation; the bridge mutes capture around speech using `ALTextToSpeech/Status` events (`[taskId, 'enqueued'|'started'|'done'|...]`, confirmed on real NAOqi).
- On the desktop build: `wakeUp()` takes about 15 s; putting Autonomous Life to `disabled` from `solitary` rests the robot (so `/prepare` sets life first, then wakes); the `Sound` awareness stimulus does not exist there (the bridge now reports unavailable stimuli instead of failing); `ALBasicAwareness` does not pause itself for raw head moves (the bridge pauses it around `/move/head` and resumes 8 s later); a missing animation package yields "Wrong path format ... package/path"; `ALSystem`, `ALAudioDevice`, `ALAudioRecorder`, `ALTabletService` are absent.
- A raw sonar reading of exactly 0.0 means "no measurement" and no longer blocks moves.
- Claude Opus 5 at `effort=low` occasionally writes a tool call as XML text; the manager suppresses and retries once.
- The reactive layer and voice design borrow from Autonomous OS (intent table, state signals, safety ledger); see `RESEARCH_2026-09.md`.

## How to resume testing

### Environment

```bash
cd ~/Projects/PepperEvolution
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # the last session used a temporary venv; make your own
pytest tests/ -q                                                                        # ~415 tests, ~25 s
PEPPER_BRIDGE_PYTHON=~/.local/share/mise/installs/python/2.7.18/bin/python pytest tests/test_bridge_integration.py -q   # robot's interpreter
```

`.env` holds the Anthropic key and `AI_MODEL=claude-opus-5`; never commit it. Lint with `black`, `flake8`, `mypy` (120 columns). Commit messages end with the `Co-Authored-By` and `Claude-Session` trailers used in `git log`.

### Virtual Pepper (real NAOqi, no robot)

Already installed on this machine under `~/.local/opt/pepper-sim/` (Choregraphe 2.5.10.7 suite with `bin/naoqi-bin`, model set to Pepper; `pynaoqi-2.5.7.1`; the mise Python 2.7.18 is a shared UCS4 build and loads `qi`). Setup notes are in the header of `scripts/virtual_pepper.sh`. Both processes may still be running from the last session.

```bash
scripts/virtual_pepper.sh status
scripts/virtual_pepper.sh start && scripts/virtual_pepper.sh bridge       # NAOqi on :9559, bridge on :8899, logs in ~/.local/opt/pepper-sim/run/
PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:8899 pytest tests/test_virtual_naoqi.py -v -s
python scripts/smoke_host.py --bridge http://127.0.0.1:8899                # whole host stack + real model against it
PEPPER_IP=127.0.0.1 BRIDGE_PORT=8899 python main.py                        # then open http://localhost:8000
scripts/virtual_pepper.sh stop
```

`~/.local/opt/pepper-sim/probe.py` and `probe2.py` are throwaway Python 2.7 scripts that talk to NAOqi directly (`PYTHONPATH=<pynaoqi>/lib/python2.7/site-packages LD_LIBRARY_PATH=<pynaoqi>/lib python2.7 probe.py`); handy for checking a NAOqi method before adding it to the bridge.

### First session with the physical robot

Follow Milestone 0 in `ROADMAP.md` (15 steps). In short: robot on, `ping 10.0.100.100`, `python robot_bridge/deploy.py` (ssh nao/nao), curl `/status` and `/sensors`, `POST /prepare`, speech, animations (`GET /animations` to learn what is installed), head and base moves with the sonar guard, emergency stop and recovery, `examples/event_monitor.py`, `examples/mic_monitor.py --speak` (confirms the microphone mutes while Pepper talks), then `python main.py` and the first prompts, then voice. `PEPPER_VIRTUAL_BRIDGE=http://10.0.100.100:8888 pytest tests/test_virtual_naoqi.py -v -s` runs the same endpoint checks against the robot (it drives 10 cm forward and back; use a clear floor).

The previous session's standing instruction from the user: once Pepper is on the network, probe and test it directly (ssh, deploy, run the checks) rather than waiting.

### Voice on the day

`pip install -r requirements-voice.txt`, download the sherpa-onnx streaming zipformer named in `GETTING_STARTED.md`, set `STT_BACKEND=sherpa` and `STT_MODEL=<model dir>`; hold-to-talk needs the UI at `http://localhost:8000`; `VOICE_INPUT=true` adds the robot microphone; `VOICE_RECORD_DIR=recordings` saves utterances for tuning.

## Decisions waiting on the live session

- `PEPPER_AWARENESS` default is `false`; try head tracking (roadmap step 12) and decide whether the 8 s resume after a head move feels right, and whether the `Sound` stimulus makes the head twitch during conversation.
- Microphone mute tail (0.4 s) and the energy endpointer thresholds for the whisper path; whether a neural VAD is needed.
- Filler delay (2 s), LED colours in daylight, speech volume, the 0.45 m obstacle threshold, `bodyLanguageMode` for animated speech, which animations are installed.
- Barge-in (a spoken "stop" while Pepper talks) is not possible with the mute as designed; decide after seeing how much Pepper hears of itself.

## Next milestones after M0

M3 vision grounding (`look_at(x, y)` from a point in the last photo, verify-after-act), M4 memory and people identity, M5 operations (bridge autostart, latency budget, session recording). Small reactive-layer leftovers: chest LED for errors, an idle "thinking" gesture, queue policy for speech that arrives while a turn runs.
