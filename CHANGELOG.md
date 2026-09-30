# Changelog

PepperEvolution is pre-1.0: versions are `0.MINOR.PATCH`, with a new minor version for each milestone-sized step and patch versions for fixes in between. The bridge (`robot_bridge/pepper_bridge.py`) carries the same number as the host (`src/__init__.py`); a test keeps them equal.

**1.0** will mean Pepper can be left running in the lab as a presence: Milestones 4 (world model) and 5 (memory and people) done, and a week of unattended daily use without a safety incident or a restart. See [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Unreleased

- **Greeting newcomers** (Milestone 4): the world model raises an arrival when someone appears after nobody was in view for 20 s, and Pepper greets them in one short line if they are close and looking at it, with a 90 s cooldown and no greeting during a conversation (`GREET_NEWCOMERS`, `GREET_COOLDOWN`). Tested on the robot in the open room: picked up at 2.9 m, head turns at once, speech about 2 s later.
- **Head reflexes**: Pepper turns its head to a newcomer as soon as it sees them, and looks back out at the room (straight ahead, 18° up) 3 s after the last person leaves. With `PEPPER_AWARENESS=true`, NAOqi face tracking runs while someone is in view and is switched off for an empty room, where it used to park the head looking at the floor. The bridge reports the head direction to face each person.
- **Local models through Ollama** (`OLLAMA_URL`): a streaming provider with photos, `scripts/bench_llm.py` for raw latency, and local models in `scripts/compare_models.py`. Qwen3-VL on an RTX 5090 is about twice as fast as Claude to first words but not yet reliable about looking before describing.
- Sonnet 5.5 chosen for spoken turns (typed and blind spoken rounds on the robot).
- `deploy.py` waits for the old bridge to exit before starting the new one.

## 0.3.0 (2026-09-23): first sessions on the robot, world model begins

- **First contact with the physical Pepper** (Milestone 0 complete): every bridge endpoint, base moves with odometry, touch, bumpers, people, tablet, head tracking, camera, microphone and emergency stop verified on Pepper 1.8A with NAOqi 2.5.10.7.
- Five fixes that only the hardware showed: the deploy script sets NAOqi's Python path; the emergency stop really rests the robot (a `rest()` straight after `killAll()` is ignored); looping animations are stopped after 20 s; "stop" silences animated speech in about a second instead of eight; microphone frames are 85 ms.
- Tested against NAOqi's own desktop build first (`scripts/virtual_pepper.sh`, `tests/test_virtual_naoqi.py`), which found three more bridge bugs.
- Voice latency: the model speaks first and gestures inline; fillers cover silent tool calls and voice turns; the model knows the full date.
- Model comparison on the robot (`scripts/compare_models.py`): Sonnet 5 at medium effort chosen for spoken turns.
- Speech-to-text comparison on recordings from a noisy open room (`scripts/compare_stt.py`): NVIDIA Nemotron streaming chosen (6 % word errors against 37 % for the previous model); streamed utterances are saved with transcripts for tuning.
- **World model, first slice** (Milestone 4): debounced people events with distance, gaze and zone; `src/world/model.py`; an "Around you" line in the model's state on every turn.
- Docs: `docs/ARCHITECTURE.md` (the target design), `docs/SAFETY.md`, `docs/HANDOFF.md`.

## 0.2.0 (2026-09-10): reactive layer and voice input

First labelled v2.2. Local intents ("stop", "be quiet", "look at me") answered without a model; eye-colour state signals; spoken fillers; microphone streaming from the bridge muted while Pepper speaks; speech-to-text on the host (sherpa-onnx, faster-whisper); hold-to-talk in the web UI.

## 0.1.0 (2026-09-08): rewrite

First labelled v2.1. A new bridge on the robot (every NAOqi call off the event loop, safety bounds, emergency stop) and a new host (FastAPI, Claude with streaming tool calls, replies spoken sentence by sentence, photos as image tool results), replacing an earlier prototype that had never run on the robot.

## Before 0.1

A 2025 prototype, kept on the `Bridge-mode` branch.
