# Pepper Bridge API Reference (v2.2)

The bridge server runs on the Pepper robot (Python 2.7 + Tornado 3.1.1) and exposes NAOqi 2.5 services as JSON-over-HTTP endpoints plus a WebSocket event stream.

**Default:** `http://<PEPPER_IP>:8888`

Every NAOqi call runs on a worker thread, so the server never blocks: `/stop`, `/emergency_stop` and `/sensors` answer immediately even while the robot is speaking or driving. Blocking actions (speech, moves, postures, animations) return when the robot has finished.

## Authentication

Optional. Start the bridge with `--api-key=SECRET`; clients must then send `X-API-Key: SECRET` (or `?api_key=SECRET`). The WebSocket accepts `?api_key=SECRET`. `/tablet/page` and `/tablet/state` are unauthenticated because the tablet's browser loads them.

## Response format

```json
{"ok": true, ...}                    // success, plus endpoint-specific fields
{"ok": false, "error": "message"}    // failure (HTTP 400 for bad input, 500 for NAOqi errors, 401 for auth)
```

---

## REST endpoints

### Health & status

| Method | Path | Returns |
|--------|------|---------|
| GET | `/health` | `bridge`, `version`, `naoqi` (system version), `robot_name`, `naoqi_connected`, `uptime`, `audio` (microphone stream state, see `/audio/stream`). While NAOqi is still booting the bridge answers **503** `{"ok": false, "error": "naoqi_connecting"}` (the server listens before NAOqi is up, so a deploy can watch it come alive). |
| GET | `/status` | `battery` (%), `charging`, `posture`, `posture_family`, `robot_name`, `naoqi_version`, `autonomous_life`, `awake`, `halted` (after an emergency stop), `language`, `volume`, `awareness` |
| GET | `/sensors` | see below |

`/sensors`:
```json
{"ok": true, "battery": 81, "charging": false,
 "touch": {"head_front": false, "head_middle": false, "head_rear": false, "hand_left": false, "hand_right": false},
 "bumpers": {"front_left": false, "front_right": false, "back": false},
 "sonar": {"front": 1.23, "back": 2.01}, "obstacle": false,
 "people_count": 1, "people_ids": [7], "sonar_ok": true, "people_ok": true, "timestamp": 1757000000.0}
```
Sonar values are metres from Pepper's front/back ultrasonic sensors (`Device/SubDeviceList/Platform/{Front,Back}/Sonar/Sensor/Value`). `obstacle` is true when either is under 0.45 m. The bridge subscribes to `ALSonar` and `ALPeoplePerception` itself (they only publish while subscribed, and Autonomous Life stops them when disabled); `sonar_ok`/`people_ok` report whether those subscriptions are in place, and they are renewed on every NAOqi reconnect.

### Preparing the robot

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/prepare` | `{"autonomous_life": "disabled", "wake_up": true, "posture": "Stand", "awareness": true}` | Put the robot in a known state for external control. All fields optional; `autonomous_life: ""` leaves it alone. `awareness: true` turns on head-only people tracking with the `People`, `Sound` and `Touch` stimuli. Every step is attempted; failures are listed in `errors`. |
| POST | `/wake_up` | | `ALMotion.wakeUp()` (motors on, StandInit); also clears the `halted` flag set by `/emergency_stop` |
| POST | `/rest` | | `ALMotion.rest()` (safe posture, motors off) |
| POST | `/autonomous_life` | `{"state": "disabled"}` | `solitary`, `interactive`, `safeguard`, `disabled` |
| POST | `/awareness` | `{"enabled": true, "tracking_mode": "Head", "engagement_mode": "SemiEngaged", "stimuli": ["People", "Sound", "Touch"]}` | ALBasicAwareness on/off. Optional: `tracking_mode` `Head` (recommended; the others rotate or drive the base) / `BodyRotation` / `WholeBody` / `MoveContextually`; `engagement_mode` `Unengaged` / `SemiEngaged` / `FullyEngaged`; `stimuli` from `People`, `Touch`, `TabletTouch`, `Sound`, `Movement`, `NavigationMotion` (a list, or a comma-separated string in the query). Awareness pauses by itself while `/move/head` uses the head and resumes afterwards. |

### Speech

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/speak` | `{"text": "Hello", "language": "French", "animated": true, "wait": true, "body_language": "contextual"}` | Text-to-speech. `language` accepts NAOqi names or ISO codes (`fr`) and applies to this utterance only (the previous language is restored afterwards). `animated` uses ALAnimatedSpeech (supports `^start(animations/...)` tags). `wait: false` returns immediately. Returns `spoken`, `duration`, `animated`, `language`. |
| POST | `/speak/stop` | | Stop current speech |
| POST | `/volume` | `{"level": 50}` | TTS volume 0–100 |

