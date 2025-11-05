# PepperEvolution 🤖☁️

A cloud-based AI control system for Pepper robots that connects the robot to OpenAI models for intelligent decision-making and natural interactions.

## Overview

PepperEvolution transforms your Pepper robot into an AI-powered companion by connecting it to cloud-based AI models (like GPT-4) through a bridge service architecture. The system provides:

- **Bidirectional Communication**: Real-time sensor data from Pepper to AI, and AI instructions back to Pepper
- **Cloud-Based Intelligence**: Offloads computational heavy lifting to powerful cloud AI models
- **Bridge Service Architecture**: Clean HTTP API that bypasses SDK compatibility issues
- **Modular Design**: Easy to extend and customize for different use cases

## Architecture

```
┌─────────────────┐    HTTP/JSON    ┌──────────────────┐    NAOqi    ┌─────────────┐
│   Your Server   │ ◄─────────────► │  Bridge Service  │ ◄─────────► │   Pepper   │
│                 │    Port 8888    │  (on Pepper)     │   Local     │   Robot     │
│ • Python 3.9+  │                 │  • Python 2.7   │             │             │
│ • FastAPI       │                 │  • HTTP Server   │             │             │
│ • OpenAI API    │                 │  • NAOqi Access  │             │             │
└─────────────────┘                 └──────────────────┘             └─────────────┘
```

## Bridge Service

The bridge service runs directly on Pepper and provides a clean HTTP API for robot control. This architecture:

- ✅ Eliminates SDK compatibility issues between macOS and Pepper's Linux system
- ✅ Provides real robot control (not simulation)
- ✅ Uses modern HTTP/JSON for easy integration
- ✅ Allows updates without restarting the robot

### Setting Up the Bridge

1. **Deploy the bridge service to Pepper:**
   ```bash
   ./deploy_bridge.sh
   ```

2. **Start the bridge service:**
   ```bash
   ./start_bridge.exp
   ```

   Or manually via SSH:
   ```bash
   ssh nao@10.0.100.100
   cd /home/nao
   nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &
   ```

3. **Verify it's running:**
   ```bash
   curl http://10.0.100.100:8888/health
   ```

## Prerequisites

- Pepper robot (version 1.6 or 1.7)
- Python 3.8+ on your development machine
- OpenAI API key
- Network connectivity between your machine and Pepper

**Note**: You no longer need the NAOqi SDK on your development machine - the bridge service handles all NAOqi communication.

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/mfbergmann/PepperEvolution.git
   cd PepperEvolution
   ```

2. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp env.example .env
   # Edit .env with your settings:
   # - OPENAI_API_KEY: Your OpenAI API key
   # - PEPPER_IP: Pepper robot's IP address (e.g., 10.0.100.100)
   # - USE_PEPPER_BRIDGE=true (enables bridge mode)
   # - PEPPER_BRIDGE_URL=http://10.0.100.100:8888
   ```

5. **Deploy and start bridge service** (see Bridge Service section above)

6. **Run the system**
   ```bash
   python main.py
   ```

## Quick Start

Once the bridge service is running and your `.env` is configured:

```bash
# Start the application
python main.py
```

The system will:
- Connect to Pepper via the bridge service
- Start the REST API on port 8000
- Start the WebSocket server on port 8765
- Enable AI-powered interactions

### Test the API

```bash
# Health check
curl http://localhost:8000/health

# Chat with Pepper via AI
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Pepper!"}'

# Get robot status
curl http://localhost:8000/status
```

## Configuration

### Environment Variables

- `OPENAI_API_KEY`: Your OpenAI API key (required)
- `PEPPER_IP`: Pepper robot's IP address
- `USE_PEPPER_BRIDGE`: Set to `true` to use bridge service (recommended)
- `PEPPER_BRIDGE_URL`: Bridge service URL (default: `http://10.0.100.100:8888`)
- `AI_MODEL`: AI model to use (default: `gpt-4`)
- `API_PORT`: REST API port (default: 8000)
- `WEBSOCKET_PORT`: WebSocket port (default: 8765)

### AI Model Configuration

The system supports:
- OpenAI GPT-4 (default)
- OpenAI GPT-3.5-turbo
- Anthropic Claude (via AnthropicProvider)

## Project Structure

```
PepperEvolution/
├── src/
│   ├── pepper/           # Pepper robot interface
│   │   ├── connection.py      # Connection management (supports bridge)
│   │   ├── bridge_client.py   # Bridge service client
│   │   └── robot.py           # High-level robot interface
│   ├── ai/              # AI model integrations
│   │   ├── manager.py         # AI decision-making logic
│   │   └── models.py          # AI provider implementations
│   ├── communication/   # WebSocket and API handling
│   ├── sensors/         # Sensor data processing
│   └── actuators/       # Robot control commands
├── examples/            # Example applications
├── tests/              # Unit and integration tests
├── pepper_bridge.py     # Bridge service (deploys to Pepper)
├── deploy_bridge.sh    # Bridge deployment script
└── main.py            # Main application entry point
```

## How It Works

1. **User Input**: User sends message via REST API or WebSocket
2. **AI Processing**: OpenAI analyzes the message with robot context (sensors, state, history)
3. **Action Generation**: AI generates response text and action commands (movement, gestures, etc.)
4. **Bridge Communication**: Actions sent to Pepper via bridge service HTTP API
5. **Robot Execution**: Pepper executes actions via NAOqi
6. **Response**: Robot speaks the AI's response

## API Endpoints

### REST API (Port 8000)

- `GET /` - API information
- `GET /health` - Health check
- `GET /status` - Robot status
- `GET /sensors` - Sensor data
- `POST /chat` - Chat with AI (returns response and executes actions)
- `POST /command` - Direct robot commands

### WebSocket (Port 8765)

- Real-time bidirectional communication
- Chat interface
- Sensor data streaming
- Event notifications

## Troubleshooting

### Bridge Service Not Responding

```bash
# Check if bridge is running on Pepper
ssh nao@10.0.100.100 "ps aux | grep pepper_bridge"

# Check bridge logs
ssh nao@10.0.100.100 "tail -20 /tmp/pepper_bridge.log"

# Restart bridge
ssh nao@10.0.100.100 "pkill -f pepper_bridge.py && cd /home/nao && nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &"
```

### Connection Issues

- Verify Pepper IP is correct in `.env`
- Check network connectivity: `ping 10.0.100.100`
- Verify bridge is running: `curl http://10.0.100.100:8888/health`
- Check firewall settings

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Credits

PepperEvolution is a performance research experiment running out of [TRiPL Lab](https://tripl.ca/).

---

**Note**: This project is designed for educational and research purposes. Please ensure compliance with local regulations when deploying AI-controlled robots.
