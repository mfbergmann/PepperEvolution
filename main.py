#!/usr/bin/env python3
"""
PepperEvolution - Main application entry point
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
from loguru import logger

from src.pepper import PepperRobot, ConnectionConfig
from src.ai import AIManager, OpenAIProvider, AnthropicProvider
from src.communication import WebSocketServer, APIServer


class PepperEvolution:
    """Main PepperEvolution application"""
    
    def __init__(self):
        # Load environment variables
        load_dotenv()
        
        # Configure logging
        logger.add(
            os.getenv("LOG_FILE", "pepper_evolution.log"),
            rotation="10 MB",
            retention="7 days",
            level=os.getenv("LOG_LEVEL", "INFO")
        )
        
        self.logger = logger.bind(module="PepperEvolution")
        
        # Initialize components
        self.robot = None
        self.ai_manager = None
        self.websocket_server = None
        self.api_server = None
        
    async def initialize(self):
        """Initialize all components"""
        try:
            self.logger.info("Initializing PepperEvolution...")
            
            # Initialize robot connection
            connection_config = ConnectionConfig(
                ip=os.getenv("PEPPER_IP", "192.168.1.100"),
                port=int(os.getenv("PEPPER_PORT", "9559")),
                username=os.getenv("PEPPER_USERNAME", "nao"),
                password=os.getenv("PEPPER_PASSWORD", "nao")
            )
            
            self.robot = PepperRobot(connection_config)
            
            # Initialize AI provider
            ai_model = os.getenv("AI_MODEL", "gpt-5")
            if ai_model.startswith("gpt"):
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is required")
                provider = OpenAIProvider(api_key, ai_model)
            elif ai_model.startswith("claude"):
                api_key = os.getenv("ANTHROPIC_API_KEY")
                if not api_key:
                    raise ValueError("ANTHROPIC_API_KEY environment variable is required")
                provider = AnthropicProvider(api_key, ai_model)
            else:
                raise ValueError(f"Unsupported AI model: {ai_model}")
            
            # Initialize AI manager
            self.ai_manager = AIManager(self.robot, provider)
            
            # Initialize communication servers
            self.websocket_server = WebSocketServer(
                host=os.getenv("WEBSOCKET_HOST", "0.0.0.0"),
                port=int(os.getenv("WEBSOCKET_PORT", "8765")),
                ai_manager=self.ai_manager,
                robot=self.robot
            )
            
            self.api_server = APIServer(
                host=os.getenv("API_HOST", "0.0.0.0"),
                port=int(os.getenv("API_PORT", "8000")),
                ai_manager=self.ai_manager,
                robot=self.robot
            )
            
            # Initialize robot
            if not await self.robot.initialize():
                raise RuntimeError("Failed to initialize Pepper robot")
            
            self.logger.success("PepperEvolution initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize PepperEvolution: {e}")
            raise
    
    async def run(self):
        """Run the main application"""
        try:
            self.logger.info("Starting PepperEvolution...")
            
            # Start communication servers
            websocket_task = asyncio.create_task(self.websocket_server.start())
            api_task = asyncio.create_task(self.api_server.start())
            
            # Start robot event loop
            robot_task = asyncio.create_task(self.robot.start_event_loop())
            
            # Start AI conversation (continuous speech listening)
            # Enable by default - set ENABLE_SPEECH_CONVERSATION=false to disable
            if os.getenv("ENABLE_SPEECH_CONVERSATION", "true").lower() != "false":
                self.logger.info("Starting continuous speech conversation mode...")
                conversation_task = asyncio.create_task(self.ai_manager.start_conversation())
            else:
                self.logger.info("Speech conversation mode disabled (set ENABLE_SPEECH_CONVERSATION=true to enable)")
                conversation_task = None
            
            # Wait for all tasks
            tasks = [websocket_task, api_task, robot_task]
            if conversation_task:
                tasks.append(conversation_task)
            
            await asyncio.gather(*tasks)
            
        except KeyboardInterrupt:
            self.logger.info("Shutting down PepperEvolution...")
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """Shutdown all components"""
        try:
            self.logger.info("Shutting down components...")
            
            # Stop communication servers
            if self.websocket_server:
                await self.websocket_server.stop()
            
            if self.api_server:
                await self.api_server.stop()
            
            # Shutdown robot
            if self.robot:
                await self.robot.shutdown()
            
            self.logger.success("PepperEvolution shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")


async def main():
    """Main entry point"""
    app = PepperEvolution()
    
    try:
        await app.initialize()
        await app.run()
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Run the application
    asyncio.run(main())
