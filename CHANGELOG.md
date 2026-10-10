# Changelog

PepperEvolution is pre-1.0: versions are `0.MINOR.PATCH`, with a new minor version for each milestone-sized step and patch versions for fixes in between. The bridge (`robot_bridge/pepper_bridge.py`) carries the same number as the host (`src/__init__.py`); a test keeps them equal.

**1.0** will mean Pepper can be left running in the lab as a presence: Milestones 4 (world model) and 5 (memory and people) done, and a week of unattended daily use without a safety incident or a restart. See [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Unreleased

- The lab's Basecamp (TRiPL project, Pepper section) is kept in step with the repository by `scripts/basecamp_sync.py` (2026-10-09). It mirrors the process documentation into Pepper / Pepper Evolution, and keeps a PepperEvolution to-do list as the running log: milestones with their issues, robot sessions and releases.

## 0.6.0 (2026-10-08): memory

A place where remembering goes, built offline after the 2026-10-08 robot session (design: `docs/MEMORY.md`, tracking issue #29). Working memory and long-term memory share one vocabulary (`Observation`, `Event`) and one way in (`WorldModel.observe()`); readers are pure views; nothing on the read path calls a model; only the mind speaks. The robot test comes next session (`docs/HANDOFF.md`, "Before the next robot session").

- **Long-term memory** (`src/memory/`, `MEMORY_DIR`, off by default):
  - A SQLite store of people who agreed to be remembered, facts about them and the lab, and a short record of each session.
  - The mind's new tools: `remember_person`, `remember`, `recall` and `forget_person`.
  - Consent is checked in code against what Pepper heard in the last two minutes: a yes to its question, or a request to be remembered, and never side talk. The words are kept as the consent note.
  - "Forget me" deletes at once. People and their facts expire after 90 days unless renewed.
  - `scripts/memory_admin.py` lists, shows and forgets.
  - Tested end to end with the real model on the virtual Pepper: remember, recall, forget, a refusal, a lab fact.
- **Remembering where people were** (#27):
  - Pepper's pose in NAOqi's odometry frame comes from the bridge, or is dead-reckoned from its own moves.
  - The host keeps its own person tracks.
  - The "Around you" line says where someone out of view was last seen ("about 90° to your right ... (turn -90 to face where they were)", "they may have moved since" after 30 s).
  - Replaying the 12 sessions of 2026-10-08: when someone came back into view, memory had predicted their direction within a median of 7° (28 of 36 within 20°; the 3 misses were people who had moved). For the "turn towards me" that took four turns on the day, it would have said "turn -90".
- **Bridge (0.6.0):**
  - The pose in sensors snapshots, people events and move results, plus a settled `pose` event.
  - Opt-in sound localisation: `POST /sound/localization` and `sound` events with a body-frame direction. Pepper's own speech is dropped.
- **Where a voice came from** (#12, #24, `SOUND_DIRECTION`, off by default): each spoken turn gets "The voice you are answering came from about 50° to your left (turn 50 to face it)". The speaker is attached to the person in that direction, or remembered as someone heard, for "come to me".
- **Scene notes** (#11, `SCENE_NOTES`, off by default until tried on the robot):
  - Two models, each doing what it is good at. Clef Flash says who is where; a benchmark on 12 hand-labelled frames got 11 of 12 people counts right in 0.14 s. A generative model counted reflections as people.
  - `qwen3.5:4b` describes only the place and the objects.
  - A look about every 20 s while someone is in view, about 0.5 s each.
- **Working memory's timeline:**
  - It holds heard and said utterances, arrivals, greetings, camera events and moves, for the `recall` tool.
  - Every gated turn records Pepper's last sentence, so the addressee judgement can be re-benchmarked with it (#25).
- `scripts/replay_world.py` rebuilds working memory from a session folder and compares the "Around you" lines.
- The model cannot point and now says so (#28). Base moves from the web UI go through the robot, so memory knows about them.
- The default model is Sonnet 5.5 at medium effort, the choice made on the robot.
- The recording notice adds "I only remember you from one day to the next if you say yes when I ask. Say 'forget me' any time and I will."

## 0.5.1 (2026-10-08): first robot session with system one

Fixes from the robot session on 2026-10-08 (`docs/HANDOFF.md`, "Robot session 2026-10-08"); each was reviewed from the session records and re-tested on the robot the same day unless noted.

- **Handshake that holds**: NAOqi's Give_3 gesture dropped the arm at once. The bridge's `/pose/offer_hand` holds the right hand out for up to 15 s, notices it being taken (the shoulder current drops when a hand takes the arm's weight, or the hand is shaken, or the touch sensor), shakes gently and lowers the arms; Pepper speaks without body language while the hand is out. The router's "shake hands" and a new `offer_hand` tool use it; the result is recorded. A gentle grip is not detectable from the arm (#23).
- **Command router on the robot**: actions start about 0.25 s after the transcript (32 on the day). A routed "look at me" looks at once, in the background, and passes the whole request on (it used to end the turn, and later made the words wait 1.5 s for face tracking to switch on). Routed head moves are followed by the look-back. A failed routed action is no longer reported as done.
- **Addressee gate**: in a conversation (Pepper answered within 20 s) the bar is 0.4. Right after Pepper asked a question it is 0.2. With at most one person seen for a minute, anything said within a minute of Pepper's last reply is answered, because alone with Pepper the gate had dropped 12 of 38 things said to it. Re-tested: 10 of 11 answered, and the one miss led to the one-minute window.
- **Turning to face people**: the "Around you" direction says it is measured from where the body points now and gives the turn ("turn -75 to face them"), since the model had added an earlier turn on top. Photos carry the measured head direction, since face tracking moves the head without commands. The take_photo result says where the head pointed relative to the body. Re-tested: a person at 67°, Pepper turned 70°.
- **People reports follow sideways moves** (direction and distance steps in the bridge's change detection); "come to me" had driven straight at a position 18 s old.
- **Camera events**: a threshold per kind; holding something up counts from 0.6 (it scored 0.68-0.77 and was missed at 0.7).
- **Decision models down**: the client says so once, answers at once for 30 s and retries, instead of a warning per camera frame and a 0.8 s wait per turn when the host is unreachable. A minute with the models stopped: every turn answered, no errors.
- **Review script**: shows handshakes, whether the speaker was alone with Pepper, and how much of a slow first word was waiting for the previous turn.
- Measured: the camera stream at 1 fps costs the robot 5-22 % of a core and about 45-50 KB/s.
- A printable recording notice for people near Pepper (`docs/signs/`).

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
