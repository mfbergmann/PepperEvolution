# Changelog

PepperEvolution is pre-1.0: versions are `0.MINOR.PATCH`, with a new minor version for each milestone-sized step and patch versions for fixes in between. The bridge (`robot_bridge/pepper_bridge.py`) carries the same number as the host (`src/__init__.py`); a test keeps them equal.

**1.0** will mean Pepper can be left running in the lab as a presence: Milestones 4 (world model) and 5 (memory and people) done, and a week of unattended daily use without a safety incident or a restart. See [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
