# Pepper Bridge v2

Cloud-based AI control system for Pepper robots that offloads the robot's "brain" to the cloud.

## Architecture

```
┌─────────────────────┐         ┌──────────────────────┐
│   Pepper Robot      │         │  Off-Robot Computer  │
│                     │  WiFi   │                      │
│ pepper_bridge_v2.py │ <-----> │  pepper_client.py    │
│ (Python 2.7)        │         │  (Python 3.x)        │
│                     │         │                      │
│ HTTP :8888          │         │  Your AI System:     │
│ WebSocket :8889     │         │  - LLM/VLM           │
│                     │         │  - Whisper ASR       │
│                     │         │  - Vision models     │
└─────────────────────┘         └──────────────────────┘
```

## Installation

### On Pepper Robot

1. Copy `robot/pepper_bridge_v2.py` to `/home/nao/`
2. Run: `python2 /home/nao/pepper_bridge_v2.py`

Or to run on boot, add to `/home/nao/naoqi/preferences/autoload.ini`:
```
[python]
/home/nao/pepper_bridge_v2.py
```

### On Off-Robot Computer

```bash
pip install requests websockets numpy pillow
```

## Quick Start

```python
from pepper_client import PepperClient

# Connect to Pepper
pepper = PepperClient("PEPPER_IP_ADDRESS")

# Check status
print(pepper.status())

# Make Pepper speak
pepper.speak("Hello, world!")

# Move
pepper.move_forward(0.5)  # meters
pepper.turn(90)           # degrees

# Get camera image
frame = pepper.get_picture()
frame.save("snapshot.jpg")

# Stream video
def on_frame(frame):
    # Process frame with AI
    image = frame.to_numpy()  # or frame.to_pil()
    
pepper.start_video_stream(on_frame)
```

## API Reference

### HTTP Endpoints (port 8888)

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | `/health` | - | Health check + stream URLs |
| GET | `/status` | - | Robot status |
| GET | `/picture` | - | Camera snapshot (base64) |
| GET | `/animations` | - | List animations |
| POST | `/speak` | `{"text": "...", "animated": true}` | Speak text |
| POST | `/wake_up` | - | Wake robot |
| POST | `/rest` | - | Put robot to rest |
| POST | `/posture` | `{"posture": "StandInit"}` | Set posture |
| POST | `/move/forward` | `{"distance": 0.5}` | Move forward (m) |
| POST | `/move/turn` | `{"angle": 90}` | Turn (degrees) |
| POST | `/move/head` | `{"yaw": 0, "pitch": 0}` | Move head (deg) |
| POST | `/stop` | - | Stop motion |
| POST | `/animation` | `{"animation": "..."}` | Play animation |
| POST | `/leds/eyes` | `{"color": "blue"}` | Set eye color |
| POST | `/tablet/text` | `{"text": "..."}` | Show text on tablet |
| POST | `/tablet/web` | `{"url": "..."}` | Show webpage |
| POST | `/tablet/image` | `{"url": "..."}` | Show image |
| POST | `/tablet/hide` | - | Hide tablet |
| POST | `/awareness` | `{"enabled": true}` | Toggle awareness |

### WebSocket Streams (port 8889)

| Endpoint | Format | Rate | Description |
|----------|--------|------|-------------|
| `/video` | Binary: header_len(4) + JSON header + RGB data | 15 fps | Camera frames |
| `/audio` | Binary: header_len(4) + JSON header + WAV data | 2 Hz | Audio chunks |
| `/sensors` | JSON | 10 Hz | Sensor data |

### Video Frame Header
```json
{"type": "video", "width": 320, "height": 240, "format": "RGB", "timestamp": 1234567890.123}
```

### Audio Chunk Header
```json
{"type": "audio", "format": "wav", "sample_rate": 16000, "channels": 1, "timestamp": 1234567890.123}
```

### Sensor Data
```json
{
  "timestamp": 1234567890.123,
  "battery": {"level": 100, "charging": false},
  "sonar": {"front": 1.5, "back": 2.0},
  "touch": {"head_front": 0, "head_middle": 0, "head_rear": 0, "left_hand": 0, "right_hand": 0},
  "position": {"head_yaw": 0.0, "head_pitch": 0.0},
  "people": {"count": 1, "data": [...]}
}
```

## Example: AI Integration

```python
from pepper_client import PepperClient
import openai  # or any AI library

pepper = PepperClient("PEPPER_IP")

# Stream audio to Whisper for transcription
def on_audio(chunk):
    # Save chunk and transcribe
    chunk.save("/tmp/audio.wav")
    transcript = whisper.transcribe("/tmp/audio.wav")
    
    # Send to LLM
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": transcript}]
    )
    
    # Make Pepper respond
    pepper.speak(response.choices[0].message.content)

pepper.start_audio_stream(on_audio)
```

## License

MIT
