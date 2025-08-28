#!/usr/bin/env python3
"""
Basic chat example for PepperEvolution
Demonstrates simple AI conversation with Pepper robot
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
from loguru import logger

from src.pepper import PepperRobot, ConnectionConfig
from src.ai import AIManager, OpenAIProvider


async def basic_chat_example():
    """Basic chat example with Pepper robot"""
    
    # Load environment variables
    load_dotenv()
    
    # Configure logging
    logger.add("basic_chat.log", level="INFO")
    
    try:
        # Initialize robot connection
        connection_config = ConnectionConfig(
            ip=os.getenv("PEPPER_IP", "192.168.1.100"),
            port=int(os.getenv("PEPPER_PORT", "9559"))
        )
        
        robot = PepperRobot(connection_config)
        
        # Initialize AI provider
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        
        provider = OpenAIProvider(api_key, "gpt-4")
        ai_manager = AIManager(robot, provider)
        
        # Initialize robot
        logger.info("Initializing Pepper robot...")
        if not await robot.initialize():
            raise RuntimeError("Failed to initialize Pepper robot")
        
        logger.success("Pepper robot initialized successfully")
        
        # Start conversation
        logger.info("Starting conversation...")
        await ai_manager.start_conversation()
        
    except KeyboardInterrupt:
        logger.info("Conversation ended by user")
    except Exception as e:
        logger.error(f"Error in basic chat example: {e}")
    finally:
        # Cleanup
        if 'robot' in locals():
            await robot.shutdown()


if __name__ == "__main__":
    asyncio.run(basic_chat_example())
