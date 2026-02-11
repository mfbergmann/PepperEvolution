# Pepper Bridge API Reference

The bridge server runs on the Pepper robot (Python 2.7 + Tornado) and exposes NAOqi services as HTTP/WebSocket endpoints.

**Default:** `http://<PEPPER_IP>:8888`

## Authentication

Optional. Set `--api-key=SECRET` when starting the bridge. Clients must send `X-API-Key: SECRET` header.

---

## REST Endpoints

### Health & Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Bridge health check |
| GET | `/status` | Battery, posture, robot name, NAOqi version, autonomous life state |

### Speech

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/speak` | `{"text": "Hello", "language": "en", "animated": false}` | Text-to-speech |
| POST | `/volume` | `{"level": 50}` | Set TTS volume (0-100) |

### Movement

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/move/forward` | `{"distance": 0.5, "speed": 0.3}` | Move forward/backward (-2 to 2m) |
| POST | `/move/turn` | `{"angle": 90}` | Turn (-180 to 180 degrees) |
| POST | `/move/head` | `{"yaw": 0, "pitch": 0, "speed": 0.2}` | Move head (degrees) |
| POST | `/move/to` | `{"x": 0.5, "y": 0, "theta": 0}` | Move to position |
| POST | `/stop` | | Stop movement |
| POST | `/emergency_stop` | | Stop all + disable motors |

### Posture & Stiffness

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/posture` | `{"posture": "Stand", "speed": 0.5}` | Set posture (Stand, Crouch, StandInit, StandZero) |
| POST | `/wake_up` | | Enable motors |
| POST | `/rest` | | Disable motors (rest position) |

### Camera

| Method | Path | Params | Description |
|--------|------|--------|-------------|
| GET | `/picture` | `?camera=0&resolution=2` | Take photo, returns base64 JPEG |

### Sensors

| Method | Path | Description |
|--------|------|-------------|
| GET | `/sensors` | Battery, touch sensors, sonar distances, people count |

### LEDs

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/leds/eyes` | `{"color": "blue"}` or `{"r": 0, "g": 0, "b": 1}` | Set eye LED color |
| POST | `/leds/chest` | `{"color": "green"}` or `{"r": 0, "g": 1, "b": 0}` | Set chest LED color |

### Animation

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/animation` | `{"name": "animations/Stand/Gestures/Hey_1"}` | Play gesture animation |

### Awareness & Autonomous Life

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/awareness` | `{"enabled": true}` | Toggle basic awareness |
| POST | `/autonomous_life` | `{"state": "solitary"}` | Set autonomous life state |

### Audio

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/audio/record` | `{"duration": 3.0}` | Record audio, returns base64 WAV |

---

## WebSocket Events

**Endpoint:** `ws://<PEPPER_IP>:8888/ws/events`

Authentication via query param: `?api_key=SECRET`

### Events pushed by the bridge

```json
{"type": "touch", "data": {"head_front": true, ...}, "timestamp": 1234567890.0}
{"type": "sonar", "data": {"left": 0.3, "right": 0.5, "obstacle": true}, "timestamp": ...}
{"type": "battery", "data": {"level": 75}, "timestamp": ...}
{"type": "people", "data": {"count": 2, "ids": [1, 2]}, "timestamp": ...}
```

### Client messages

```json
{"type": "ping"}  -->  {"type": "pong", "timestamp": ...}
```

---

## Response Format

All REST endpoints return JSON:

```json
{"ok": true, ...}           // Success
{"ok": false, "error": "message"}  // Failure
```

## Common Animations

| Animation | Path |
|-----------|------|
| Wave | `animations/Stand/Gestures/Hey_1` |
| Nod | `animations/Stand/Gestures/Enthusiastic_4` |
| Think | `animations/Stand/Gestures/Thinking_1` |
| Explain | `animations/Stand/Gestures/Explain_1` |
| Show tablet | `animations/Stand/Gestures/ShowTablet_1` |
