#!/usr/bin/env python3
"""
PepperEvolution - main application entry point.

Connects to the bridge server on the robot (or a fake one), sets up the AI
provider and serves the REST API, WebSocket and web UI from one port.

    python main.py              # talk to the real robot (bridge must be running)
    PEPPER_FAKE_BRIDGE=true python main.py   # everything but the robot
"""

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from src import __version__  # noqa: E402
from src.ai import (  # noqa: E402
    DEFAULT_ANTHROPIC_MODEL,
    AIManager,
    AIProvider,
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
)
from src.audio import VoiceInput, make_transcriber  # noqa: E402
from src.communication import APIServer  # noqa: E402
from src.pepper import AudioStream, ConnectionConfig, FakeBridgeClient, PepperRobot, PrepareOptions  # noqa: E402
from src.decide import DecisionClient  # noqa: E402
from src.decide.addressee import MODEL as ADDRESSEE_MODEL  # noqa: E402
from src.perception import FrameWatcher  # noqa: E402
from src.perception.vision import MODEL as VISION_MODEL  # noqa: E402
from src.session import SessionRecorder  # noqa: E402
from src.world import WorldModel  # noqa: E402


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
    ollama_url: Optional[str]
    api_host: str
    api_port: int
    speak_responses: bool
    tablet_subtitles: bool
    react_to_touch: bool
    greet_newcomers: bool
    greet_cooldown: float
    prepare_on_connect: bool
    autonomous_life: Optional[str]
    posture_on_connect: Optional[str]
    rest_on_exit: bool
    log_level: str
    log_file: str
    led_state_signals: bool
    backchannel_after: float
    awareness_on_connect: Optional[bool]
    stt_backend: str
    stt_model: str
    stt_language: str
    voice_input: bool
    voice_record_dir: Optional[str]
    photo_record_dir: Optional[str]
    session_dir: Optional[str]
    decide_url: Optional[str]
    addressee_gate: bool
    addressee_threshold: float
    router: bool
    router_threshold: float
    vision_stream: bool
    vision_fps: float

    @classmethod
    def from_env(cls) -> "Settings":
        life = os.getenv("PEPPER_AUTONOMOUS_LIFE", "disabled").strip().lower()
        awareness = os.getenv("PEPPER_AWARENESS", "false").strip().lower()
        if awareness not in ("", "keep", "none", "1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ValueError(f"PEPPER_AWARENESS={awareness!r}: use true, false or keep")
        return cls(
            pepper_ip=os.getenv("PEPPER_IP", "10.0.100.100"),
            bridge_port=int(os.getenv("BRIDGE_PORT", "8888")),
            bridge_api_key=os.getenv("BRIDGE_API_KEY", ""),
            bridge_timeout=float(os.getenv("BRIDGE_TIMEOUT", "15")),
            bridge_action_timeout=float(os.getenv("BRIDGE_ACTION_TIMEOUT", "120")),
            fake_bridge=env_bool("PEPPER_FAKE_BRIDGE", False),
            ai_model=os.getenv("AI_MODEL", DEFAULT_ANTHROPIC_MODEL),
            ai_effort=(os.getenv("AI_EFFORT", "medium").strip().lower() or None),
            ai_max_tokens=int(os.getenv("AI_MAX_TOKENS", "16000")),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            ollama_url=os.getenv("OLLAMA_URL") or None,
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
            speak_responses=env_bool("SPEAK_RESPONSES", True),
            tablet_subtitles=env_bool("TABLET_SUBTITLES", True),
            react_to_touch=env_bool("REACT_TO_TOUCH", True),
            greet_newcomers=env_bool("GREET_NEWCOMERS", True),
            greet_cooldown=float(os.getenv("GREET_COOLDOWN") or "90"),
            prepare_on_connect=env_bool("PREPARE_ON_CONNECT", True),
            autonomous_life=None if life in ("", "keep", "none") else life,
            posture_on_connect=os.getenv("POSTURE_ON_CONNECT") or None,
            rest_on_exit=env_bool("REST_ON_EXIT", False),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", "pepper_evolution.log"),
            led_state_signals=env_bool("LED_STATE_SIGNALS", True),
            backchannel_after=float(os.getenv("BACKCHANNEL_AFTER") or "2.0"),
            awareness_on_connect=None if awareness in ("", "keep", "none") else awareness in ("1", "true", "yes", "on"),
            stt_backend=(os.getenv("STT_BACKEND", "none").strip().lower() or "none"),
            stt_model=os.getenv("STT_MODEL", "").strip(),
            stt_language=(os.getenv("STT_LANGUAGE", "en").strip() or "en"),
            voice_input=env_bool("VOICE_INPUT", False),
            voice_record_dir=os.getenv("VOICE_RECORD_DIR") or None,
            photo_record_dir=os.getenv("PHOTO_RECORD_DIR") or None,
            session_dir=os.getenv("SESSION_DIR") or None,
            decide_url=os.getenv("DECIDE_URL") or None,
            addressee_gate=env_bool("ADDRESSEE_GATE", True),
            addressee_threshold=float(os.getenv("ADDRESSEE_THRESHOLD") or "0.6"),
            router=env_bool("ROUTER", True),
            router_threshold=float(os.getenv("ROUTER_THRESHOLD") or "0.7"),
            vision_stream=env_bool("VISION_STREAM", True),
            vision_fps=float(os.getenv("VISION_FPS") or "1"),
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
    if model.startswith("gpt") or re.match(r"o\d", model):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI models")
        return OpenAIProvider(settings.openai_api_key, model, max_tokens=settings.ai_max_tokens)
    if settings.ollama_url:
        return OllamaProvider(settings.ollama_url, model, max_tokens=settings.ai_max_tokens)
    raise ValueError(f"Unsupported AI model: {model} (set OLLAMA_URL for a local model)")


class PepperEvolution:
    """Main PepperEvolution application."""

    def __init__(self, settings: Optional[Settings] = None):
        load_dotenv()
        self.settings = settings or Settings.from_env()
        self.recorder: Optional[SessionRecorder] = None
        if self.settings.session_dir:
            # One folder per run holds everything for the review (CLAUDE.md, "Session records").
            self.recorder = SessionRecorder.start(self.settings.session_dir)
            self.settings.log_file = self.recorder.path("host.log")
            self.settings.voice_record_dir = self.recorder.path("audio")
            self.settings.photo_record_dir = self.recorder.path("photos")
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
        self.voice: Optional[VoiceInput] = None
        self.world = WorldModel()
        # Situation judgements on the Creative AI Hub (src/decide); off without DECIDE_URL
        self.decider: Optional[DecisionClient] = (
            DecisionClient(self.settings.decide_url) if self.settings.decide_url else None
        )

    async def initialize(self):
        s = self.settings
        self.logger.info(f"Initializing PepperEvolution {__version__}...")

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
        self.robot.photo_record_dir = s.photo_record_dir  # PHOTO_RECORD_DIR: local test records

        provider = build_provider(s)
        self.logger.info(f"AI provider: {provider.__class__.__name__} model={provider.model}")
        self.ai_manager = AIManager(
            self.robot,
            provider,
            speak_responses=s.speak_responses,
            tablet_subtitles=s.tablet_subtitles,
            react_to_touch=s.react_to_touch,
            greet_newcomers=s.greet_newcomers,
            greet_cooldown=s.greet_cooldown,
            face_tracking=s.awareness_on_connect is True,
            decider=self.decider,
            addressee_gate=s.addressee_gate,
            addressee_threshold=s.addressee_threshold,
            router=s.router,
            router_threshold=s.router_threshold,
            led_signals=s.led_state_signals,
            backchannel_after=s.backchannel_after,
            world=self.world,
        )
        self.voice = self.build_voice(config)
        self.api_server = APIServer(
            host=s.api_host,
            port=s.api_port,
            ai_manager=self.ai_manager,
            robot=self.robot,
            web_dir=ROOT / "web",
            voice=self.voice,
        )

        if self.voice is not None:
            await self.voice.load()  # a missing model fails here, before the robot is touched

        prepare = PrepareOptions(
            enabled=s.prepare_on_connect,
            autonomous_life=s.autonomous_life,
            wake_up=True,
            posture=s.posture_on_connect,
            awareness=s.awareness_on_connect,
        )
        # Before connecting: the bridge sends a snapshot (who is around) as soon as the event stream opens.
        self.robot.on_event(self.world.handle_event)
        if not await self.robot.initialize(prepare=prepare):
            raise RuntimeError(
                f"Failed to connect to the Pepper bridge at {config.base_url}. "
                "Is the bridge running? (python robot_bridge/deploy.py --status)"
            )
        self.robot.on_event(self.ai_manager.handle_event)
        self.world.on_arrival(self.ai_manager.handle_arrival)  # greet someone who walks up (GREET_NEWCOMERS)
        self.vision: Optional[FrameWatcher] = None
        if self.decider is not None and s.vision_stream and not s.fake_bridge:
            self.vision = FrameWatcher(
                config.camera_ws_url,
                self.decider,
                self.world,
                self.ai_manager.handle_event,
                fps=s.vision_fps,
                api_key=s.bridge_api_key,
                recorder=self.recorder,
                photo_dir=s.photo_record_dir,
            )
            self.vision.start()  # streams only while someone is in view
        if self.decider is not None:
            models = [ADDRESSEE_MODEL] + ([VISION_MODEL] if self.vision is not None else [])
            self.decider.keep_warm(models)  # the first call after an unload takes ~30 s
            self.logger.info(
                f"Decision models at {self.decider.base_url} (addressee gate: {s.addressee_gate}, router: {s.router}, "
                f"camera judgements: {self.vision is not None})"
            )
        if self.recorder is not None:
            self.ai_manager.recorder = self.recorder
            self.robot.on_event(self._record_robot_event)
            self.logger.info(f"Session records: {self.recorder.folder}")
        if self.world.count == 0:
            self.ai_manager.look_at_the_room()  # nobody here at start-up: head (and tracking) as for an empty room
        self.robot.start_state_loop(interval=10.0)
        if self.voice is not None:
            await self.voice.start()  # streams the robot microphone if VOICE_INPUT
        self.logger.success(
            f"PepperEvolution ready - web UI at http://localhost:{s.api_port}/  "
            f"(robot {self.robot.state.robot_name}, battery {self.robot.state.battery_level:.0f}%)"
        )

    def build_voice(self, config: ConnectionConfig) -> Optional[VoiceInput]:
        """Speech-to-text from settings: None when STT_BACKEND is ``none`` (typing only)."""
        s = self.settings
        transcriber = make_transcriber(s.stt_backend, model=s.stt_model, language=s.stt_language)
        if transcriber is None:
            return None
        source = None
        if s.voice_input:
            if s.fake_bridge:
                self.logger.warning("VOICE_INPUT ignored with the fake bridge (push-to-talk in the UI still works)")
            else:
                source = AudioStream(config.audio_ws_url, api_key=config.api_key)
        self.logger.info(
            f"Voice input: backend={transcriber.name} robot_microphone={'on' if source else 'off'} " f"push_to_talk=on"
        )
        return VoiceInput(self.ai_manager, transcriber, source=source, record_dir=s.voice_record_dir)

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

    async def _record_robot_event(self, event_type: str, data: Dict[str, Any]):
        if self.recorder is not None and event_type in ("people", "touch", "bumper"):
            self.recorder.record_event(event_type, **data)

    async def _save_bridge_log(self):
        """Best effort: copy the robot's bridge.log into the session folder (needs ssh, like deploy.py)."""
        if self.recorder is None or self.settings.fake_bridge:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(ROOT / "robot_bridge" / "deploy.py"),
                "--logs",
                "-n",
                "5000",
                "--host",
                self.settings.pepper_ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            with open(self.recorder.path("bridge.log"), "wb") as fh:
                fh.write(out)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"Could not copy the bridge log: {exc}")

    async def shutdown(self):
        self.logger.info("Shutting down components...")
        if getattr(self, "vision", None) is not None:
            await self.vision.stop()
        await self._save_bridge_log()
        if self.decider is not None:
            await self.decider.close()
        if self.voice:
            await self.voice.stop()
        if self.api_server:
            await self.api_server.stop()
        if self.robot:
            await self.robot.shutdown(rest=self.settings.rest_on_exit)
        self.logger.success("PepperEvolution shutdown complete")


async def main():
    try:
        app = PepperEvolution()
    except ValueError as exc:  # a bad .env value
        logger.error(f"Configuration error: {exc}")
        sys.exit(2)
    try:
        await app.initialize()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Startup failed: {exc}")
        await app.shutdown()  # rests the robot if REST_ON_EXIT, stops what was started
        sys.exit(1)
    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
