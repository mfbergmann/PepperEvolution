# Roadmap

Last updated: September 2026. See [RESEARCH_2026-09.md](RESEARCH_2026-09.md) for the survey this roadmap is based on.

## Where we are

v2.1 is a complete rewrite of the bridge and the host (September 2026). It has been verified off-robot only:

- 259 tests, including the real bridge process running under a fake NAOqi.
- The bridge suite also passes under Python 2.7.18 + Tornado 3.1.1, the robot's own environment.
- Live runs against the Anthropic API with the simulated robot (tool calls, photo description, touch reactions, streaming speech).
- An adversarial multi-agent review; all 28 confirmed findings fixed with regression tests.

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
12. `python main.py`, open the UI, run the first prompts from GETTING_STARTED; then `examples/basic_chat.py`.

Things most likely to need adjusting on the day: `bodyLanguageMode` config on `ALAnimatedSpeech.say`, head-pitch comfort limits, the 0.45 m obstacle threshold, speech volume, and which animations are actually installed.

## Milestone 1: reactive layer

Goal: Pepper looks alive while Claude is thinking, without any model call.

- Gaze: track the speaking or nearest person with `ALBasicAwareness`-style head tracking that yields to explicit `move_head` calls and resumes afterwards.
- State signalling: eye LEDs for listening / thinking / speaking, chest LED for errors.
- Backchannels: an idle "thinking" gesture or a short verbal filler after ~1.5 s of model latency; interruptible.
- Reflexes stay on the bridge: collision protection, sonar guard, halt on emergency stop.

Acceptance: perceived latency in a conversation drops; no model calls are made by this layer.

## Milestone 2: voice input

Goal: people talk to Pepper instead of typing.

- Bridge: stream 16 kHz mono microphone audio over a WebSocket (`ALAudioDevice` remote module), muting capture while Pepper speaks (Pepper 1.8 has no echo cancellation).
- Host: streaming speech-to-text with end-of-utterance detection, then the existing `process_user_input`. Target under 2 s from end of speech to first spoken word (the literature reports 4 to 9 s for naive cascades, 1.35 s for a well-engineered one).
- Turn taking: a simple push-to-talk fallback in the UI; later, interruption handling.
- Known failure mode from every Pepper study: speech recognition with accents, dialects and noise. Log transcripts for tuning.

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

## Watch list (no action now)

- Anthropic's Model Hardware Standard (research preview, August 2026): a way to describe a robot as tools an agent can discover. The bridge could publish a descriptor once the spec is open.
- Embodied-reasoning APIs (Gemini Robotics-ER 2) as a second opinion for pointing and spatial questions, if Claude's grounding proves insufficient.
- Navigation models that emit waypoints from RGB (the most embodiment-agnostic action space in the survey); only relevant if the lab wants longer autonomous drives than `ALNavigation` handles.

## Non-goals

- Running a vision-language-action model to drive Pepper's joints. Wrong action space, no Pepper data, no on-robot GPU, nothing to manipulate. See the research notes.
- On-robot inference of any kind. The Atom CPU runs the bridge and nothing else.
- Migrating to NAOqi 2.9 / QiSDK. 2.5 gives raw audio, camera and joint access that the language-model stack needs; 2.9 hides them behind Kotlin APIs.
- Generating motion trajectories with the model. Gestures stay discrete intents mapped to the animation library.
