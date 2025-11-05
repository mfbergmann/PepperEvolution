# Quick Start Guide

## Bridge Service Status

✅ **Bridge is currently running!**

You can check status anytime with:
```bash
./check_bridge.sh
```

## Starting the System

### Option 1: All-in-One Script (Recommended)

```bash
./start_system.sh
```

This script will:
1. ✅ Check if bridge is running (it is!)
2. Start bridge if needed
3. Check environment configuration
4. Activate virtual environment
5. Start the main application

### Option 2: Manual Steps

#### 1. Check/Start Bridge Service

If bridge is not running:
```bash
./start_bridge.exp
```

Or manually:
```bash
ssh nao@10.0.100.100
cd /home/nao
nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &
```

Verify it's running:
```bash
./check_bridge.sh
```

#### 2. Configure Environment

Make sure your `.env` file has:
```bash
# Required
OPENAI_API_KEY=your_actual_api_key_here
USE_PEPPER_BRIDGE=true
PEPPER_BRIDGE_URL=http://10.0.100.100:8888

# Optional (defaults work)
PEPPER_IP=10.0.100.100
AI_MODEL=gpt-4
API_PORT=8000
WEBSOCKET_PORT=8765
```

#### 3. Start Main Application

```bash
# Activate virtual environment
source venv/bin/activate

# Start the application
python main.py
```

## What Happens When You Start

1. **Connects to Pepper** via bridge service
2. **Starts REST API** on port 8000
3. **Starts WebSocket** on port 8765
4. **Initializes AI** with OpenAI
5. **Ready for interactions!**

## Testing the System

### Test API (in another terminal)

```bash
# Health check
curl http://localhost:8000/health

# Chat with Pepper
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Pepper!"}'

# Get robot status
curl http://localhost:8000/status
```

### Test Speech Recognition

The system is ready to listen! You can:
- Use the REST API `/chat` endpoint
- Use the WebSocket interface
- Use Python code (see examples/)

## Troubleshooting

### Bridge Not Running
```bash
./check_bridge.sh  # Check status
./start_bridge.exp  # Start it
```

### Application Won't Start
- Check `.env` file exists and has `OPENAI_API_KEY`
- Check virtual environment: `source venv/bin/activate`
- Check dependencies: `pip install -r requirements.txt`

### Connection Issues
- Verify Pepper IP: `ping 10.0.100.100`
- Check bridge: `curl http://10.0.100.100:8888/health`
- Check logs: `ssh nao@10.0.100.100 "tail -20 /tmp/pepper_bridge.log"`

## Next Steps

Once running:
- ✅ Chat with Pepper via API
- ✅ Test speech recognition
- ✅ Try multi-turn conversations
- ✅ Test robot actions (movement, gestures)

See `TESTING_GUIDE.md` for more details!

