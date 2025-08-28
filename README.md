
# PepperEvolution 🤖☁️

A cloud-based AI control system for Pepper robots that offloads the robot's "brain" to the cloud, enabling advanced AI capabilities through bidirectional communication.

## Overview

PepperEvolution transforms your Pepper robot into an AI-powered companion by connecting it to cloud-based AI models (like GPT-5) through a robust Python interface. The system provides:

- **Bidirectional Communication**: Real-time sensor data from Pepper to AI, and AI instructions back to Pepper
- **Cloud-Based Intelligence**: Offloads computational heavy lifting to powerful cloud AI models
- **Modular Architecture**: Easy to extend and customize for different use cases
- **Open Source**: Contributes to the Pepper community since the robot is discontinued

## Features

### 🤖 Robot Control
- Movement control (walking, turning, gestures)
- Speech synthesis and recognition
- Camera and sensor data processing
- Touch and button interaction handling
- LED and display control

### 🧠 AI Integration
- OpenAI GPT-5 integration (configurable for other models)
- Real-time sensor data analysis
- Natural language understanding and generation
- Context-aware decision making
- Memory and learning capabilities

### 🔄 Communication
- WebSocket-based real-time communication
- RESTful API for external integrations
- Event-driven architecture
- Robust error handling and recovery

## Prerequisites

- Pepper robot (version 1.6 or 1.7)
- NAOqi 2.5 Python SDK
- Python 3.8+
- OpenAI API key
- Network connectivity between Pepper and cloud server

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/PepperEvolution.git
   cd PepperEvolution
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your OpenAI API key and Pepper IP address
   ```

4. **Run the system**
   ```bash
   python main.py
   ```

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Pepper Robot  │◄──►│  PepperEvolution │◄──►│  Cloud AI Model │
│                 │    │     Gateway      │    │   (GPT-5, etc.) │
│ • Sensors       │    │                  │    │                 │
│ • Actuators     │    │ • NAOqi Bridge   │    │ • Natural Lang  │
│ • Camera        │    │ • WebSocket API  │    │ • Reasoning     │
│ • Microphone    │    │ • Data Processing│    │ • Memory        │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Quick Start

1. **Basic AI Chat**
   ```python
   from pepper_evolution import PepperAI
   
   ai = PepperAI()
   ai.start_conversation()
   ```

2. **Custom Behavior**
   ```python
   from pepper_evolution import PepperAI
   
   ai = PepperAI()
   
   @ai.on_sensor_data
   def handle_sensor_data(data):
       if data['touch_head']:
           ai.speak("I felt that!")
   
   ai.run()
   ```

## Configuration

### Environment Variables
- `OPENAI_API_KEY`: Your OpenAI API key
- `PEPPER_IP`: Pepper robot's IP address
- `PEPPER_PORT`: NAOqi port (default: 9559)
- `AI_MODEL`: AI model to use (default: gpt-5)
- `LOG_LEVEL`: Logging level (default: INFO)

### AI Model Configuration
The system supports multiple AI models:
- OpenAI GPT-5 (default)
- OpenAI GPT-4
- Anthropic Claude
- Local models (via Ollama)

## Project Structure

```
PepperEvolution/
├── src/
│   ├── pepper/           # Pepper robot interface
│   ├── ai/              # AI model integrations
│   ├── communication/   # WebSocket and API handling
│   ├── sensors/         # Sensor data processing
│   └── actuators/       # Robot control commands
├── examples/            # Example applications
├── tests/              # Unit and integration tests
├── docs/               # Documentation
├── config/             # Configuration files
└── requirements.txt    # Python dependencies
```

## Examples

### 1. Interactive Assistant
Pepper acts as an AI assistant, responding to voice commands and questions.

### 2. Environmental Monitor
Pepper analyzes its surroundings using camera and sensor data, reporting findings.

### 3. Educational Companion
Pepper teaches concepts through interactive dialogue and demonstrations.

### 4. Healthcare Assistant
Pepper monitors and assists with basic healthcare tasks and reminders.

## Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- SoftBank Robotics for the Pepper robot platform
- OpenAI for providing the AI models
- The open-source Pepper community for inspiration and resources

## Support

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/YOUR_USERNAME/PepperEvolution/issues)
- 💬 [Discussions](https://github.com/YOUR_USERNAME/PepperEvolution/discussions)

---

**Note**: This project is designed for educational and research purposes. Please ensure compliance with local regulations when deploying AI-controlled robots.
