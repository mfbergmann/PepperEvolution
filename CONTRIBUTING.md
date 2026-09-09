# Contributing to PepperEvolution 🤖

Thank you for your interest in contributing to PepperEvolution! This project aims to create an open-source cloud-based AI control system for Pepper robots, and we welcome contributions from the community.

## How to Contribute

### 1. Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/mfbergmann/PepperEvolution.git
   cd PepperEvolution
   ```

### 2. Set Up Development Environment

1. Create a virtual environment (Python 3.12+):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies (runtime, test and dev tools are all in one file):
   ```bash
   pip install -r requirements.txt
   ```

3. Run everything without a robot to check your setup:
   ```bash
   pytest tests/ -q
   PEPPER_FAKE_BRIDGE=true python main.py
   ```

### 3. Make Your Changes

1. Create a new branch for your feature:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following our coding standards:
   - Host code: Python 3.12+, type hints, docstrings, `loguru` logging, black (120 cols), flake8, mypy
   - `robot_bridge/pepper_bridge.py` runs **on the robot under Python 2.7 / Tornado 3.1.1**: no f-strings,
     no `async`/`await`, no type hints, no `pathlib` (a test enforces this)
   - Keep the bridge contract in sync everywhere: bridge handler, `docs/BRIDGE_API.md`, `BridgeClient`,
     `FakeBridgeClient`

3. Write tests for your changes:
   ```bash
   pytest tests/
   ```

### 4. Testing

Before submitting your changes, please ensure:

- All tests pass: `pytest tests/` (includes starting the real bridge with `tests/fakenaoqi`)
- Code follows style guidelines: `black src/ main.py examples/ robot_bridge/deploy.py tests/ --line-length=120` and `flake8`
- Type checking passes: `mypy src/ --ignore-missing-imports --no-strict-optional`
- If you touched the bridge, also byte-compile it with a Python 2.7 interpreter if you have one

### 5. Commit and Push

1. Commit your changes with a descriptive message:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```

2. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

### 6. Submit a Pull Request

1. Go to your fork on GitHub
2. Click "New Pull Request"
3. Select your feature branch
4. Fill out the pull request template
5. Submit the PR

## Development Guidelines

### Code Style

- Use **Black** for code formatting
- Use **Flake8** for linting
- Use **MyPy** for type checking
- Follow PEP 8 conventions

### Documentation

- Update README.md if adding new features
- Add docstrings to all new functions and classes
- Update API documentation if changing endpoints
- Add examples for new functionality

### Testing

- Write unit tests for new functionality
- Ensure existing tests continue to pass
- Add integration tests for complex features
- Test with actual Pepper robot when possible

### Security

- Never commit API keys or sensitive data
- Use environment variables for configuration
- Validate all user inputs
- Follow security best practices

## Areas for Contribution

### High Priority

- **Speech input**: stream microphone audio from the bridge (`/audio/record` exists) to a speech-to-text
  service so people can talk to Pepper instead of typing
- **Wake word / turn taking**: know when someone is talking to the robot
- **Navigation**: use the laser/sonar data for longer, safer moves

### Medium Priority

- **Tablet UI**: richer content on the chest tablet (the bridge already serves `/tablet/page`)
- **Multi-Robot Support**: support controlling multiple Pepper robots
- **Memory**: remember people and earlier conversations across sessions

### Low Priority

- **Documentation**: Improve documentation and tutorials
- **Testing**: Add more comprehensive test coverage
- **Performance**: Optimize code for better performance
- **Internationalization**: Add support for multiple languages

## Bug Reports

When reporting bugs, please include:

1. **Environment details**: OS, Python version, Pepper robot version
2. **Steps to reproduce**: Clear, step-by-step instructions
3. **Expected behavior**: What you expected to happen
4. **Actual behavior**: What actually happened
5. **Error messages**: Full error traceback if applicable
6. **Screenshots/logs**: Visual evidence if helpful

## Feature Requests

When requesting features, please include:

1. **Use case**: Why this feature would be useful
2. **Proposed implementation**: How you think it should work
3. **Alternatives considered**: Other approaches you've thought about
4. **Mockups**: Visual examples if applicable

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors. Please:

- Be respectful and considerate of others
- Use inclusive language
- Be open to constructive feedback
- Help others learn and grow

## Getting Help

If you need help with your contribution:

1. Check existing issues and pull requests
2. Join our discussions on GitHub
3. Create an issue for questions or problems
4. Reach out to maintainers directly

## Recognition

Contributors will be recognized in:

- The project README
- Release notes
- Contributor hall of fame (if we create one)

Thank you for contributing to PepperEvolution! 🚀
