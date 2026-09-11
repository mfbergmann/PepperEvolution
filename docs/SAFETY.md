# Safety ledger

Every numeric bound and every safety behaviour in the system, in one place, with where it lives. Change the code and this table together. The pattern is borrowed from Autonomous OS's `SAFETY.md`; the values are Pepper's.

Pepper is 1.2 m tall, 28 kg, and drives on an omnidirectional base at up to 0.55 m/s. It has no arms strong enough to hurt anyone, but it can roll into things and people, and its head and arms can pinch. The bounds below are chosen for an office with furniture and bystanders.

## Motion (bridge, `robot_bridge/pepper_bridge.py`)

| What | Bound | Where | Why |
|------|-------|-------|-----|
| Obstacle guard | refuse `/move/forward`, `/move/to` when the sonar in the direction of travel reads under **0.45 m** (`force: true` overrides for direct commands only) | `OBSTACLE_DISTANCE`, `Robot._check_obstacle` | Pepper's sonars see 0.25 to 2.5 m; 0.45 m leaves stopping room at 0.3 m/s |
| Drive distance | **-2.0 to 2.0 m** per call | `Robot.move_forward` | one call never crosses a room |
| Drive speed | **0.1 to 0.55 m/s** (`MAX_VEL_XY`); default 0.3 | `Robot.move_forward`, `move_to` | 0.55 is NAOqi's own maximum |
| Turn | **-180 to 180°** per call; angular speed capped at 2.0 rad/s (`MAX_VEL_THETA`) | `Robot.turn` | |
| `move_to` target | x, y in **-3 to 3 m**, theta -180 to 180° | `Robot.move_to` | |
| Head yaw | **±119.5°** | `HEAD_YAW_LIMIT_DEG` | joint limit |
| Head pitch | **-40.5 to 25.5°** straight ahead, narrowing to -35.0 to 13.5° at full yaw (`HEAD_PITCH_LIMITS`) | `head_pitch_limits()` | Pepper's yaw-coupled pitch envelope; outside it the joint stalls |
| Head speed | **0.05 to 0.6** fraction of max | `Robot.move_head` | fast head moves look aggressive |
| Posture speed | **0.1 to 1.0** | `Robot.set_posture` | |
| NAOqi collision protection | never disabled | (not touched) | NAOqi's own arm/base reflexes stay on; `completed: false` reports a cut-short move |
| Motion while resting or halted | refused with a clear error; the bridge never wakes the robot implicitly | `Robot._ensure_awake` | a robot that stands up by itself surprises people |
| Awareness tracking | off unless `PEPPER_AWARENESS=true` or "look at me"; then **Head** tracking only. `BodyRotation`/`MoveContextually` (which rotate or drive the base) must be asked for explicitly | `Robot.prepare`, `set_awareness` | the base must not move because someone walked past |

## Stopping (bridge)

| What | Behaviour | Where |
|------|-----------|-------|
| `/emergency_stop` | `stopAllBehaviors` + `ALTextToSpeech.stopAll` + `killMove`/`killAll` + `rest()`; sets `halted` so every motion call is refused until `/wake_up` or `/prepare` | `Robot.emergency_stop`, `_ensure_awake` |
| `/stop` | stops the base and running animations, keeps motors on | `Robot.stop` |
| Never blocked | every NAOqi call runs on a worker thread; `/emergency_stop` and `/sensors` answer while the robot talks or drives | `JSONHandler.run_in_thread` |
| Bridge exit | SIGTERM/SIGINT (a redeploy, a crash of the parent) halts the robot before the process disappears | `_on_signal` |
| Setup | `/prepare` puts Autonomous Life to `disabled` so NAOqi's own behaviours do not fight external control | `Robot.prepare` |

## Speech and hearing (bridge)

| What | Bound | Where |
|------|-------|-------|
| Volume | **0 to 100** | `Robot.set_volume` |
| Microphone stream | 16 kHz mono, front microphone, only while a `/ws/audio` client is connected; a client more than **30** frames (about 5 s) behind is disconnected so the robot never buffers audio without bound | `AudioTap`, `AudioWebSocket.broadcast` |
| Self-hearing | capture muted during every `/speak`, on `ALTextToSpeech/Status` events, and for **0.4 s** after speech ends (`AUDIO_MUTE_TAIL`) | `AudioTap.muted` |
| Audio recording | **0.5 to 15 s** per `/audio/record` | `Robot.record_audio` |
| Tablet | text and URLs only; the tablet page is served by the bridge on the robot's own network | `TabletHandlers` |

## Host (`src/`)

| What | Bound | Where | Why |
|------|-------|-------|-----|
| Tool rounds per turn | **10** (`MAX_TOOL_ROUNDS`) | `AIManager._run_turn` | a confused model cannot loop forever |
| Halt propagation | when the robot is halted, remaining tool calls get a failure result and the turn ends | `AIManager`, `ToolExecutor.halted_outcome` | |
| "Stop" by voice or text | matched locally in under a millisecond, before the turn lock; stops speech and motion, cancels the model call in flight and the rest of the turn, and drops turns that were queued behind it | `src/ai/intents.py`, `AIManager._handle_intent` | must not wait for a model |
| Intent phrases | only exact short phrases (≤ **40** characters after normalisation); "stop by the kitchen" is not a stop | `match_intent` | false stops are annoying, false positives on "wait" are worse |
| Emergency stop by voice | only "emergency stop" / "e-stop" / "kill the motors" → full `/emergency_stop` (motors off); a bare "halt" is an ordinary stop | `intents.py` | |
| Touch reactions | at most one per **8 s**, none while a turn runs | `AIManager.handle_event` | |
| Backchannel filler | after **2 s** without model text, once per turn, never on event turns | `AIManager._backchannel` | |
| Truncated replies | a reply cut off by `max_tokens` never has its tool calls executed | `AIManager._run_turn` | half a tool call is not a tool call |
| Phantom tool calls | XML that looks like a tool call is never spoken; retried once | `SpeechStreamer`, `AIManager` | |
| Push-to-talk | utterances under **100 ms** rejected, over **60 s** rejected; sample rate 8 to 48 kHz, resampled to 16 kHz off the event loop | `POST /voice/utterance` | |
| Voice transcripts | finals shorter than **2** characters or without a letter or digit are ignored | `VoiceInput._deliver` | noise never becomes a command |
| Speech model | recognition runs on a worker thread, never on the event loop | `WhisperTranscriber`, `SherpaTranscriber` | the loop must stay free for `/emergency_stop` |
| Shutdown | Ctrl-C shuts the API down, then the robot connection; `REST_ON_EXIT=true` rests the robot | `main.py` | |

## What the model is allowed to do

The model only sees the tools in `src/ai/tools.py`: speak, animations from a fixed list, head moves, turns, drives, posture, eye colour, photo, sensors, tablet, emergency stop. It cannot change speeds beyond the bounds above, cannot disable collision protection, cannot touch Autonomous Life or awareness modes, and cannot run arbitrary NAOqi calls. Anything it needs beyond that is a code change and a new row in this table.

## Not yet verified on the physical robot

Every number above was chosen from the NAOqi 2.5 documentation and tested against a fake NAOqi. Milestone 0 in [ROADMAP.md](ROADMAP.md) walks through checking them on the robot: the obstacle threshold, the head envelope, speech volume and the microphone mute tail are the ones most likely to need tuning.