### Movement

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/move/forward` | `{"distance": 0.5, "speed": 0.3, "force": false}` | Drive forward/backward (−2 to 2 m). `speed` is m/s (0.1–0.55, sets `MaxVelXY`). Refused (HTTP 500, `not moving: front sonar shows an obstacle at 0.31 m`) when the sonar in the direction of travel reads under 0.45 m, unless `force` is true. Refused while the robot is resting or halted (wake it first; motion never wakes the robot implicitly). `completed` is false when collision avoidance or `/stop` cut the move short. |
| POST | `/move/turn` | `{"angle": 90}` | Turn in place (−180 to 180°, positive = left) |
| POST | `/move/head` | `{"yaw": 0, "pitch": 0, "speed": 0.2}` | Head angles in degrees (yaw ±119.5, pitch −40.5..25.5 straight ahead, narrower when turned), non-blocking |
| POST | `/move/to` | `{"x": 0.5, "y": 0, "theta": 0, "speed": 0.3, "force": false}` | Move to a relative pose (theta in degrees); same sonar and awake checks as `/move/forward` |
| POST | `/stop` | | Stops any running animation (`ALBehaviorManager.stopAllBehaviors`) and the base (`stopMove()` + `killMove()`) |
| POST | `/emergency_stop` | | Stops all behaviours (animations, animated speech gestures) and speech, kills all motion tasks, then `rest()` (Pepper does not allow manual body stiffness control). Sets `halted`: every motion/animation call is refused until `/wake_up` or `/prepare`. |
| POST | `/posture` | `{"posture": "Stand", "speed": 0.5}` | `Stand`, `StandInit`, `StandZero`, `Crouch` |

### Camera & audio

| Method | Path | Params/Body | Description |
|--------|------|-------------|-------------|
| GET | `/picture` | `?camera=0&resolution=2` | Snapshot. camera 0 = forehead, 1 = mouth; resolution 0=QQVGA 1=QVGA 2=VGA 3=4VGA. Returns `image` (base64), `width`, `height`, `format` (`jpeg` when PIL is on the robot, else raw `rgb`). |
| POST | `/audio/record` | `{"duration": 3.0}` | Record from the front microphone; returns base64 16 kHz mono WAV |
| GET | `/audio/stream` | | State of the live microphone stream (see `/ws/audio`): `streaming`, `clients`, `muted`, `frames`, `dropped`, `sample_rate`, `last_frame_age`. Also included in `/health` as `audio`. |

### LEDs & animations

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/leds/eyes` | `{"color": "blue"}` or `{"r": 0, "g": 0, "b": 1, "duration": 0.5}` | Eye LEDs. Colours: red, green, blue, yellow, purple, magenta, cyan, white, orange, pink, off |
| POST | `/leds/chest` | same | Chest LED |
| POST | `/animation` | `{"name": "animations/Stand/Gestures/Hey_1"}` | Play an installed animation (blocking) |
| GET | `/animations` | | List installed `animations/...` behaviours |

### Tablet

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/tablet/text` | `{"text": "Hello", "title": "Pepper"}` | Show text. The bridge serves a page at `/tablet/page` that the tablet loads once and then polls `/tablet/state`, so later calls update instantly. |
| POST | `/tablet/web` | `{"url": "https://..."}` | Show a web page |
| POST | `/tablet/image` | `{"url": "https://..."}` | Show an image |
| POST | `/tablet/hide` | | Hide web view / image |

The tablet reaches the robot head at `198.18.0.1`; change with `--tablet-host` if your robot differs.

---

## WebSocket events

**Endpoint:** `ws://<PEPPER_IP>:8888/ws/events` (`?api_key=SECRET` if configured)

