# Roadmap

Last updated: September 2026. The design these milestones build towards is in [ARCHITECTURE.md](ARCHITECTURE.md); the survey behind it is in [RESEARCH_2026-09.md](RESEARCH_2026-09.md).

## Where we are

*Updated 2026-10-09, version 0.7.0.* The bridge and host were rewritten in September 2026 (0.1), with a reactive layer and voice input (0.2). They have run on the physical Pepper since 2026-09-22 (Milestone 0), in six robot sessions so far: the office, the open space outside it, and an evening with two visitors. Each session is recorded and reviewed, and what it shows becomes the next fixes (`docs/HANDOFF.md` has the details per session; the wiki's Test-sessions page has the summary).

What works on the robot today:
- **Conversation by voice** through Pepper's own microphone. NVIDIA's Nemotron streaming recogniser runs on the host's CPU, and Claude Sonnet 5.5 answers about 2 s after the transcript; it speaks sentence by sentence with gestures and tablet captions.
- **Reflexes without a model:** "stop" and the other control phrases in milliseconds, eye-colour states, fillers, the head turning to newcomers, face tracking while someone is in view, looking out at the room when nobody is.
- **A world model** of who is in view (distance, direction, gaze) that greets people who walk up and tells the model each turn who is around and where.
- **Fast local judgements** on a GPU machine (Milestone 4 groundwork, 0.5):
  - whether speech was meant for Pepper (side talk gets no reply);
  - which simple action to start at once (Pepper moves about 0.25 s after the transcript while Claude prepares the words);
  - whether someone in view is waving or showing something.
  All of them fail open: without the GPU machine Pepper behaves as before, just slower.
- **Sharp photos,** a handshake that holds and notices being taken, short drives and turns with the sonar guard, the bridge starting by itself at boot, and session records with a review script.

- **Memory, built offline in 0.6.0 and not yet on the robot** (`docs/MEMORY.md`):
  - Working memory keeps Pepper's own pose and where people were after they leave view; replayed on the 2026-10-08 sessions, a person's direction was predicted within a median of 7°.
  - It also knows where a voice came from and keeps occasional scene notes.
  - Long-term memory keeps people who agreed to be remembered, with consent checked in code and "forget me".

- **Built offline in 0.7.0, also not yet on the robot:**
  - waiting for the rest of a sentence that stops mid-way (#26);
  - shorter replies (#19);
  - pointing and looking at a spot in a photo (#28, #6);
  - a camera fallback that completes "come to me" (#24);
  - a bridge watchdog (#15);
  - a first, opt-in initiative loop (Pepper may speak unprompted).

What is not there yet:
- **All of the above on the robot:** the checks in `docs/HANDOFF.md`; then identity (face or voice, opt-in, #14).
- **Spatial grounding:** "come to me", pointing, looking at a point in a photo, follow me (Milestone 3).
- **Conversation:** side talk with two people still to be tested, and barge-in (Milestone 2).

Off the robot, about 670 tests run in CI (including the real bridge process under a fake NAOqi and the bridge suite under Python 2.7 + Tornado 3.1.1); NAOqi's desktop build runs as a headless virtual Pepper; and recorded sessions can be replayed.

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

**Status (2026-09-22): Milestone 0 is complete.** Base moves, done later the same day in an open space with odometry from `ALMotion.getRobotPosition`: turns of ±30° measured 31°, a 0.3 m drive forward and back measured 0.30 m each way, `move_to` 0.2 m out and back returned to within a centimetre, the drive was refused with a leg 0.40 m in front of the base, and an emergency stop one second into a 0.15 m/s drive stopped the robot after 0.13 m and rested it (the call returns after about 5 s, once the rest has finished), after which driving was refused until wake-up. Earlier checks: Checked with a person present: the front sonar reads 0.32 m with a hand in front of the base and 0.7 m without (the sonars are in the base, near the floor); the emergency stop during a gesture stops it and rests the robot in 4.5 s; the tablet showed the test text; head tracking locked on to a person in 59 of 60 samples and followed them from -48 to +83 degrees; the web UI with typed chat and hold-to-talk worked.

Five bugs only the hardware showed were fixed on the day (deploy path, emergency-stop rest, looping animations, speech stop, microphone frame size); see HANDOFF.md.

## Milestone 1: reactive layer (implemented in 0.2, verified on the robot 2026-09-22)

Goal: Pepper looks alive while Claude is thinking, without any model call.

- [x] Gaze: `/prepare` can enable `ALBasicAwareness` head-only tracking (people and touch stimuli). On the robot it runs while someone is in view and is switched off for an empty room (`PEPPER_AWARENESS=true` in the lab since 2026-09-29). The bridge holds it off for deliberate head moves (8 s) and photos (1 s), because a tracker locked on a face pulls the head back even while paused. "Look at me" / "look ahead" toggle it.
- [x] State signalling: eye LEDs blue / purple / white for listening / thinking / speaking, restored to the last chosen colour after the turn (`LED_STATE_SIGNALS`).
- [x] Backchannels: a short verbal filler after `BACKCHANNEL_AFTER` (2 s) without model text, user turns only.
- [x] Local intents: stop / emergency stop / quiet / wake up / rest / look at me / look ahead, matched before the turn lock and executed in milliseconds; "stop" cancels the remaining tool calls of a running turn and silences it (`src/ai/intents.py`).
- [x] Reflexes stay on the bridge: collision protection, sonar guard, halt on emergency stop, microphone mute while speaking.
- [ ] Chest LED for errors; an idle "thinking" gesture (animation) instead of, or in addition to, the filler.
- [x] `move_head` pauses awareness and a bridge-side timer resumes it 8 s later, so the model's gaze commands hold (found on the virtual robot: NAOqi only pauses tracking for its own activities).
- [x] Spoken touch reactions on the robot: one short reply per touch, gestures chosen by the model, a double touch gives one reply.
- [x] Head reflexes (2026-09-29): turn to a newcomer at once, look out at the room 3 s after the last person leaves; greeting is in Milestone 4.
- [x] Command router (0.5, issue #21): a local decision model picks the first physical action from Pepper's fixed list, and head moves, gestures, turns in place and the handshake start about 0.25 s after the transcript; Claude still does the words. On the robot 2026-10-08: 32 actions started, one wrong ("turn back" → turn around).
- [ ] Tune on the robot: filler delay, LED colours in daylight (#2). The `Sound` stimulus is left off; the 8 s hold after a head move works.

Acceptance: perceived latency in a conversation drops; no model calls are made by this layer.

## Milestone 2: voice input (implemented in 0.2, verified on the robot 2026-09-22)

Goal: people talk to Pepper instead of typing.

- [x] Bridge: `/ws/audio` streams 16 kHz mono PCM from the front microphone through a qi service subscribed to `ALAudioDevice`, only while a client is connected; capture is muted around `/speak`, on `ALTextToSpeech/Status` events and for 0.4 s after speech (Pepper 1.8 has no echo cancellation).
- [x] Host: `src/audio/` with two backends behind one interface: `sherpa-onnx` streaming transducer with its own endpointing (recommended; partial transcripts, ~80 MB zipformer or the 2026 Nemotron streaming model), and `faster-whisper` per utterance behind an energy endpointer. Finals go to `process_user_input(source="voice")`; control phrases are intents.
- [x] Turn taking: hold-to-talk in the web UI (browser microphone, raw PCM to `POST /voice/utterance`); the robot microphone path is opt-in with `VOICE_INPUT`.
- [x] Transcript logging (`VOICE_RECORD_DIR`) for tuning on real audio.
- [x] Measured on the robot: 7-12.5 s from the final transcript to the first sound at first; after the "speak first" prompt and filler changes, 2.6-3.9 s; with Sonnet 5.5 (2026-10-08) a median of about 2 s, plus 1 s of end-of-speech silence before the transcript. Target is still under 2 s from end of speech (#3).
- [x] Addressee gate (0.5, #19): open-microphone speech is answered only when it seems meant for Pepper. Alone with Pepper, anything said in a conversation is answered (0.5.1). Still to test with two people; the judgement should also see what Pepper just said (#25).
- [x] Sentences cut in two at a pause longer than 1 s (#26): built in 0.7.0. A word rule holds a clearly unfinished utterance for 2.5 s and joins what follows (10 of 11 recorded cut-offs, none of 140 complete utterances held). Robot test next.
- [x] Shorter replies in conversation (#19): built in 0.7.0. One or two sentences, then stop. Replayed on 37 recorded turns: 3 sentences on average down to 2.3, replies of four or more from about 42 % to about 20 %. Robot test next.
- [x] Acceptance met: a five-turn spoken conversation with tool use (head turns, photos, gestures, the quiet intent) through Pepper's own microphone, twice.
- [ ] Barge-in: let a spoken "stop" interrupt while Pepper is speaking. Today the bridge mutes the microphone for the whole reply (sentences follow each other with a 0.4 s tail), so a spoken "stop" only lands between turns; typed, button and hold-to-talk stops work at any time. Needs either echo cancellation on the host (the bridge would have to stream during speech) or keyword spotting on the muted-out audio; not planned before M0 shows how much Pepper hears of itself.
- [ ] A neural VAD (Silero/TEN through sherpa-onnx) in front of the whisper path if the energy detector proves too crude in the lab.

Acceptance: a five-turn spoken conversation with tool use, end to end.

## Model comparison for spoken turns (done 2026-09-22 and 2026-09-29)

Decision: **Claude Sonnet 5.5 at medium effort** for spoken turns (blind spoken rounds on the robot, 2026-09-29), now the default. The plan as it was written follows, then the results.


Goal: pick the model (and effort) that gives the best spoken conversation on Pepper, now that the recogniser and the "speak first" prompt have taken latency from 7-12.5 s to 2.6-3.9 s after the transcript (2026-09-22). The remaining time is mostly the model's first round.

- Candidates, each through `AI_MODEL` / `AI_EFFORT` with nothing else changed:
  - `claude-opus-5` at `effort=low` (today's default; $5 / $25 per million tokens).
  - `claude-opus-5-5` at `effort=low` ($4 / $20). Thinking cannot be turned off on this model; effort is the only control and its default is `medium`, so `AI_EFFORT=low` must be set explicitly. The provider needs no code change (it never disables thinking or forces a tool, and already sends `output_config.effort`). Two things to watch: its thinking blocks only replay on the same model, so switching models mid-conversation drops them; and text it writes between tool calls that runs longer than a sentence or two comes back as hidden "progress update" thinking blocks instead of text, which would swallow a spoken preamble such as "Let me have a look." Short preambles are expected to stay as text; check on the robot, and if they vanish, try `thinking.display: "updates"` (beta `thinking-display-updates-2026-08-18`) and speak those summaries.
  - `claude-sonnet-5` at `low` and `medium` ($2 / $10).
  - `claude-haiku-4-5` ($1 / $5; no effort parameter, so the provider sends none).
  Check ids and prices against the API docs on the day; the Opus 5.5 details above come from the reference current on 2026-09-22.
- Method: the same scripted set on the robot for every candidate, run through `scripts/smoke_host.py` (typed, repeatable) and then a short guided spoken run for the finalists. Prompts cover greeting, "look left", "what do you see" (photo), a gesture request, a multi-step request, a stop mid-turn, and a factual question.
- Measure per turn: time to first sound and to first real words after the transcript, total turn time, tool calls made and whether they were right (silent tool calls before speaking count against), phantom tool calls, spoken length, and cost from the usage block. Judge the photo descriptions for accuracy against the actual image.
- Decide: a default for spoken turns, and whether typed turns (web UI) should keep a stronger model. `AIManager` could route by `source` if the split is worth it.
- Also try the recogniser's end-of-speech silence at 0.6 s instead of 1.0 s (`rule2_min_trailing_silence`), checking that people are not cut off mid-sentence. (Dropped 2026-10-08: sentences are already cut at 1.0 s; see #26.)
- Speech-to-text comparison, alongside: run the spoken part with `VOICE_RECORD_DIR=recordings` (each utterance is saved as a WAV with the live transcript beside it), correct a copy of each transcript into a `.ref.txt`, and compare offline with `scripts/compare_stt.py recordings --sherpa <zipformer dir> --sherpa <nemotron dir> --whisper small`. Candidates: today's 2023 streaming zipformer (about 80 MB of weights), NVIDIA's Nemotron speech streaming 0.6B int8 (2026, about 630 MB, same library and API, more accurate), faster-whisper `small` per utterance, and, if local accuracy disappoints, a hosted recogniser with its own end-of-turn detection (Deepgram, AssemblyAI; needs an account and sends audio off the machine). Judge word error rate on your own voice in that room, recognition time, and end-of-turn delay.

### Results, typed phase (2026-09-22, `scripts/compare_models.py`, seven prompts per configuration on the robot)

| Configuration | First words (median) | Answered in words first | Total turn (median) | Cost for 7 turns |
|---------------|---------------------:|------------------------:|--------------------:|-----------------:|
| Opus 5, effort low | 3.0 s | 7 of 7 | 17.9 s | $0.104 |
| Opus 5.5, effort low | 3.1 s | 4 of 7 | 25.8 s | $0.074 |
| Sonnet 5, effort low | 3.1 s | 4 of 7 | 18.9 s | $0.043 |
| Sonnet 5, effort medium | 2.8 s | 6 of 7 | 16.4 s | $0.043 |
| Haiku 4.5 | 1.9 s | 6 of 7 | 21.9 s | $0.033 |

Times are from sending the text to Pepper's first spoken sentence, so the spoken latency adds about 1 s of end-of-speech detection. Every configuration described the photos accurately (checked against the images), waved with an inline gesture tag, and got the factual answer right. Differences: Opus 5 was the most consistent at speaking first but costs two to three times more; Opus 5.5 at low effort was slower, used fillers more often and gave the longest replies; Sonnet 5 at medium effort was as fast as at low, spoke first more often and cost the same; Haiku 4.5 was clearly fastest and cheapest but its descriptions were vaguer and it sometimes narrated its own steps aloud ("Now let me take a photo…"). One run of seven prompts, so single turns are noisy (the multi-step prompt produced 8-9 s outliers for Sonnet). Finalists for the spoken phase: **Sonnet 5 at medium** (best balance) and **Haiku 4.5** (fastest), with Opus 5 as the reference.

### Results, spoken phase (2026-09-22, Pepper's own microphone, a large open public room)

Three blind rounds of the same five spoken prompts, one model per round:

| Model | First words after the transcript | Notes |
|-------|----------------------------------|-------|
| Sonnet 5, effort medium | 2.2, 4.6, 3.0, 3.3 s | Best description of the person (glasses, beard, navy shirt) and noticed the photo was blurry; one filler before speaking |
| Haiku 4.5 | 2.1, 2.3, 2.1, 1.9 s | Fastest every time; good-natured about being misheard ("I'm Pepper, not Hydropper"); descriptions accurate but plainer |
| Opus 5, effort low | 2.9, 3.3 s | The first greeting was misheard badly and every later reply landed one prompt late, so this round says little about Opus |

The recogniser struggled in the open room: "I pepper", "Looked here left", "Hydropper", "What is to day state", "Glowed up bernoia". Recognition, not the model, is now the weak point of the spoken loop; the utterances of all three rounds are saved in `recordings/round*` for the speech-to-text comparison. All three models said they did not know the date: the state line only gave weekday and time, now fixed to include the full date.

### Results, 2026-09-29: Sonnet 5.5 and a local model

Typed round on the robot (7 prompts, medians): Sonnet 5.5 at low effort 2.5 s to first words, at medium 2.6 s, both 7 of 7 turns opening with real words; Sonnet 5 at medium 2.7 s, 5 of 7. A local model on the Creative AI Hub's Alien3 (RTX 5090, Ollama, `qwen3-vl:30b-a3b-instruct`, `OLLAMA_URL`): 1.3 s to first words (0.19 s to the first sentence in the raw benchmark, `scripts/bench_llm.py`, against 1.14 s for Sonnet 5), but it described scenes without taking a photo (invented chairs, a whiteboard, books) in 2 of 2 turns on the robot and about half the time once a conversation had history; when it does look, its descriptions are accurate. Blind spoken round, Sonnet 5.5 against Sonnet 5 at medium: the user judged them about equal. Same price ($2 / $10 per million tokens).

**Decision (2026-09-29):** `.env` uses `claude-sonnet-5-5` at medium effort. The local model stays an option for fast social lines (greetings need no photo) once its habit of describing without looking is fixed or fenced.

### Decision (2026-09-22)

The user judged Sonnet 5 at medium effort best in the spoken rounds; one caveat noted: it described glasses the user does not wear. `.env` now uses `AI_MODEL=claude-sonnet-5`, `AI_EFFORT=medium`. Opus 5 remains the code default in `main.py` for anyone without a `.env`.

### Results, speech-to-text comparison (2026-09-22, `scripts/compare_stt.py`, offline on the 15 recordings from the spoken rounds)

References were drafted from the prompts given in each round (the uncertain ones are listed in `recordings/REFERENCES.md` for correction). One clip holds only the word "Look" (2.1 s; every recogniser missed the rest), so it was probably cut short by the live recogniser's end-of-speech detection; it is excluded from the second column.

| Recogniser | Word error rate, all 15 | Without the cut clip | Compute per audio second | Delay after end of speech |
|------------|------------------------:|---------------------:|-------------------------:|--------------------------|
| sherpa-onnx streaming zipformer (2023, today's) | 40.3 % | 36.5 % | 0.07 s | streams; final at the endpoint |
| NVIDIA Nemotron speech streaming 0.6B int8, 560 ms chunks (2026) | 11.9 % | 6.3 % | 0.31 s | streams; final about 0.2 s later than the zipformer (max 1 s) |
| faster-whisper base | 16.4 % | 12.7 % | 0.19 s | per utterance, about 0.5 s after the endpoint |
| faster-whisper small | 6.0 % | 0.0 % | 0.42 s | per utterance, about 1.4 s after the endpoint |

The zipformer's mistakes are exactly the ones that derailed the spoken rounds ("Hydropper", "Looked here left", "What is to day state", "Blow numbernorium" for "Hello Pepper how are you"). Nemotron fixes almost all of them while staying a streaming recogniser with its own endpointing and partial results, at a small latency cost; Whisper small is the most accurate but adds about 1.4 s to every turn and would need the energy endpointer, which is weak in a noisy room. **Nemotron is now the default** (`STT_MODEL` in `.env`; 633 MB in `~/.local/share/pepper-models/`, loads in 2.6 s). Whisper small stays an option as a second, more accurate pass on the final transcript if Nemotron's errors matter in practice. Next live check: whether the "Look" clip was the person pausing or the recogniser cutting off early, and whether 1.0 s of end-of-speech silence is right with Nemotron.

### Live check with Nemotron (2026-09-23, the office, Sonnet 5 at medium effort)

The same five spoken prompts through Pepper's microphone, office background level slightly higher than the open room (median microphone level about 610 against 550). Nemotron heard all five word for word ("Hello Pepper, how are you", "Look to your left", "What can you see in front of you", "Be quiet", "What is the date today"), where the zipformer had misheard three of five in each earlier round. First sound 2.4-3.0 s after the transcript; real words first on the greeting and the date question, a filler first on the two turns that needed a head move or photo (first words 3.9 and 5.4 s). The date question is now answered correctly. The recordings are in `recordings/round4-nemotron-office` with references.

## Milestone 3: vision grounding

Goal: Claude can act on what it sees, not just describe it.

- Sharp photos (**done 2026-10-01**, verified on the robot; a paused face tracker also had to be held off): `/move/head` waits for the head to stop, `/picture` pauses face tracking and waits for a still head and drops the first frame, and the host retakes a blurry photo once and tells the model if it is still blurry. Photos taken right after a head move scored 76-200 on the sharpness measure, still ones 290-1250.
- `look_at(x, y)`: the model returns a point in the last photo; the bridge converts it to head angles using the camera field of view.
- Verify-after-act: for moves and gestures with a visible effect, take a photo and let the model judge success before continuing.
- Optional depth frames from the 3D camera for distance questions.
- **Handshake** (built 2026-10-08): Pepper holds its right hand out (`/pose/offer_hand`), notices a firm handshake by the shoulder motor's current dropping as a hand takes the arm's weight, a real shake (4° or more) or the back-of-hand touch sensor, speaks without body language while the hand is out, and lowers it after 15 s if nobody takes it. A gentle grip is not detectable from the arm (lower priority, issue #23: judge it with the camera).
- **Follow me** (asked for on the robot 2026-10-01: "can you follow me back into my office?"; Pepper had no way to). Candidates: NAOqi's `ALTracker` in `Move` mode (follows a face or person with the base, keeping a set distance), or the host steering short base moves from the world model's direction and distance. Safety first: slow speed (at most 0.3 m/s), a minimum distance of about 0.8 m, the sonar guard and NAOqi collision avoidance stay on, stop at once on "stop", on touching the head, when the person is lost for more than a couple of seconds, or after a time limit; only on explicit request, never on its own. Test in the open room first.

- **Pointing and look_at** (#28, #6): built in 0.7.0. `point_at` aims the arm with `ALTracker.pointAt` at a spot in the last photo, the person, or a direction (within 2-6° on the virtual Pepper); `look_at` turns the head to a spot in the last photo. Robot test next.

Acceptance: "look at the person on the left", "is the door open?", "go towards the chair", "come to me" and "follow me" work reliably. "Come to me" uses the person's direction in the "Around you" line since 2026-10-01 (turn by that angle, then drive). On the robot (2026-10-08) it got there only after several tries, because the detector keeps losing people. Since 0.6.0 and 0.7.0 three things fill in when the detector has lost someone: remembered positions (#27), the voice's direction (#12), and a camera fallback that finds the person in the frame (#24). Robot test next.

## Milestone 4: world model (continuous perception)

Goal: Pepper knows what is around it between turns, notices when someone arrives or leaves, and can answer "who's here?" without taking a photo. Status 2026-10-08:
- **Built and verified on the robot:** the "now" part (layer 1 below, plus camera judgements about once a second while someone is in view).
- **Built offline in 0.6.0, to be tested on the robot:** memory (`docs/MEMORY.md`, issue #29). One structure (observations in, views out) holds Pepper's pose and remembered people (#27), voice direction (#12, #24), scene notes (#11) and the `recall` detail tool (#12).
- **Still open:** the reflection loop, and identity (Milestone 5).

Two layers feeding one host-side world model:

1. **Always-on, on the robot, free.** NAOqi's own people perception (count and IDs, already polled by the bridge), face detection, engagement zones (distance bands), gaze analysis (is the person looking at Pepper), sound localisation. **Done 2026-09-23:** the bridge debounces people events (a change of count, zone or gaze must hold for 1 s) and reports distance, gaze, zone and time present per person; the host keeps them in `src/world/model.py` (`WorldModel`) and adds an "Around you" sentence to the model's state on every turn, with recent arrivals and departures. Live check: over a minute of someone leaving, returning and stepping back, three events instead of several a second; sample line "Around you: one person, about 1.1 m away, looking at you. Someone arrived 10 seconds ago." **Greeting newcomers done 2026-09-29** (tested on the robot in the open room): the world model raises an arrival when someone appears after nobody was in view for 20 s; Pepper turns its head to them at once (a reflex, no model) and greets them if they are within 3 m and looking at it (1.8 m while gaze is unknown), nobody spoke to Pepper in the last 30 s and the last greeting was over 90 s ago. Final run: picked up at 2.9 m, head turned the same instant, speech about 2 s later; a look-away, a return inside the cooldown and a passer-by at 2.3 m correctly got no greeting. Lessons from the session: NAOqi's detector only sees faces from about 3 m and not side-on (an office desk at 1.3 m was never seen as looking); its reports change only with count, zone or gaze, not distance; NAOqi face tracking (`ALBasicAwareness`) follows well up close but parks the head looking down when it loses someone, so the host now switches it off for an empty room and looks out at 18° up; tuning `ALPeoplePerception` (fast mode off, movement detection, VGA faces) stopped detection entirely and was reverted. Longer-range detection is for layer 2 below (a person detector on the host). Still to do in this layer: sound localisation for people who are not facing Pepper, and greeting a second person who joins.
2. **Periodic scene understanding, on the host.** A background task grabs a frame every 10-20 s, and at once when layer 1 reports a change, and asks a fast vision model for a short structured description (people and rough positions, what they are doing, notable objects, changes since last time). A VGA photo costs about 0.6 s through the bridge; continuous video would load the robot's Atom CPU and cost per frame, so this stays event-driven and low-rate. The vision model is chosen with the same method as the spoken-turn comparison.

The world model (host, in memory): people with first-seen / last-seen, zone and engagement; the latest scene summary with its timestamp; a short log of recent events. It is read in two places:

- a compact "around you" line in the dynamic system block on every turn, so the model knows who is present and that someone has just joined a conversation;
- arrival and departure events routed through the same rate-limited reaction path as touches, so Pepper can greet a newcomer without interrupting a running reply.

Constraints: frames are kept in memory only, never written to disk, and Pepper shows that it is looking (for example an eye or chest LED state) while the camera is in use; the scene summaries in the world model are text. Knowing *who* someone is (face or voice identity) is Milestone 5 and builds on this.

Situation judgements (tested 2026-10-03, issue #20): local decision models answer fixed questions before the mind is called. Setup: a private Ollama 0.35.1 on Alien3 (`http://alien3:11435`, the shared service on :11434 is too old and needs root to upgrade), models `nimble` and `clef-flash`. Results on Pepper's own data: "is this addressed to Pepper?" right for 21 of 22 utterances from the 2026-10-01 session with two visitors (Nimble 0.05 s, Clef Flash 0.09 s; a name-matching rule 9 of 22; the miss was "Emer, can you come to me" with the name misheard); four questions per camera frame in 0.25 s (person 38/40, the two misses were people shown on a TV screen; facing 9/9; blur 39/40; scene 24/30, mostly ambiguous labels). Command routing (issue #21): Nimble picking the first physical action from a fixed list of 17 plus "talk" got 26 of 28 direct commands right at a 0.7 threshold, with no wrong actions and no action on a question or conversation, in about 0.15 s; Clef Flash was weaker on text alone. Built in 0.5 and tested on the robot 2026-10-08 (`docs/HANDOFF.md`, "Robot session 2026-10-08"): the addressee gate, the command router (Milestone 1) and a camera frame stream judged by Clef Flash about once a second while someone is in view (a wave gets a reply about 3.6 s later; the stream costs the robot 5-22 % of a core).

Compute for this milestone (2026-09-23): the Creative AI Hub at TMU (RCC230) has an always-on AI server (hal-9000: 2 x RTX 6000 Ada, 48 GB each, 128 GB RAM, 10 GbE, Ubuntu, Ollama already serving local models) and five workstations (alien1-5: RTX 5090 32 GB, Core Ultra 9, 64 GB; one on Ubuntu 24.04, being moved to Ubuntu). Plan: run the periodic vision pass as a service on the server (through its Ollama API or a vLLM server), reached from the host over the TRiPL tailnet; only the host needs to reach it, not Pepper. A 32 GB workstation is enough for fine-tuning jobs (below) and experiments. Before relying on it: agree usage with the Hub (shared machines), check that the host reaches it over the tailnet with low latency, and keep recordings of people in a private account on it. Blackwell cards (RTX 5090) need CUDA 12.8 or newer builds of PyTorch and onnxruntime.

Also for these machines, once there are a few hours of corrected recordings: fine-tune Nemotron or Whisper on the lab's acoustics and voices, and rerun `scripts/compare_stt.py` with the large Whisper models. The Digital Research Alliance cluster remains an option for larger batch jobs, but it cannot host the live service.

Acceptance: someone walks up and Pepper greets them within a few seconds, unprompted; "who's here?" and "what's on the desk?" are answered from the world model without a new photo when the last look is recent; no frame is stored.

## Milestone 5: memory and people

Goal: Pepper remembers who it talked to and what was said.

- **Memory tools: built in 0.6.0** (#13, `docs/MEMORY.md`).
  - `remember_person`, `remember`, `recall` and `forget_person` over a local SQLite store.
  - People only with consent, which code checks against what Pepper heard; facts, and session episodes.
  - 90-day expiry; "forget me" at once; `scripts/memory_admin.py`.
  - Robot test next session.
- **Face or voice identity (#14), opt-in only, after the consent flow has been used on the robot.** Candidates from a survey of recent models (see `docs/MEMORY.md`):
  - InsightFace 2.1 for faces and body appearance (its pretrained models are for non-commercial research);
  - sherpa-onnx speaker embeddings for voices.
  NAOqi's people IDs are session-local.
- Keep the tool count small; multi-step task completion on social robots is still fragile in the literature.

## Milestone 6: robustness and operations

- Bridge autostart on robot boot: **done**, a NAOqi package with an `autorun` service (`deploy.py --install-autostart`); verified on the robot after a reboot (2026-10-01) and a cold power-on (2026-10-08). Watchdog (#15): built in 0.7.0. `watchdog.sh` starts the bridge and starts it again if it exits or stops answering; tested on the desktop NAOqi.
- Latency budget per turn in the log; per-sentence speech timing.
- **Session recording and review (issue #16): done in 0.5.** Every robot session is recorded and reviewed, because real interactions are the main source of fixes. `SESSION_DIR` gives one folder per run with the host and bridge logs, utterance audio, photos with the measured head angle, `turns.jsonl` (per turn: end of speech, the judgements, the router's action and timing, tools, first word, what Pepper said) and `events.jsonl` (people, greetings, camera events, handshakes). `scripts/review_session.py` prints a timed transcript with flags (slow first words, long replies, unanswered speech, failed tools). Records stay local and git-ignored (`CLAUDE.md`, "Session records").

## Testing without the robot

Three levels, from cheapest to most faithful:

1. **`PEPPER_FAKE_BRIDGE=true`** (host only): the whole host stack against an in-memory robot. Checks the model, tools, speech streaming, voice input with `STT_BACKEND=fake`, and the UI.
2. **`tests/fakenaoqi`** (bridge included): the real bridge process under a fake `qi` module that answers every NAOqi call with plausible data, toggles a touch sensor and pumps microphone frames. This is what the test suite runs, also under Python 2.7 + Tornado 3.1.1. It proves the bridge's threading, HTTP and WebSocket behaviour, not that the NAOqi calls exist.
3. **A virtual Pepper on NAOqi's own desktop binary** (`scripts/virtual_pepper.sh`, done 2026-09-11). Choregraphe 2.5 ships `naoqi-bin`, the same NAOqi that runs on the robot, compiled for Linux x86-64; it runs headless and Pepper is selected by editing `etc/naoqi/ALRobotModel.xml` to `JULIETTEY20MP.xml`. The bridge runs against it under Python 2.7 with the `pynaoqi` SDK (a shared, UCS4 Python 2.7; mise's 2.7.18 qualifies). The original archives are still served from `community-static.aldebaran.com` (Choregraphe suite; the Python SDK only from GitHub mirrors). `tests/test_virtual_naoqi.py` (opt-in with `PEPPER_VIRTUAL_BRIDGE`) exercises every endpoint against it. Verified there: every method name and signature the bridge calls on `ALMotion` (including `moveTo` with a velocity config), `ALRobotPosture`, `ALTextToSpeech` and its `Status` events, `ALAnimatedSpeech` with the body-language config, `ALBasicAwareness` modes and stimuli, `ALAutonomousLife` transitions, `ALLeds`, `ALMemory` subscribers, `ALVideoDevice`, `qi` service registration, plus the refusals while resting or halted and the emergency-stop/wake-up sequence. Found and fixed there: a raw sonar value of 0.0 blocked every move; a stimulus the build lacks (`Sound`) failed the whole awareness call; awareness does not pause itself for raw head moves. Cannot verify: sonar, touch and battery values, camera content, `ALAudioDevice` (microphone stream), `ALTabletService`, the animation package, timing, the room.

qiBullet (PyBullet with a Pepper model) and the ROS 2 / Gazebo Pepper packages simulate physics with their own APIs, not NAOqi's, so they would not exercise the bridge. Webots has no Pepper model.

## Watch list (no action now)

- Anthropic's Model Hardware Standard (research preview, August 2026): a way to describe a robot as tools an agent can discover. The bridge could publish a descriptor once the spec is open.
- Embodied-reasoning APIs (Gemini Robotics-ER 2) as a second opinion for pointing and spatial questions, if Claude's grounding proves insufficient.
- "System One" decision models: **moved out of the watch list on 2026-10-03** (see Milestone 4 and `docs/ARCHITECTURE.md`, "Situation judgements"). Ollama now serves them locally with image support (Nimble, Clef Flash, Clef; `/v1/systemone`, the TypeSafe Jev API), which answers the earlier objection that they could not see. Earlier notes: TypeSafe AI's Jev (hosted, early access since 2026-09-15) and open reproductions (Von, Laya, https://github.com/rupeshpoojary9/awesome-open-system-one); the intent-paraphrase and end-of-turn ideas below still apply.
- Navigation models that emit waypoints from RGB (the most embodiment-agnostic action space in the survey); only relevant if the lab wants longer autonomous drives than `ALNavigation` handles.

## Non-goals

- Running a vision-language-action model to drive Pepper's joints. Wrong action space, no Pepper data, no on-robot GPU, nothing to manipulate. See the research notes.
- On-robot inference of any kind. The Atom CPU runs the bridge and nothing else.
- Migrating to NAOqi 2.9 / QiSDK. 2.5 gives raw audio, camera and joint access that the language-model stack needs; 2.9 hides them behind Kotlin APIs.
- Generating motion trajectories with the model. Gestures stay discrete intents mapped to the animation library.
