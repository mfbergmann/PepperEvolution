#!/usr/bin/env python3
"""
PepperEvolution v2 - Main application entry point.

Connects to the bridge server on the robot (or a fake one), sets up the AI
provider and serves the REST API, WebSocket and web UI from one port.

    python main.py              # talk to the real robot (bridge must be running)
    PEPPER_FAKE_BRIDGE=true python main.py   # everything but the robot
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from src.ai import DEFAULT_ANTHROPIC_MODEL, AIManager, AIProvider, AnthropicProvider, OpenAIProvider  # noqa: E402
from src.communication import APIServer  # noqa: E402
from src.pepper import ConnectionConfig, FakeBridgeClient, PepperRobot, PrepareOptions  # noqa: E402


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    pepper_ip: str
    bridge_port: int
    bridge_api_key: str
    bridge_timeout: float
    bridge_action_timeout: float
    fake_bridge: bool
    ai_model: str
    ai_effort: Optional[str]
    ai_max_tokens: int
    anthropic_api_key: Optional[str]
    openai_api_key: Optional[str]
    api_host: str
    api_port: int
    speak_responses: bool
    tablet_subtitles: bool
    react_to_touch: bool
    prepare_on_connect: bool
    autonomous_life: Optional[str]
    posture_on_connect: Optional[str]
    rest_on_exit: bool
    log_level: str
    log_file: str

    @classmethod
    def from_env(cls) -> "Settings":
        life = os.getenv("PEPPER_AUTONOMOUS_LIFE", "disabled").strip().lower()
        return cls(
            pepper_ip=os.getenv("PEPPER_IP", "10.0.100.100"),
            bridge_port=int(os.getenv("BRIDGE_PORT", "8888")),
            bridge_api_key=os.getenv("BRIDGE_API_KEY", ""),
            bridge_timeout=float(os.getenv("BRIDGE_TIMEOUT", "15")),
            bridge_action_timeout=float(os.getenv("BRIDGE_ACTION_TIMEOUT", "120")),
            fake_bridge=env_bool("PEPPER_FAKE_BRIDGE", False),
            ai_model=os.getenv("AI_MODEL", DEFAULT_ANTHROPIC_MODEL),
            ai_effort=(os.getenv("AI_EFFORT", "low").strip().lower() or None),
            ai_max_tokens=int(os.getenv("AI_MAX_TOKENS", "16000")),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
            speak_responses=env_bool("SPEAK_RESPONSES", True),
            tablet_subtitles=env_bool("TABLET_SUBTITLES", True),
            react_to_touch=env_bool("REACT_TO_TOUCH", True),
            prepare_on_connect=env_bool("PREPARE_ON_CONNECT", True),
            autonomous_life=None if life in ("", "keep", "none") else life,
            posture_on_connect=os.getenv("POSTURE_ON_CONNECT") or None,
            rest_on_exit=env_bool("REST_ON_EXIT", False),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", "pepper_evolution.log"),
        )


def build_provider(settings: Settings) -> AIProvider:
    model = settings.ai_model
    if model.startswith("claude"):
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Claude models")
        return AnthropicProvider(
            settings.anthropic_api_key,
            model,
            effort=settings.ai_effort if settings.ai_effort not in ("", "none") else None,
            max_tokens=settings.ai_max_tokens,
        )
    if model.startswith("gpt") or model.startswith("o"):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI models")
        return OpenAIProvider(settings.openai_api_key, model, max_tokens=settings.ai_max_tokens)
    raise ValueError(f"Unsupported AI model: {model}")


class PepperEvolution:
    """Main PepperEvolution application."""

    def __init__(self, settings: Optional[Settings] = None):
        load_dotenv()
        self.settings = settings or Settings.from_env()
        logger.add(
            self.settings.log_file,
            rotation="10 MB",
            retention="7 days",
            level=self.settings.log_level,
        )
        self.logger = logger.bind(module="PepperEvolution")
        self.robot: Optional[PepperRobot] = None
        self.ai_manager: Optional[AIManager] = None
        self.api_server: Optional[APIServer] = None

    async def initialize(self):
        s = self.settings
        self.logger.info("Initializing PepperEvolution v2.1...")

        config = ConnectionConfig(
            ip=s.pepper_ip,
            bridge_port=s.bridge_port,
            api_key=s.bridge_api_key,
            timeout=s.bridge_timeout,
            action_timeout=s.bridge_action_timeout,
        )
        bridge = FakeBridgeClient() if s.fake_bridge else None
        if bridge:
            self.logger.warning("PEPPER_FAKE_BRIDGE is set: no robot will be contacted")
        self.robot = PepperRobot(config, bridge=bridge)

        provider = build_provider(s)
        self.logger.info(f"AI provider: {provider.__class__.__name__} model={provider.model}")
        self.ai_manager = AIManager(
            self.robot,
            provider,
            speak_responses=s.speak_responses,
            tablet_subtitles=s.tablet_subtitles,
            react_to_touch=s.react_to_touch,
        )
        self.api_server = APIServer(
            host=s.api_host, port=s.api_port, ai_manager=self.ai_manager, robot=self.robot, web_dir=ROOT / "web"
        )

        prepare = PrepareOptions(
            enabled=s.prepare_on_connect,
            autonomous_life=s.autonomous_life,
            wake_up=True,
            posture=s.posture_on_connect,
        )
        if not await self.robot.initialize(prepare=prepare):
            raise RuntimeError(
                f"Failed to connect to the Pepper bridge at {config.base_url}. "
                "Is the bridge running? (python robot_bridge/deploy.py --status)"
            )
        self.robot.on_event(self.ai_manager.handle_event)
        self.robot.start_state_loop(interval=10.0)
        self.logger.success(
            f"PepperEvolution ready - web UI at http://localhost:{s.api_port}/  "
            f"(robot {self.robot.state.robot_name}, battery {self.robot.state.battery_level:.0f}%)"
        )

    async def run(self):
        try:
            await self.api_server.start()  # returns on SIGINT/SIGTERM
        finally:
            # Shield the teardown: a stray cancellation must not skip resting/disconnecting the robot.
            teardown = asyncio.ensure_future(self.shutdown())
            try:
                await asyncio.shield(teardown)
            except asyncio.CancelledError:
                await teardown
                task = asyncio.current_task()
                if task is not None and hasattr(task, "uncancel"):
                    task.uncancel()

    async def shutdown(self):
        self.logger.info("Shutting down components...")
        if self.api_server:
            await self.api_server.stop()
        if self.robot:
            await self.robot.shutdown(rest=self.settings.rest_on_exit)
        self.logger.success("PepperEvolution shutdown complete")


async def main():
    app = PepperEvolution()
    try:
        await app.initialize()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Startup failed: {exc}")
        if app.robot:
            await app.robot.shutdown()
        sys.exit(1)
    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