On connect the bridge sends `hello` and a `sensors` snapshot. Afterwards events are edge-triggered:

```json
{"type": "hello",   "data": {"version": "2.2.0"}, "timestamp": ...}
{"type": "sensors", "data": {...same as GET /sensors...}, "timestamp": ...}
{"type": "touch",   "data": {"sensor": "head_front", "touched": true}, "timestamp": ...}
{"type": "bumper",  "data": {"sensor": "front_left", "pressed": true}, "timestamp": ...}
{"type": "sonar",   "data": {"front": 0.31, "back": 1.9, "obstacle": true}, "timestamp": ...}
{"type": "battery", "data": {"level": 75, "charging": false}, "timestamp": ...}
{"type": "people",  "data": {"count": 2, "ids": [1, 2]}, "timestamp": ...}
{"type": "speech",  "data": {"state": "start", "text": "Hello"}, "timestamp": ...}
```

Clients may send `{"type": "ping"}` and get `{"type": "pong"}`. Sensors are polled every 250 ms.

## WebSocket microphone stream

**Endpoint:** `ws://<PEPPER_IP>:8888/ws/audio` (`?api_key=SECRET` if configured)

The bridge registers a small qi service (`PepperBridgeAudio`) with NAOqi and subscribes it to `ALAudioDevice` (front microphone, 16 kHz, mono) while at least one client is connected; the subscription is dropped when the last client leaves, so an idle bridge costs the robot nothing.

On connect the client gets one JSON text frame, then binary frames:

```json
{"type": "hello", "version": "2.2.0", "sample_rate": 16000, "channels": 1, "format": "pcm_s16le"}
{"type": "state", "streaming": true}
<binary> 5460 bytes = 2730 samples of signed 16-bit little-endian PCM (about 170 ms), repeated
```

Frames are **not sent while the robot is speaking** (Pepper has no echo cancellation): the bridge mutes capture around every `/speak`, on `ALTextToSpeech/Status` events from any other source, and for 0.4 s after speech ends. `{"type": "error", "error": "..."}` reports a failed subscription (for example NAOqi not connected yet).

Clients may send `{"type": "ping"}` (answered with `pong`) and `{"type": "mute", "muted": true|false}` to pause the stream from the host side; the reply is `{"type": "state", "streaming": ..., "muted": ...}`.

The host side is `src/pepper/audio_stream.py` (`AudioStream`), consumed by `src/audio/voice.py`.

---

## Running the bridge

```
python pepper_bridge.py [--port=8888] [--api-key=SECRET] [--naoqi=tcp://127.0.0.1:9559]
                        [--tablet-host=198.18.0.1] [--log-level=INFO]
```

`robot_bridge/deploy.py` copies the file to `/home/nao/pepper_bridge/`, starts it with `nohup`, and polls `/health` until NAOqi is connected. It also has `--restart`, `--stop`, `--status` and `--logs`.

The bridge starts listening immediately and connects to NAOqi in the background, retrying for about two minutes after a robot boot (`/health` answers 503 meanwhile). If the session drops later it reconnects lazily and re-subscribes to the sensor extractors. On SIGTERM/SIGINT (for example a redeploy) it stops any running motion, animation and speech before exiting.

## Common animations (Pepper, NAOqi 2.5)

| Animation | Path |
|-----------|------|
| Wave | `animations/Stand/Gestures/Hey_1` |
| Bow | `animations/Stand/Gestures/BowShort_1` |
| Nod yes / shake no | `animations/Stand/Gestures/Yes_1` / `No_1` |
| Think | `animations/Stand/Gestures/Thinking_1` |
| Explain | `animations/Stand/Gestures/Explain_1` |
| Enthusiastic | `animations/Stand/Gestures/Enthusiastic_4` |
| Show tablet | `animations/Stand/Gestures/ShowTablet_1` |
| Happy | `animations/Stand/Emotions/Positive/Happy_1` |

Use `GET /animations` for the definitive list on your robot.
