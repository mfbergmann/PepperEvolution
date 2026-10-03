# Changelog

PepperEvolution is pre-1.0: versions are `0.MINOR.PATCH`, with a new minor version for each milestone-sized step and patch versions for fixes in between. The bridge (`robot_bridge/pepper_bridge.py`) carries the same number as the host (`src/__init__.py`); a test keeps them equal.

**1.0** will mean Pepper can be left running in the lab as a presence: Milestones 4 (world model) and 5 (memory and people) done, and a week of unattended daily use without a safety incident or a restart. See [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 0.5.0 (2026-10-03): system one in the loop

Built and tested offline (unit tests, NAOqi's desktop build, replays of real sessions against the local models); the robot test is on 2026-10-06 (`docs/HANDOFF.md`, issue #22).

- **Situation judgements** (`src/decide/`, `DECIDE_URL`): a client for local decision models (Ollama `/v1/systemone`) that fails open after 0.8 s and keeps the models warm.
- **Addressee gate**: open-microphone speech is answered only when it seems meant for Pepper (Nimble; p ≥ 0.6, or 0.4 while someone looks at Pepper); side talk gets no reply, the head turns to the speaker. Replaying the 2026-10-01 session with two visitors through the production code: 21 of 22 right, 53 ms.
- **Command router**: head moves, gestures, turns in place, stop, be quiet and look at me start about 0.15 s after the transcript instead of 3-4 s; Claude still does the words, and its repeat of the same action is absorbed. Replay: 19 of 20 startable commands, no wrong actions, none on questions, 123 ms; with real Sonnet on the fake robot, one 90° turn for "turn left ninety degrees" and one 30° turn for "a little bit farther".
- **Camera judgements**: the bridge streams small frames over `/ws/camera`; while someone is in view the host sends about one a second to Clef Flash (waving? showing something? facing?), into the world model and the "Around you" line, with events that let Pepper react to a wave. About 145 ms per frame end to end on the desktop build.
- **Session records** (`SESSION_DIR`): one folder per run with the log, audio, photos, `turns.jsonl` and `events.jsonl`; `scripts/review_session.py` prints a timed transcript with flags.
- **Setup guide** (`docs/SETUP_PROFILES.md`): running with cloud models only (Claude plus a laptop) or with a local GPU machine for the judgements, and what each piece adds.

- **Situation judgements tested** (not yet wired in): local decision models (Ollama's Nimble and Clef Flash on the Creative AI Hub) told addressed from side talk in 21 of 22 real utterances in under 0.1 s, and answered four questions about a camera frame in 0.25 s. See `docs/ARCHITECTURE.md`.

- **Look back after looking away**: after a turn that moved the head ("look left, what's there?"), Pepper faces the person again: face tracking resumes at once instead of after 8 s, or the head turns to the nearest person, or, with nobody in view, looks out at the room. Reported from a second Pepper.
- **Photos show where Pepper looked**: on the robot, face tracking locked on a person pulled the head back from 70° to the face within a second, before the photo was taken, even while paused; the bridge now switches tracking off for deliberate head moves (8 s) and photos (1 s). The bridge reports the measured head angles, the model is told when its head did not arrive, and the state block says how old the last photo is and whether the head has moved since (an old photo had been described as the current view). Verified on the robot: the photo after "look left" measured 60° and showed the office.
- **Coming to people and driving on request**: the "Around you" line gives each person's direction in the turn tool's terms ("about 20° to your left"), and the model is told how to come to someone (turn, then drive their distance minus 0.6 m). It no longer checks the sensors before every drive (the bridge's sonar guard and NAOqi's collision avoidance already stop it), saving a model round trip per move.
- Session logs keep full tool results and every sentence Pepper says ("Saying: ..."), for reviewing sessions.
- **Photo log** for test sessions: `PHOTO_RECORD_DIR` keeps every photo with its measured head angle and sharpness (local, git-ignored).
- Contributor guide rewritten (`CONTRIBUTING.md`), `AGENTS.md` for AI coding assistants, issue templates (bug report, robot test report, feature) and a pull request template.

## 0.4.0 (2026-09-29): greeting newcomers, head reflexes, sharp photos, autostart

- **Sharp photos**: `/move/head` waits until the head has arrived and stopped (about 1.3 s for a 60° turn); `/picture` pauses face tracking for the shot, waits for a still head and drops the first frame; the host scores each photo's sharpness, retakes a blurry one once and tells the model if it is still blurry. Built offline; measurement on the robot next session.
- **Neutral pose** after gestures and animations: arms and legs return to StandInit (`/posture/neutral`), the head is left to tracking.
- **Bridge autostart**: `deploy.py --install-autostart` installs a NAOqi package whose `autorun` service starts the bridge at every boot, through the same `launch.sh` that `deploy.py` uses. Tested on NAOqi's desktop build, including a cold start.
- Face tracking at start-up no longer follows sounds (`People` and `Touch` only).

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
