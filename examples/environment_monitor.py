#!/usr/bin/env python3
"""
Environment monitoring example for PepperEvolution
Demonstrates continuous sensor data analysis and environment monitoring
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


async def environment_monitor_example():
    """Environment monitoring example with Pepper robot"""
    
    # Load environment variables
    load_dotenv()
    
    # Configure logging
    logger.add("environment_monitor.log", level="INFO")
    
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
        
        # Set up event handlers
        def on_sensor_data(data):
            logger.info(f"Received sensor data: {data}")
        
        def on_analysis(analysis, sensor_data):
            logger.info(f"Environment analysis: {analysis.get('analysis', 'No analysis available')}")
            
            # Check for important events
            if sensor_data.get("touch", {}).get("touched"):
                logger.warning("Touch detected!")
                asyncio.create_task(robot.speak("I felt something touch me!"))
            
            if sensor_data.get("sonar", {}).get("obstacle_detected"):
                logger.warning("Obstacle detected!")
                asyncio.create_task(robot.speak("I see an obstacle ahead!"))
        
        # Register event handlers
        robot.on_sensor_data(on_sensor_data)
        ai_manager.on_analysis(on_analysis)
        
        # Start monitoring loop
        logger.info("Starting environment monitoring...")
        
        while True:
            try:
                # Analyze environment every 30 seconds
                analysis = await ai_manager.analyze_environment()
                
                if analysis.get("analysis"):
                    logger.info(f"Environment analysis: {analysis['analysis']}")
                    
                    # Speak important findings
                    if "person" in analysis["analysis"].lower():
                        await robot.speak("I can see someone in my environment!")
                    elif "obstacle" in analysis["analysis"].lower():
                        await robot.speak("I notice an obstacle nearby!")
                    elif "activity" in analysis["analysis"].lower():
                        await robot.speak("I detect some activity around me!")
                
                # Wait before next analysis
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)
        
    except KeyboardInterrupt:
        logger.info("Environment monitoring ended by user")
    except Exception as e:
        logger.error(f"Error in environment monitor example: {e}")
    finally:
        # Cleanup
        if 'robot' in locals():
            await robot.shutdown()


if __name__ == "__main__":
    asyncio.run(environment_monitor_example())
