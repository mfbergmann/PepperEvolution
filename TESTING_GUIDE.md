# Testing Guide - Speech to Pepper with OpenAI

## What's Implemented ✅

### 1. Speech Recognition
- **Bridge Service**: Added `listen_for_speech()` method using Pepper's `ALSpeechRecognition` service
- **Endpoint**: `POST /listen` on bridge service (port 8888)
- **Integration**: Sensors manager uses bridge to listen for speech
- **Usage**: `robot.listen(timeout=5.0)` returns transcribed text

### 2. Conversation Memory
- **Storage**: Conversation history maintained in `AIManager`
- **Context Window**: Last 10 messages (configurable via `context_window`)
- **OpenAI Integration**: Full conversation history passed to OpenAI API
- **Format**: Proper message format with `user` and `assistant` roles

### 3. System Prompt
The system prompt includes:
- Pepper's identity and capabilities
- Personality traits (friendly, helpful, safety-conscious)
- Action tag documentation
- Guidelines for natural responses
- Current robot status (battery, connection state)

### 4. OpenAI Processing Flow
1. User speaks → Speech recognition (via bridge)
2. Transcribed text → `AIManager.process_user_input()`
3. Context built → Robot state + sensor data + conversation history
4. OpenAI API call → With full conversation history
5. Response generated → Includes action tags if needed
6. Actions executed → Movement, gestures, LEDs
7. Response spoken → Via Pepper's TTS
8. History updated → For next interaction

## How to Test

### 1. Start Bridge Service (if not already running)

```bash
./start_bridge.exp
```

Or manually:
```bash
ssh nao@10.0.100.100
cd /home/nao
nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &
```

### 2. Test Speech Recognition

```bash
# Test via bridge directly
curl -X POST http://10.0.100.100:8888/listen \
  -H "Content-Type: application/json" \
  -d '{"timeout": 5.0, "language": "English"}'

# Speak to Pepper and wait for response
```

### 3. Test Full Conversation Flow

```bash
# Start the application
python main.py

# In another terminal, test via API
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Pepper!"}'
```

### 4. Test Conversation Memory

```python
# The system remembers previous conversations
# Try these in sequence:

# Message 1
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is Alice"}'

# Message 2 (should remember your name)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?"}'
```

### 5. Test Speech Input (via Python)

```python
from src.pepper import PepperRobot, ConnectionConfig
from src.ai import AIManager, OpenAIProvider
import asyncio

async def test_speech():
    # Initialize
    config = ConnectionConfig(ip='10.0.100.100')
    robot = PepperRobot(config)
    await robot.initialize()
    
    # Initialize AI
    provider = OpenAIProvider(api_key="your-key", model="gpt-4")
    ai_manager = AIManager(robot, provider)
    
    # Listen and respond
    while True:
        print("Listening...")
        user_input = await robot.listen(timeout=10.0)
        
        if user_input:
            print(f"You said: {user_input}")
            response = await ai_manager.process_user_input(user_input)
            print(f"Pepper: {response}")
            # Response is automatically spoken

asyncio.run(test_speech())
```

## System Prompt

The current system prompt tells OpenAI:

```
You are Pepper, a friendly humanoid robot assistant developed by SoftBank Robotics.

Your capabilities include:
- Movement: forward, backward, turn, navigate
- Speech: speak and listen to humans
- Vision: cameras to see surroundings
- Sensors: touch, obstacles, battery monitoring
- Gestures: wave, nod, point
- LEDs: colored LEDs on face and chest
- Tablet: display images and web content

Your personality:
- Friendly, helpful, approachable
- Polite and respectful
- Enthusiastic about helping
- Safety-conscious

Guidelines:
- Keep responses concise (2-3 sentences)
- Use action tags: [MOVE:forward:0.5], [GESTURE:wave], etc.
- Response text is automatically spoken
- Remember previous conversations
- Be proactive in offering help
```

## Conversation Memory Details

- **Storage**: In `AIManager.conversation_history`
- **Format**: List of dicts with `role`, `content`, `timestamp`
- **Size**: Last 10 messages (20 total with user+assistant pairs)
- **Passed to OpenAI**: Full conversation history in proper message format
- **Not Starting from Scratch**: ✅ Each request includes full conversation context

## Troubleshooting

### Speech Recognition Not Working
- Check if bridge is running: `curl http://10.0.100.100:8888/health`
- Check bridge logs: `ssh nao@10.0.100.100 "tail -20 /tmp/pepper_bridge.log"`
- Ensure Pepper's microphones are working
- Try speaking louder/closer to Pepper

### Memory Not Working
- Check logs for conversation history updates
- Verify `context_window` is set correctly
- Check OpenAI API responses include previous context

### OpenAI Not Responding
- Verify `OPENAI_API_KEY` is set in `.env`
- Check API key has credits
- Check network connectivity to OpenAI API
- Review logs for API errors

## Next Steps

1. **Test speech recognition** - Speak to Pepper and verify transcription
2. **Test conversation memory** - Have multi-turn conversations
3. **Test action execution** - Verify movement and gestures work
4. **Monitor logs** - Watch for errors or issues
5. **Adjust system prompt** - Customize Pepper's personality if needed

