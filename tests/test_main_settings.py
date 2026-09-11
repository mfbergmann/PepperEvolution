"""
Tests for main.py configuration and provider selection.
"""

import pytest

import main
from src.ai.models import AnthropicProvider, OpenAIProvider


def settings_with(monkeypatch, **env):
    for key in list(env):
        monkeypatch.setenv(key, env[key])
    return main.Settings.from_env()


class TestSettings:

    def test_defaults(self, monkeypatch):
        for key in (
            "PEPPER_IP",
            "AI_MODEL",
            "AI_EFFORT",
            "PEPPER_AUTONOMOUS_LIFE",
            "SPEAK_RESPONSES",
            "PEPPER_FAKE_BRIDGE",
            "API_PORT",
        ):
            monkeypatch.delenv(key, raising=False)
        s = main.Settings.from_env()
        assert s.pepper_ip == "10.0.100.100"
        assert s.ai_model == "claude-opus-5"
        assert s.ai_effort == "low"
        assert s.autonomous_life == "disabled"
        assert s.speak_responses is True and s.fake_bridge is False
        assert s.api_port == 8000

    def test_reactive_and_voice_defaults(self, monkeypatch):
        for key in (
            "STT_BACKEND",
            "STT_MODEL",
            "VOICE_INPUT",
            "VOICE_RECORD_DIR",
            "LED_STATE_SIGNALS",
            "BACKCHANNEL_AFTER",
            "PEPPER_AWARENESS",
        ):
            monkeypatch.delenv(key, raising=False)
        s = main.Settings.from_env()
        assert s.stt_backend == "none" and s.stt_model == "" and s.stt_language == "en"
        assert s.voice_input is False and s.voice_record_dir is None
        assert s.led_state_signals is True and s.backchannel_after == 2.0
        assert s.awareness_on_connect is False

    def test_awareness_values(self, monkeypatch):
        assert settings_with(monkeypatch, PEPPER_AWARENESS="true").awareness_on_connect is True
        assert settings_with(monkeypatch, PEPPER_AWARENESS="off").awareness_on_connect is False
        assert settings_with(monkeypatch, PEPPER_AWARENESS="").awareness_on_connect is None
        with pytest.raises(ValueError):
            settings_with(monkeypatch, PEPPER_AWARENESS="Head")
        assert settings_with(monkeypatch, PEPPER_AWARENESS="true", BACKCHANNEL_AFTER="").backchannel_after == 2.0

    def test_reactive_and_voice_settings(self, monkeypatch):
        s = settings_with(
            monkeypatch,
            STT_BACKEND="Whisper",
            STT_MODEL="small",
            VOICE_INPUT="true",
            VOICE_RECORD_DIR="/tmp/utt",
            LED_STATE_SIGNALS="false",
            BACKCHANNEL_AFTER="0",
            PEPPER_AWARENESS="keep",
        )
        assert s.stt_backend == "whisper" and s.stt_model == "small" and s.voice_input is True
        assert s.voice_record_dir == "/tmp/utt" and s.led_state_signals is False and s.backchannel_after == 0
        assert s.awareness_on_connect is None
        assert settings_with(monkeypatch, PEPPER_AWARENESS="false").awareness_on_connect is False

    def test_build_voice(self, monkeypatch):
        from unittest.mock import MagicMock

        from src.pepper import ConnectionConfig

        app = main.PepperEvolution.__new__(main.PepperEvolution)
        app.logger = MagicMock()
        app.ai_manager = MagicMock()
        app.settings = settings_with(monkeypatch, STT_BACKEND="fake", VOICE_INPUT="true", PEPPER_FAKE_BRIDGE="true")
        voice = app.build_voice(ConnectionConfig(ip="1.2.3.4"))
        assert voice.transcriber.name == "fake" and voice.source is None  # no robot microphone with the fake bridge
        app.settings.fake_bridge = False
        voice = app.build_voice(ConnectionConfig(ip="1.2.3.4", api_key="k"))
        assert voice.source.ws_url == "ws://1.2.3.4:8888/ws/audio" and voice.source.api_key == "k"
        app.settings.stt_backend = "none"
        assert app.build_voice(ConnectionConfig(ip="1.2.3.4")) is None

    def test_keep_autonomous_life(self, monkeypatch):
        assert settings_with(monkeypatch, PEPPER_AUTONOMOUS_LIFE="keep").autonomous_life is None
        assert settings_with(monkeypatch, PEPPER_AUTONOMOUS_LIFE="solitary").autonomous_life == "solitary"

    def test_bools(self, monkeypatch):
        s = settings_with(monkeypatch, PEPPER_FAKE_BRIDGE="yes", SPEAK_RESPONSES="0", REACT_TO_TOUCH="false")
        assert s.fake_bridge is True and s.speak_responses is False and s.react_to_touch is False
        assert main.env_bool("MISSING_VAR_X", True) is True


class TestBuildProvider:

    def test_anthropic(self, monkeypatch):
        s = settings_with(monkeypatch, AI_MODEL="claude-opus-5", ANTHROPIC_API_KEY="sk-test", AI_EFFORT="medium")
        provider = main.build_provider(s)
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-opus-5" and provider.effort == "medium"

    def test_anthropic_effort_gated_for_old_model(self, monkeypatch):
        s = settings_with(monkeypatch, AI_MODEL="claude-sonnet-4-5-20250929", ANTHROPIC_API_KEY="sk-test")
        assert main.build_provider(s).effort is None

    def test_anthropic_requires_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        s = settings_with(monkeypatch, AI_MODEL="claude-opus-5")
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            main.build_provider(s)

    def test_openai(self, monkeypatch):
        s = settings_with(monkeypatch, AI_MODEL="gpt-4o", OPENAI_API_KEY="sk-test")
        assert isinstance(main.build_provider(s), OpenAIProvider)

    def test_unknown_model(self, monkeypatch):
        s = settings_with(monkeypatch, AI_MODEL="llama-3")
        with pytest.raises(ValueError, match="Unsupported"):
            main.build_provider(s)
