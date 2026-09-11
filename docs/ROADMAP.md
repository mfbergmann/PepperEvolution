# Roadmap

Last updated: September 2026. See [RESEARCH_2026-09.md](RESEARCH_2026-09.md) for the survey this roadmap is based on.

## Where we are

v2.1 was a complete rewrite of the bridge and the host (September 2026); v2.2 added the reactive layer (Milestone 1) and voice input (Milestone 2) on top, borrowing the design of the local intent table, state signals and safety ledger from Autonomous OS (see the research notes). Everything has been verified off-robot only:

- About 380 tests, including the real bridge process running under a fake NAOqi that also pumps microphone frames.
- The bridge suite also passes under Python 2.7.18 + Tornado 3.1.1, the robot's own environment.
- Live runs against the Anthropic API with the simulated robot (tool calls, photo description, touch reactions, streaming speech).
- Adversarial multi-agent reviews; every confirmed finding fixed with a regression test.

**It has never been run on the physical robot.** Milestone 0 is that first contact.

## Strategy in one paragraph

Pepper's brain stays off-board. The robot exposes its hardware as documented, safety-bounded tools over a bridge; Claude plans, speaks and calls tools; NAOqi's own skills and reflexes (animations, `moveTo`, collision protection) do the fast, low-level work. The 2026 survey confirmed this is the state of the art for Pepper specifically: every serious Pepper/NAO language-model system uses the same two-process pattern, Anthropic's own robotics evaluation found frontier models are good at supervising skills and bad at joint-level control, and Google's embodied-reasoning API is structurally the same design. Robot foundation models (VLAs) are not on this roadmap; the reasons are in the research notes.

## Milestone 0: first live session

Goal: the bridge runs on Pepper, every endpoint answers, one AI conversation works end to end.

Checklist (in order; stop and fix before moving on):

1. Robot on, `ping 10.0.100.100`, `ssh nao@10.0.100.100` works.
2. `python robot_bridge/deploy.py` reports healthy. Watch `bridge.log` for the NAOqi connect and the extractor subscriptions.
3. `curl .../status` and `.../sensors`: confirm sonar values change when a hand is held in front; confirm `sonar_ok`/`people_ok`.
4. `POST /prepare`: confirm Autonomous Life goes to `disabled` (it may be refused until the setup wizard has been completed on the tablet; the bridge reports this in `errors`) and the robot wakes.
5. `POST /speak` plain and animated; `POST /speak` with `language: fr`; confirm the language is restored.
6. `POST /animation` with `Hey_1`; `GET /animations` to capture the installed list into the docs.
7. `POST /move/head`, then `POST /move/turn 30`, then `POST /move/forward 0.3` with a clear path, then again with a hand in front (must be refused).
8. `POST /emergency_stop` while an animation is running: animation stops, robot rests, `halted` is true; `POST /wake_up` recovers.
9. `python examples/event_monitor.py`: touch head, hands, bumpers; check `people` events with someone in view.
10. `GET /picture` at resolutions 1 and 2; check the JPEG opens and the exposure is usable.
11. `POST /tablet/text`: confirm the tablet can reach `http://198.18.0.1:8888/tablet/page`.
12. `POST /awareness {"enabled": true, "tracking_mode": "Head"}`: the head should follow a person walking past; `POST /move/head` must still work (the bridge pauses tracking and resumes it 8 s later; check the `Sound` stimulus is accepted on the robot and whether it makes the head twitch while talking).
13. `curl http://10.0.100.100:8888/audio/stream`, then `python examples/mic_monitor.py`: `streaming` goes true, `frames` climbs, the level meter moves when someone talks, and `dropped` climbs during `POST /speak` (the robot must not hear itself).
14. `python main.py`, open the UI, run the first prompts from GETTING_STARTED; then `examples/basic_chat.py`. Say "stop" mid-reply (typed) and check the eyes change colour with the turn.
15. With `STT_BACKEND` set: hold-to-talk from `http://localhost:8000`; then `VOICE_INPUT=true` and talk to the robot from a metre away. Save utterances with `VOICE_RECORD_DIR` for tuning.

