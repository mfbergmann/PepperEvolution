# Getting Started with PepperEvolution 🤖

This guide will help you set up and run PepperEvolution on your Pepper robot.

## Prerequisites

### Hardware Requirements
- Pepper robot (version 1.6 or 1.7)
- Computer with Python 3.8+ (can be the same computer or a separate one)
- Network connection between Pepper and your computer

### Software Requirements
- Python 3.8 or higher
- NAOqi 2.5 Python SDK
- OpenAI API key (or other supported AI provider)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/mfbergmann/PepperEvolution.git
cd PepperEvolution
```

### 2. Install Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Install NAOqi SDK

You'll need to install the NAOqi 2.5 Python SDK. This is typically provided by SoftBank Robotics.

**For Pepper 1.6:**
```bash
# Download and install NAOqi 2.5 SDK
# Follow the official SoftBank Robotics documentation
```

**For Pepper 1.7:**
```bash
# Download and install NAOqi 2.5 SDK
# Follow the official SoftBank Robotics documentation
```

### 4. Configure Environment

```bash
# Copy example environment file
cp env.example .env

# Edit the .env file with your settings
nano .env  # or use your preferred editor
```

Update the following variables in your `.env` file:

```env
# Pepper Robot Configuration
PEPPER_IP=192.168.1.100  # Your Pepper's IP address
PEPPER_PORT=9559
PEPPER_USERNAME=nao
PEPPER_PASSWORD=nao

# AI Model Configuration
OPENAI_API_KEY=your_openai_api_key_here
AI_MODEL=gpt-4  # or gpt-5 when available
```

## Quick Start

### 1. Basic Chat Example

Run the basic chat example to test your setup:

```bash
python examples/basic_chat.py
```

This will:
- Connect to your Pepper robot
- Start an interactive conversation
- Use AI to generate responses

### 2. Environment Monitor

Run the environment monitoring example:

```bash
python examples/environment_monitor.py
```

This will:
- Continuously monitor sensor data
- Analyze the environment using AI
- Provide insights about the robot's surroundings

### 3. Full Application

Run the complete PepperEvolution application:

```bash
python main.py
```

This starts:
- WebSocket server (port 8765)
- REST API server (port 8000)
- Robot event loop
- AI conversation (if enabled)

## Web Interface

Once the application is running, you can access the web interface:

1. Open your browser
2. Navigate to `examples/web_interface.html`
3. The interface will connect to the running servers

**Features:**
- Real-time robot control
- Live status monitoring
- AI chat interface
- Sensor data visualization

## API Usage

### REST API Endpoints

The application provides a REST API on port 8000:

```bash
# Health check
curl http://localhost:8000/health

# Get robot status
curl http://localhost:8000/status

# Send chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Pepper!"}'

# Make robot speak
curl -X POST http://localhost:8000/speak \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello world!"}'

# Move robot forward
curl -X POST "http://localhost:8000/move/forward?distance=0.5"

# Take a photo
curl -X POST http://localhost:8000/photo
```

### WebSocket API

Connect to the WebSocket server on port 8765 for real-time communication:

```javascript
const ws = new WebSocket('ws://localhost:8765');

// Send chat message
ws.send(JSON.stringify({
  type: 'chat',
  message: 'Hello Pepper!'
}));

// Request status
ws.send(JSON.stringify({
  type: 'status_request'
}));
```

## Configuration Options

### AI Models

PepperEvolution supports multiple AI providers:

**OpenAI GPT-4/GPT-5:**
```env
AI_MODEL=gpt-4
OPENAI_API_KEY=your_key_here
```

**Anthropic Claude:**
```env
AI_MODEL=claude-3-sonnet-20240229
ANTHROPIC_API_KEY=your_key_here
```

### Robot Settings

**Connection:**
```env
PEPPER_IP=192.168.1.100
PEPPER_PORT=9559
PEPPER_USERNAME=nao
PEPPER_PASSWORD=nao
```

**Communication:**
```env
WEBSOCKET_HOST=0.0.0.0
WEBSOCKET_PORT=8765
API_HOST=0.0.0.0
API_PORT=8000
```

**Sensors:**
```env
CAMERA_RESOLUTION=640x480
CAMERA_FPS=30
SENSOR_UPDATE_RATE=10
```

## Troubleshooting

### Common Issues

**1. Connection Failed**
```
Error: Failed to connect to Pepper robot
```
- Check Pepper's IP address
- Ensure Pepper is powered on and connected to network
- Verify NAOqi services are running on Pepper

**2. NAOqi Import Error**
```
ModuleNotFoundError: No module named 'qi'
```
- Install NAOqi 2.5 Python SDK
- Add NAOqi to your Python path

**3. API Key Error**
```
ValueError: OPENAI_API_KEY environment variable is required
```
- Set your OpenAI API key in the `.env` file
- Ensure the key is valid and has sufficient credits

**4. Permission Denied**
```
PermissionError: [Errno 13] Permission denied
```
- Run with appropriate permissions
- Check firewall settings
- Ensure ports are available

### Debug Mode

Enable debug logging:

```env
LOG_LEVEL=DEBUG
```

### Testing Without Robot

You can test the AI components without a physical robot by modifying the connection settings or using mock data.

## Next Steps

### Advanced Features

1. **Custom Behaviors**: Create your own robot behaviors
2. **Computer Vision**: Add object detection and recognition
3. **Navigation**: Implement autonomous navigation
4. **Multi-Robot**: Control multiple Pepper robots

### Development

1. **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md)
2. **Testing**: Run tests with `pytest tests/`
3. **Documentation**: Check the `docs/` directory

### Support

- **Issues**: Report bugs on GitHub
- **Discussions**: Join community discussions
- **Documentation**: Check the docs directory

## Safety Notes

⚠️ **Important Safety Information:**

- Always ensure Pepper is in a safe environment before testing
- Keep emergency stop procedures ready
- Test movements in open areas first
- Monitor battery levels and temperature
- Follow local robotics safety guidelines

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

**Happy coding with PepperEvolution! 🤖✨**
