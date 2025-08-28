"""
Basic import tests to ensure all modules can be loaded
"""

def test_import_pepper_modules():
    """Test that pepper modules can be imported"""
    try:
        from src.pepper import PepperRobot, ConnectionConfig
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import pepper modules: {e}")

def test_import_ai_modules():
    """Test that AI modules can be imported"""
    try:
        from src.ai import AIManager, OpenAIProvider, AnthropicProvider
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import AI modules: {e}")

def test_import_communication_modules():
    """Test that communication modules can be imported"""
    try:
        from src.communication import WebSocketServer, APIServer
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import communication modules: {e}")

def test_import_sensor_modules():
    """Test that sensor modules can be imported"""
    try:
        from src.sensors import SensorManager
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import sensor modules: {e}")

def test_import_actuator_modules():
    """Test that actuator modules can be imported"""
    try:
        from src.actuators import ActuatorManager
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import actuator modules: {e}")

def test_import_main():
    """Test that main module can be imported"""
    try:
        import main
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import main module: {e}")