Things most likely to need adjusting on the day: `bodyLanguageMode` config on `ALAnimatedSpeech.say`, head-pitch comfort limits, the 0.45 m obstacle threshold, speech volume, the microphone mute tail (0.4 s) and the energy-detector thresholds, and which animations are actually installed. [SAFETY.md](SAFETY.md) lists every bound.

## Milestone 1: reactive layer (implemented in v2.2, unverified on the robot)

Goal: Pepper looks alive while Claude is thinking, without any model call.

- [x] Gaze: `/prepare` can enable `ALBasicAwareness` head-only tracking (people, sound, touch stimuli); NAOqi pauses it while `move_head` runs and resumes right afterwards. Off by default (`PEPPER_AWARENESS`) until the live session shows whether the resume undoes the model's "look left" too quickly; "look at me" / "look ahead" toggle it.
- [x] State signalling: eye LEDs blue / purple / white for listening / thinking / speaking, restored to the last chosen colour after the turn (`LED_STATE_SIGNALS`).
- [x] Backchannels: a short verbal filler after `BACKCHANNEL_AFTER` (2 s) without model text, user turns only.
- [x] Local intents: stop / emergency stop / quiet / wake up / rest / look at me / look ahead, matched before the turn lock and executed in milliseconds; "stop" cancels the remaining tool calls of a running turn and silences it (`src/ai/intents.py`).
- [x] Reflexes stay on the bridge: collision protection, sonar guard, halt on emergency stop, microphone mute while speaking.
- [ ] Chest LED for errors; an idle "thinking" gesture (animation) instead of, or in addition to, the filler.
- [x] `move_head` pauses awareness and a bridge-side timer resumes it 8 s later, so the model's gaze commands hold (found on the virtual robot: NAOqi only pauses tracking for its own activities).
- [ ] Tune on the robot: filler delay, LED colours in daylight, whether the `Sound` stimulus makes the head twitch during conversation, the 8 s resume delay.

Acceptance: perceived latency in a conversation drops; no model calls are made by this layer.

## Milestone 2: voice input (implemented in v2.2, unverified on the robot)

Goal: people talk to Pepper instead of typing.

- [x] Bridge: `/ws/audio` streams 16 kHz mono PCM from the front microphone through a qi service subscribed to `ALAudioDevice`, only while a client is connected; capture is muted around `/speak`, on `ALTextToSpeech/Status` events and for 0.4 s after speech (Pepper 1.8 has no echo cancellation).
- [x] Host: `src/audio/` with two backends behind one interface: `sherpa-onnx` streaming transducer with its own endpointing (recommended; partial transcripts, ~80 MB zipformer or the 2026 Nemotron streaming model), and `faster-whisper` per utterance behind an energy endpointer. Finals go to `process_user_input(source="voice")`; control phrases are intents.
- [x] Turn taking: hold-to-talk in the web UI (browser microphone, raw PCM to `POST /voice/utterance`); the robot microphone path is opt-in with `VOICE_INPUT`.
- [x] Transcript logging (`VOICE_RECORD_DIR`) for tuning on real audio.
- [ ] Measure end-of-speech to first spoken word on the robot; target under 2 s (the literature reports 4 to 9 s for naive cascades, 1.35 s for a well-engineered one).
- [ ] Barge-in: let a spoken "stop" interrupt while Pepper is speaking. Today the bridge mutes the microphone for the whole reply (sentences follow each other with a 0.4 s tail), so a spoken "stop" only lands between turns; typed, button and hold-to-talk stops work at any time. Needs either echo cancellation on the host (the bridge would have to stream during speech) or keyword spotting on the muted-out audio; not planned before M0 shows how much Pepper hears of itself.
- [ ] A neural VAD (Silero/TEN through sherpa-onnx) in front of the whisper path if the energy detector proves too crude in the lab.

Acceptance: a five-turn spoken conversation with tool use, end to end.

## Milestone 3: vision grounding

Goal: Claude can act on what it sees, not just describe it.

- `look_at(x, y)`: the model returns a point in the last photo; the bridge converts it to head angles using the camera field of view.
- Verify-after-act: for moves and gestures with a visible effect, take a photo and let the model judge success before continuing.
- Optional depth frames from the 3D camera for distance questions.

