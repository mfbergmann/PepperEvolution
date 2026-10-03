# Handoff: where the work stopped and how to pick it up

Written 2026-09-13, updated 2026-09-22 after the first session with the physical robot. Read this first in a new session; it says what exists, what has been verified where, and exactly how to resume testing. `ARCHITECTURE.md` has the target design, `ROADMAP.md` the plan, `SAFETY.md` the bounds, `BRIDGE_API.md` the endpoints.

## State of the code

- `main` is pushed with CI green on Python 3.12 and 3.13. Milestones: `0e9ff49` (0.2.0: reactive layer + voice input), `070bd5d` (bridge tested against NAOqi's desktop build), and the 2026-09-22 commit with the fixes from the first session on the robot. The robot runs that bridge now.
- Host: FastAPI app on one port (REST, `/ws`, web UI with hold-to-talk), Claude Opus 5 with streaming tool calls, replies spoken sentence by sentence, local intents ("stop" cancels the model call and remaining tools in milliseconds), eye-LED state signals, backchannel fillers, optional voice input (`src/audio/`, sherpa-onnx or faster-whisper).
- Bridge (`robot_bridge/pepper_bridge.py`, Python 2.7 + Tornado 3.1.1, runs on the robot): every NAOqi call on a worker thread, `/ws/events`, `/ws/audio` microphone stream muted while the robot speaks, awareness options, sonar guard, emergency stop that rests the robot and blocks motion until wake-up.
- 415 tests (`pytest tests/`), plus the bridge suite under the robot's interpreter, plus opt-in tests against a real NAOqi.

## What has been verified, and where

| Layer | Fake NAOqi (`tests/fakenaoqi`) | Virtual Pepper (NAOqi 2.5.10 desktop build) | Physical robot |
|-------|-------------------------------|---------------------------------------------|----------------|
| Bridge HTTP/WebSocket behaviour, threading, auth, shutdown | yes (also under Python 2.7.18 + Tornado 3.1.1) | yes | yes |
| NAOqi method names and argument shapes (motion, posture, speech, animated speech, awareness, autonomous life, LEDs, memory, video, qi service registration) | n/a (fake answers everything) | yes, all 12 opt-in tests pass, every endpoint swept | yes, 11 of 11 (base moves not run) |
| Microphone stream (`/ws/audio`) | yes (fake pumps frames) | no (`ALAudioDevice` absent on the desktop build; the error path is clean) | yes, 85 ms frames, muted while speaking |
| Sonar / touch / bumpers / battery values, camera content, tablet, installed animations | fake values only | no hardware layer (values are 0, no packages) | sonar, battery, camera, tablet, 396 animations yes; touch and bumpers not yet |
| Host stack with the real model (tool calls, streaming speech, "stop" mid-turn, LED sequence, fillers) | yes (`scripts/smoke_host.py --fake`) | yes (`scripts/smoke_host.py --bridge http://127.0.0.1:8899`) | yes (`--no-move`) |
| Voice input with a real recogniser (sherpa-onnx / faster-whisper) | fake transcriber only | no | **no** |

**First contact with the physical robot happened on 2026-09-22** (see "First session with the robot" below). Everything except base moves, touch events and voice recognition has now been checked on Pepper itself.

## First session with the robot (2026-09-22)

Pepper 1.8A ("Juliette" body), NAOqi 2.5.10.7, Python 2.7.6, Tornado 3.1.1, Pillow 3.1.1, Atom E3845. Voices installed: **English and Chinese only**. 396 animations (Hey_1…10, Bow, Explain, Thinking, ShowSky, Please, No, …; `GET /animations`). A v1 bridge from February 2026 was on the robot; deploy now keeps it as `pepper_bridge.py.previous`.

Passed on the robot: deploy and health, status, sensors (sonar front 0.57 m to the desk, back 0.35 m to the wall, so `obstacle` true), `/prepare` (solitary → disabled did not rest the robot here, unlike the desktop build), plain and animated speech, the language check (French refused with the installed list), eye and chest LEDs, head moves with clamping, Hey_1, camera at QVGA and VGA (good exposure), microphone stream with self-muting (0 frames delivered during speech), tablet text, awareness with all three stimuli (`Sound` exists on the robot) and the pause around head moves, emergency stop mid-speech and wake-up, `tests/test_virtual_naoqi.py` (11 of 11, base moves deselected), and `scripts/smoke_host.py --no-move` with the real model (it looked left, handled the missing French voice, turned its eyes green, waved, and stopped a story on "stop").

Bugs that only the hardware showed, all fixed and redeployed:

- `import qi` fails in a non-login ssh shell; `deploy.py` now sets `PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages`. Without this the bridge dies at import.
- **Emergency stop did not rest the robot**: `ALMotion.rest()` right after `killAll()` returns at once and does nothing. The bridge now calls `stopMove()` in between, retries `rest()` until `robotIsWakeUp()` is false, and reports the real state.
- Some "animations" loop forever (`animations/LED/CircleEyes`, the first entry in the list); `/animation` now runs as a qi future and stops it after 20 s (`completed: false`).
- `ALTextToSpeech.stopAll()` arriving while animated speech is still being prepared stops nothing, so "stop" took about 8 s to land. Speech now runs as qi futures and `/speak/stop` keeps stopping until the sentence has ended; the whole stop path takes about 1.2 s.
- Microphone frames are 85 ms (1365 samples), not the 170 ms the docs implied; docs and the fake now match.

Touch, bumper and people events (guided check, 2026-09-22): all seven passed. Head touches arrive as all three head sensors (front, middle, rear) within 0.4 s, hands and front-right and back bumpers as single press/release pairs, people-count changes within about 1 s. Finding: people detection flickers, alternating between 1 and 2 people (and 0 and 1) several times a second, with a phantom second person at the desk, so `people` events need debouncing before anything reacts to them (nothing does yet; touch and bumper reactions are unaffected).

Spoken touch reactions (2026-09-22): work. Head, left hand, bumper and head again each got a short spoken reply ("Ooh, that tickles!"), usually with a small gesture or eye-colour change chosen by the model; a touch during a running reaction is skipped by design and a quick double touch gives one reply. The eye colour the model picks stays until something changes it.

Voice (2026-09-22): sherpa-onnx 1.13.8 with `sherpa-onnx-streaming-zipformer-en-2023-06-26` in `~/.local/share/pepper-models/` (loads in 2.6 s, about 0.08 s of CPU per second of audio); `.env` now has `STT_BACKEND=sherpa`, `STT_MODEL`, `VOICE_INPUT=true`, `VOICE_RECORD_DIR=recordings`. A guided five-turn spoken conversation through Pepper's own microphone from about a metre: all five understood ("Hullo how are you pepper", "Look to your left", "What can he see in front of you", "Quiet", "How does it feel to have a new brain"), no self-hearing, "quiet" handled locally. Transcripts come in capitals from this model and are now converted to sentence case. **Latency is the open problem: 7 to 12.5 s from end of speech to Pepper's first word**, against a 2 s target. The recogniser is not the cause; the model usually first calls a tool silently (a wave, a head move, a photo, 3 to 4 s per model round plus 4 s for an animation) and only speaks in a second round, and the filler is cancelled as soon as the first round returns. Options: tell the model to speak first and put gestures inline as `^start(...)` tags, keep the filler alive while tools run, a faster model for spoken turns (Sonnet 5), trim the recogniser's 1 s end-of-speech silence.

Latency fix, same day: the system prompt now tells the model to start answering in words in its first reply, gesture inline with `^start(...)` tags instead of a separate `play_animation`, and say a few words before any tool it needs first; fillers now also run for voice turns (they were limited to typed turns by mistake) and are spoken at once when the model's first reply is a silent tool call. Retest through Pepper's microphone, measured from the final transcript: **first sound 2.6 to 3.9 s** (was 7 to 12.5 s), and the model spoke real words first in three of four model turns; in the fourth ("what can you see") it still went straight to a head turn and photo, so the filler covered the gap and the answer began at 7.4 s. Add roughly 1 s before the transcript for the recogniser's end-of-speech silence. Remaining levers: trim that silence (`rule2_min_trailing_silence`, 1.0 s), a faster model for spoken turns. One mishearing ("Mister Brain" came out as "missus vrain") shows the 2023 zipformer's limits on unusual phrases; the Nemotron streaming model is the accuracy upgrade.

Quick checks with a person present, same day: sonar (hand in front of the base: 0.32 m, clear: 0.7 m), emergency stop during a gesture (rests in 4.5 s), tablet text seen, head tracking followed a person from -48 to +83 degrees with a lock in 59 of 60 samples (it needs to see a face; walking past side-on may not register), web UI typed chat and hold-to-talk. Base moves in an open space the same afternoon: all nine checks passed (turns 31° for 30°, 0.30 m for 0.3 m, move_to returns within a centimetre, drive refused at 0.40 m, emergency stop mid-drive after 0.13 m). **Milestone 0 is complete.**

Model and speech-to-text comparisons (2026-09-22, evening, open public room): all nine base checks passed; five model configurations compared typed and three spoken (results and the decision in ROADMAP.md, "Next test session"); `.env` now uses **Sonnet 5 at medium effort** and the **Nemotron streaming recogniser** (6 % word errors on the recordings from the open room vs 37 % for the zipformer). The model now gets the full date. `scripts/compare_models.py` and `scripts/compare_stt.py` rerun both comparisons; raw results, photos and recordings are in the git-ignored `results/` and `recordings/`, and `recordings/REFERENCES.md` lists the reference transcripts that were guesses. Nemotron was tested live through Pepper in the office on 2026-09-23: all five spoken prompts heard word for word.

Milestone 4, first slice (2026-09-23): debounced people events with distance, gaze and zone from the bridge; `src/world/model.py` and the "Around you" line in the model's state; verified live (three clean events for leave, return, step back). Next in this layer: the greeting test below, then the periodic vision pass on the Creative AI Hub server.

**Session 2026-09-29 (office, then the open room outside it).**
- Greeting newcomers passes on the robot (details in ROADMAP, Milestone 4): picked up at 2.9 m, head turns at once, speech about 2 s later. Run the host with `PEPPER_AWARENESS=true` (now in `.env`) so the head follows people up close and looks out at the room when it is empty.
- The office is too small for people perception: side-on at a desk, the user was never seen as looking; test greetings in the open room.
- Model: `.env` switched to `claude-sonnet-5-5` (medium). Local models run through Ollama on Alien3 (`OLLAMA_URL=http://alien3:11434`, `qwen3-vl:30b-a3b-instruct`): very fast, but describes scenes without looking; not for general use yet.
- The bridge does not start by itself after Pepper boots (`python robot_bridge/deploy.py --restart`); the robot had an older bridge copy until today. `deploy.py` now waits for the old bridge to exit (it rests the robot first) and forces it after 10 s.
- Built after the session, offline (0.4.0, not yet on the robot): sharp photos, a neutral pose after gestures (a greeting wave had left a hand up), and bridge autostart at boot. All tested on NAOqi's desktop build.

**Session 2026-10-01 (office, 0.4.0 checklist done):** autostart verified (software reboot: bridge healthy 68 s after the reboot without anyone starting it); sharp photos verified (ten look-left/right turns, sharpness 400-1411 against 76-200 before, head moves now 1.4-1.6 s, photo 0.48 s, no retakes); greeting with wave and arm down, look-back and face tracking verified; two bugs found and fixed on the robot: the model described an old photo as the current view (now told its last photo's age and whether the head moved), and a paused face tracker pulled the head back before the photo (now held off). The chest-tablet captions were visible for the first time this session, after the reboot; earlier sessions never showed them, so the tablet page may only load cleanly after a boot with the bridge up. Watch for that.

Later the same session: the user directed Pepper by voice (turns of 90° and "a little farther" 30°, a circle as two 180° turns, 0.7 m and 1 m drives, one drive stopped by collision avoidance); "come to me" drove straight ahead instead of towards the user (now the model gets the person's direction), "follow me" was impossible (issue #18). Two visitors then talked with Pepper for a few minutes: it answered everything said near it, including their conversation about it, with long replies while its microphone was muted (issue #19).

**Checklist used for 0.4.0 (done 2026-10-01):**
1. `python robot_bridge/deploy.py` (uploads the 0.4.0 bridge and `launch.sh`), then `python robot_bridge/deploy.py --install-autostart`.
2. Power-cycle Pepper; without running anything, `python robot_bridge/deploy.py --status` must show the bridge healthy (a few minutes after boot). If not: `~/pepper_bridge/bridge.log`, `qicli call ALServiceManager.isServiceRunning pepper-bridge-autostart.PepperBridge`, and NAOqi's log (`/var/log/naoqi/servicemanager/`, or `qicli log-view`). On the desktop build, installing while a bridge already ran made the launcher exit at once (service "terminated", no retry), and the next cold start still autostarted it. `autostart/service.sh` relies on `$HOME` for the service process; if it is not `/home/nao` on the robot, hardcode the path there.
3. Sharp photos: ten "look left/right, what do you see" turns; compare the sharpness scores with the 2026-09-29 photos in `results/models-2026-09-29` (76-200 after head moves) and note how much longer the head move now takes (about 1.3 s for 60 degrees on the desktop build).
4. Neutral pose: get a greeting with a wave; the arm should come down about half a second after Pepper stops speaking. Also play an animation from the web UI.
5. With `PEPPER_AWARENESS=true`, a photo while someone is in view: face tracking pauses for the shot and resumes a second later.
6. Look back: "look to your left and tell me what's there" while standing in front of Pepper; about half a second after it finishes speaking, the head should come back to you. Try with `PEPPER_AWARENESS=true` and `false`.

Still to do on the robot: base moves (deferred until there is a bigger space; the robot was parked half a metre from a desk), people-event debouncing, voice latency, voice recognition with a real backend (`STT_BACKEND=sherpa`), `VOICE_INPUT=true`, and deciding the defaults listed below. Idea for the model: put the installed voices into the system prompt's robot state so it does not try French first.

## Things learned that are not obvious from the docs

- NAOqi 2.5 docs are only reachable over plain `http://doc.aldebaran.com/2-5/`.
- Pepper, not NAO: sonar keys are `Device/SubDeviceList/Platform/{Front,Back}/Sonar/Sensor/Value`; HeadPitch envelope is yaw-coupled (max 25.5° down straight ahead); no manual body stiffness (use `rest()`); `ALSonar` and `ALPeoplePerception` only publish while subscribed.
- Microphone audio reaches a client only through a qi service the client registers (`session.registerService(name, obj)` with a `processRemote` method, then `ALAudioDevice.setClientPreferences(name, 16000, 3, 0)` and `subscribe(name)`); the callback runs on a libqi thread. Pepper has no echo cancellation; the bridge mutes capture around speech using `ALTextToSpeech/Status` events (`[taskId, 'enqueued'|'started'|'done'|...]`, confirmed on real NAOqi).
- On the desktop build: `wakeUp()` takes about 15 s; putting Autonomous Life to `disabled` from `solitary` rests the robot (so `/prepare` sets life first, then wakes); the `Sound` awareness stimulus does not exist there (the bridge now reports unavailable stimuli instead of failing); `ALBasicAwareness` does not pause itself for raw head moves (the bridge pauses it around `/move/head` and resumes 8 s later); a missing animation package yields "Wrong path format ... package/path"; `ALSystem`, `ALAudioDevice`, `ALAudioRecorder`, `ALTabletService` are absent.
- A raw sonar reading of exactly 0.0 means "no measurement" and no longer blocks moves.
- Claude Opus 5 at `effort=low` occasionally writes a tool call as XML text; the manager suppresses and retries once.
- The reactive layer and voice design borrow from Autonomous OS (intent table, state signals, safety ledger); see `RESEARCH_2026-09.md`.

## Plan for the robot session on Tuesday 2026-10-06

Theme: **System one in the loop.** Fast local decision models (#20) judge the situation, the bridge acts at once, Claude does the words. Everything is recorded and reviewed (#16). Best in the open room, ideally with two or three people.

**Build before Tuesday (offline, tested on the desktop NAOqi and with recorded data):**

| # | What | Issue | Done when |
|---|------|-------|-----------|
| A | Session records: one folder per host run (`SESSION_DIR`), a per-turn JSON-lines record (end of speech, transcript ready, router and addressee decisions, first word, end of reply, tools, interruptions, people state), and `scripts/review_session.py` printing a timed transcript with flags | #16 | replaying the 2026-10-01 logs through the review script shows the visitor problem at a glance |
| B | Addressee gate: Nimble judges every voice turn ("addressed to Pepper?"); above the threshold Pepper answers, below it Pepper only looks at the speaker; gaze from the people detector as extra evidence; `ADDRESSEE_GATE`, threshold in `.env` | #19, #20 | the 2026-10-01 utterances replayed through the gate give the offline 21/22 |
| C | Command router: Nimble picks the first physical action; head moves, gestures, turns in place, stop and be quiet start at once (p ≥ 0.7), amounts from a small parser, Claude told what already started; drives still via Claude | #21 | the 40-utterance test passes through the real code path; on the desktop NAOqi a routed turn starts within 0.3 s of the transcript |
| D | Frame stream: the bridge keeps one camera subscription and streams small frames (320×240 JPEG) over a WebSocket while someone is in view; the host sends about one a second to Clef Flash (waving? holding something up? facing Pepper?) into the world model, with events on threshold crossings; logged | #11, #20 | end to end on the desktop NAOqi (black frames) with timings; Clef path timed on saved photos |
| E | Bump to 0.5.0 if A-D land (bridge changes in D), redeploy | | `deploy.py` shows 0.5.0 |

**Tests on Tuesday (in this order; each recorded and reviewed the same day):**
1. Start-up: Pepper boots, bridge up by itself, start the host with a session folder. 2 min.
2. "Come to me" from about 30 degrees off to one side, 2 m away (the direction fix from 2026-10-01): Pepper should turn towards you, then drive. 3 tries.
3. Router: ten direct commands, half of them paraphrased ("glance right", "face the other way", "give us a wave", "nod if you hear me", "turn left ninety degrees"). Measure end of speech to movement start (target under 0.5 s, against about 3.5 s before), wrong actions (target 0), and that Claude's words match the action.
4. Addressee gate: two people talk to each other near Pepper for two minutes (about Pepper too), then one turns to Pepper and asks something. Count: side talk answered (target 0 or 1), direct questions missed (target 0).
5. Frame stream: at 1 frame per second, then 2, measure frame to judgement time, Wi-Fi and robot CPU. Then wave at Pepper, hold up an object, and turn away: time from the gesture to Pepper's reaction (target about 1 s).
6. A free conversation with whoever is around, for the records.
7. Review: run the review script, file what it shows, update the wiki Test-sessions page.

**Next milestones after Tuesday:** finish Milestone 4 (vision pass into the world model, #11; the detail tool, #12), get the spoken loop under 2 s and barge-in (#3, #4), verify-after-act and `look_at` (#7, #6), then follow me (#18) and memory of people (Milestone 5, opt-in only).

## Session records

Every robot session is recorded in full and reviewed afterwards; real interactions (the 2026-10-01 visitors, for example) are the main source of fixes. How, and the privacy rules, are in `CLAUDE.md` under "Session records". Records so far (local, git-ignored):

| Date | Logs | Audio | Photos |
|------|------|-------|--------|
| 2026-09-22 | (host log not kept separately) | `recordings/round1-3*`, `round4-nemotron-office` | `results/models-2026-09-22/` |
| 2026-09-29 | `results/logs/blind-*.log`, `greeting*-2026-09-29.*`, `results/llm/` | `recordings/round5-blind-*`, `recordings/greeting-2026-09-29` | `results/models-2026-09-29/`, `results/logs/greeting-view*.jpg` |
| 2026-10-01 | `results/logs/{photos,photos2,photos3,photos4,drive}-2026-10-01.*` (photos4: directing Pepper and the two visitors) | `recordings/2026-10-01/` (28 utterances) | `results/photos-2026-10-01/` (ten-turn measurement), `results/photos/` |

What the records cannot show yet, and the plan to close it, is in GitHub issue #16: one session folder per run instead of hand-named files, per-turn timings (end of speech, transcript, first word, end of reply), people events in the host log, and a review script that turns a session into a readable transcript with timings.

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

`pip install -r requirements-voice.txt`, download the sherpa-onnx streaming zipformer named in `GETTING_STARTED.md`, set `STT_BACKEND=sherpa` and `STT_MODEL=<model dir>`; hold-to-talk needs the UI at `http://localhost:8000`; `VOICE_INPUT=true` adds the robot microphone; `VOICE_RECORD_DIR=recordings` saves each utterance as a WAV with the live transcript beside it (`.hyp.txt`), in streaming mode too since 2026-09-22; add corrected `.ref.txt` files and run `scripts/compare_stt.py` to compare recognisers.

## Decisions waiting on the live session

- `PEPPER_AWARENESS` default is `false`; try head tracking (roadmap step 12) and decide whether the 8 s resume after a head move feels right, and whether the `Sound` stimulus makes the head twitch during conversation.
- Microphone mute tail (0.4 s) and the energy endpointer thresholds for the whisper path; whether a neural VAD is needed.
- Filler delay (2 s), LED colours in daylight, speech volume, the 0.45 m obstacle threshold, `bodyLanguageMode` for animated speech, which animations are installed.
- Barge-in (a spoken "stop" while Pepper talks) is not possible with the mute as designed; decide after seeing how much Pepper hears of itself.

## Next milestones after M0

the spoken-turn model comparison (roadmap, next test session), M3 vision grounding (`look_at(x, y)` from a point in the last photo, verify-after-act), M4 world model (debounced people events, periodic scene understanding, an "around you" line in the model's context, greeting newcomers), M5 memory and people identity, M6 operations (bridge autostart, latency budget, session recording). Small reactive-layer leftovers: chest LED for errors, an idle "thinking" gesture, queue policy for speech that arrives while a turn runs.
