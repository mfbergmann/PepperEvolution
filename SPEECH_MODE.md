# Continuous Speech Interaction Mode

## ✅ What's Enabled

Pepper is now configured to **listen continuously** for your voice and respond naturally!

## How It Works

1. **Application starts** → Automatically begins listening for speech
2. **You speak** → Pepper's microphones pick up your voice
3. **Speech recognition** → Transcribed to text via bridge service
4. **OpenAI processes** → Generates intelligent response
5. **Pepper responds** → Speaks the response and executes any actions

## Current Status

The application is running with continuous speech conversation enabled by default.

## What You'll See in Logs

- `"Listening for speech... (30 second timeout)"` - Pepper is waiting for you
- `"Heard: [your words]"` - Speech was recognized
- `"Processing user input: [your words]"` - Being processed
- `"Speaking response: [response]"` - Pepper is speaking
- `"Responding: [response]..."` - Response being generated

## Testing

Simply **speak to Pepper** - no commands needed! Say things like:
- "Hello Pepper!"
- "What's your name?"
- "Can you wave at me?"
- "Tell me a joke"

## Troubleshooting

### If Pepper doesn't hear you:
- Speak clearly and close to Pepper
- Check microphone status on Pepper
- Check logs for speech recognition errors

### If you see "Timeout - no speech detected":
- This is normal if no one is speaking
- Pepper will keep listening
- Try speaking louder or closer

### To disable speech mode:
Set in `.env`:
```
ENABLE_SPEECH_CONVERSATION=false
```

## Tips

- Speak clearly and at normal volume
- Wait for Pepper to finish speaking before you speak
- Pepper listens for 30 seconds, then waits again
- Conversation history is maintained across interactions