Acceptance: "look at the person on the left", "is the door open?", "go towards the chair" work reliably.

## Milestone 4: memory and people

Goal: Pepper remembers who it talked to and what was said.

- Host-side memory tools: remember/recall facts about people and the lab, with retrieval over past sessions.
- Face or voice identity through an external service; NAOqi's people IDs are session-local.
- Keep tool count small; multi-step task completion on social robots is still fragile in the literature.

## Milestone 5: robustness and operations

- Bridge autostart on robot boot (`autoload.ini`) and a watchdog.
- Latency budget per turn in the log; per-sentence speech timing.
- Session recording (transcripts, photos, tool calls) for later analysis.

## Testing without the robot

Three levels, from cheapest to most faithful:

1. **`PEPPER_FAKE_BRIDGE=true`** (host only): the whole host stack against an in-memory robot. Checks the model, tools, speech streaming, voice input with `STT_BACKEND=fake`, and the UI.
2. **`tests/fakenaoqi`** (bridge included): the real bridge process under a fake `qi` module that answers every NAOqi call with plausible data, toggles a touch sensor and pumps microphone frames. This is what the test suite runs, also under Python 2.7 + Tornado 3.1.1. It proves the bridge's threading, HTTP and WebSocket behaviour, not that the NAOqi calls exist.
3. **A virtual Pepper on NAOqi's own desktop binary** (`scripts/virtual_pepper.sh`, done 2026-09-11). Choregraphe 2.5 ships `naoqi-bin`, the same NAOqi that runs on the robot, compiled for Linux x86-64; it runs headless and Pepper is selected by editing `etc/naoqi/ALRobotModel.xml` to `JULIETTEY20MP.xml`. The bridge runs against it under Python 2.7 with the `pynaoqi` SDK (a shared, UCS4 Python 2.7; mise's 2.7.18 qualifies). The original archives are still served from `community-static.aldebaran.com` (Choregraphe suite; the Python SDK only from GitHub mirrors). `tests/test_virtual_naoqi.py` (opt-in with `PEPPER_VIRTUAL_BRIDGE`) exercises every endpoint against it. Verified there: every method name and signature the bridge calls on `ALMotion` (including `moveTo` with a velocity config), `ALRobotPosture`, `ALTextToSpeech` and its `Status` events, `ALAnimatedSpeech` with the body-language config, `ALBasicAwareness` modes and stimuli, `ALAutonomousLife` transitions, `ALLeds`, `ALMemory` subscribers, `ALVideoDevice`, `qi` service registration, plus the refusals while resting or halted and the emergency-stop/wake-up sequence. Found and fixed there: a raw sonar value of 0.0 blocked every move; a stimulus the build lacks (`Sound`) failed the whole awareness call; awareness does not pause itself for raw head moves. Cannot verify: sonar, touch and battery values, camera content, `ALAudioDevice` (microphone stream), `ALTabletService`, the animation package, timing, the room.

qiBullet (PyBullet with a Pepper model) and the ROS 2 / Gazebo Pepper packages simulate physics with their own APIs, not NAOqi's, so they would not exercise the bridge. Webots has no Pepper model.

## Watch list (no action now)

- Anthropic's Model Hardware Standard (research preview, August 2026): a way to describe a robot as tools an agent can discover. The bridge could publish a descriptor once the spec is open.
- Embodied-reasoning APIs (Gemini Robotics-ER 2) as a second opinion for pointing and spatial questions, if Claude's grounding proves insufficient.
- Navigation models that emit waypoints from RGB (the most embodiment-agnostic action space in the survey); only relevant if the lab wants longer autonomous drives than `ALNavigation` handles.

## Non-goals

- Running a vision-language-action model to drive Pepper's joints. Wrong action space, no Pepper data, no on-robot GPU, nothing to manipulate. See the research notes.
- On-robot inference of any kind. The Atom CPU runs the bridge and nothing else.
- Migrating to NAOqi 2.9 / QiSDK. 2.5 gives raw audio, camera and joint access that the language-model stack needs; 2.9 hides them behind Kotlin APIs.
- Generating motion trajectories with the model. Gestures stay discrete intents mapped to the animation library.
