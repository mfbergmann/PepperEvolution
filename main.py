#!/usr/bin/env python3
"""
PepperEvolution v2 - Main application entry point.

Connects to the bridge server on the robot, sets up AI providers,
and runs the FastAPI + WebSocket servers concurrently.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
from loguru import logger

from src.pepper import PepperRobot, ConnectionConfig
from src.ai import AIManager, AnthropicProvider, OpenAIProvider
from src.communication import WebSocketServer, APIServer


class PepperEvolution:
    """Main PepperEvolution application."""

    def __init__(self):
        load_dotenv()

        logger.add(
            os.getenv("LOG_FILE", "pepper_evolution.log"),
            rotation="10 MB",
            retention="7 days",
            level=os.getenv("LOG_LEVEL", "INFO"),
        )
        self.logger = logger.bind(module="PepperEvolution")

        self.robot = None
        self.ai_manager = None
        self.websocket_server = None
        self.api_server = None

    async def initialize(self):
        self.logger.info("Initializing PepperEvolution v2...")

        # Robot connection via bridge
        config = ConnectionConfig(
            ip=os.getenv("PEPPER_IP", "10.0.100.100"),
            bridge_port=int(os.getenv("BRIDGE_PORT", "8888")),
            api_key=os.getenv("BRIDGE_API_KEY", ""),
            timeout=float(os.getenv("BRIDGE_TIMEOUT", "15")),
        )
        self.robot = PepperRobot(config)

        # AI provider
        ai_model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
        if ai_model.startswith("claude"):
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY is required")
            provider = AnthropicProvider(api_key, ai_model)
        elif ai_model.startswith("gpt"):
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required")
            provider = OpenAIProvider(api_key, ai_model)
        else:
            raise ValueError(f"Unsupported AI model: {ai_model}")

        self.ai_manager = AIManager(self.robot, provider)

        # Communication servers
        self.websocket_server = WebSocketServer(
            host=os.getenv("WEBSOCKET_HOST", "0.0.0.0"),
            port=int(os.getenv("WEBSOCKET_PORT", "8765")),
            ai_manager=self.ai_manager,
            robot=self.robot,
        )
        self.api_server = APIServer(
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            ai_manager=self.ai_manager,
            robot=self.robot,
        )

        # Connect to robot bridge
        if not await self.robot.initialize():
            raise RuntimeError("Failed to connect to Pepper bridge. Is the bridge running?")

        self.logger.success("PepperEvolution v2 initialized")

    async def run(self):
        try:
            self.logger.info("Starting PepperEvolution...")

            tasks = [
                asyncio.create_task(self.websocket_server.start()),
                asyncio.create_task(self.api_server.start()),
                asyncio.create_task(self.robot.start_event_loop()),
            ]

            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self.logger.info("Shutting down...")
        except Exception as exc:
            self.logger.error(f"Error in main loop: {exc}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        self.logger.info("Shutting down components...")
        if self.websocket_server:
            await self.websocket_server.stop()
        if self.api_server:
            await self.api_server.stop()
        if self.robot:
            await self.robot.shutdown()
        self.logger.success("PepperEvolution shutdown complete")


async def main():
    app = PepperEvolution()
    try:
        await app.initialize()
        await app.run()
    except Exception as exc:
        logger.error(f"Application error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
