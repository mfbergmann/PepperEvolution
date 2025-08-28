# Contributing to PepperEvolution 🤖

Thank you for your interest in contributing to PepperEvolution! This project aims to create an open-source cloud-based AI control system for Pepper robots, and we welcome contributions from the community.

## How to Contribute

### 1. Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/yourusername/PepperEvolution.git
   cd PepperEvolution
   ```

### 2. Set Up Development Environment

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Install development dependencies:
   ```bash
   pip install -r requirements-dev.txt  # If available
   ```

### 3. Make Your Changes

1. Create a new branch for your feature:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following our coding standards:
   - Use Python 3.8+ syntax
   - Follow PEP 8 style guidelines
   - Add type hints where appropriate
   - Write docstrings for all functions and classes
   - Add logging using the loguru library

3. Write tests for your changes:
   ```bash
   pytest tests/
   ```

### 4. Testing

Before submitting your changes, please ensure:

- All tests pass: `pytest tests/`
- Code follows style guidelines: `black src/` and `flake8 src/`
- Type checking passes: `mypy src/`

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

- **Speech Recognition**: Implement proper speech-to-text functionality
- **Computer Vision**: Add object detection and recognition
- **Navigation**: Implement autonomous navigation capabilities
- **Safety Features**: Add collision avoidance and safety protocols

### Medium Priority

- **Web Interface**: Create a web-based control panel
- **Mobile App**: Develop a mobile app for robot control
- **Plugin System**: Create a plugin architecture for extensions
- **Multi-Robot Support**: Support controlling multiple Pepper robots

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
