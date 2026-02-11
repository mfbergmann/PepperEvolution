"""
AI manager - orchestrates multi-turn tool-calling conversations.

Sends user messages to the AI provider, executes any tool calls,
feeds results back, and loops until the AI produces a final text response.
"""

from typing import Any, Callable, Coroutine, Dict, List, Optional
from datetime import datetime

from loguru import logger

from .models import AIProvider, AIResponse, SYSTEM_PROMPT
from .tools import TOOLS
from .tool_executor import ToolExecutor
from ..pepper.robot import PepperRobot


class AIManager:
    """Manages multi-turn AI conversations with tool calling."""

    MAX_TOOL_ROUNDS = 10  # Safety limit on tool-call loops

    def __init__(self, robot: PepperRobot, provider: AIProvider):
        self.robot = robot
        self.provider = provider
        self.executor = ToolExecutor(robot)
        self.logger = logger.bind(module="AIManager")

        self.conversation_history: List[Dict[str, Any]] = []
        self.context_window = 20  # Max messages to keep

        self._response_callbacks: List[Callable] = []

    async def process_user_input(self, user_input: str) -> Dict[str, Any]:
        """Process user input through the AI with tool calling.

        Returns a dict with:
            - text: The AI's final text response
            - tool_calls: List of tools that were called
            - model: Model used
        """
        self.logger.info(f"Processing: {user_input}")

        # Add user message
        self.conversation_history.append({"role": "user", "content": user_input})
        self._trim_history()

        all_tool_calls: List[Dict[str, Any]] = []
        last_photo: Optional[Dict[str, Any]] = None

        for round_num in range(self.MAX_TOOL_ROUNDS):
            response = await self.provider.chat(
                messages=self.conversation_history,
                tools=TOOLS,
                system=self._build_system_prompt(),
            )

            if not response.tool_calls:
                # Final text response — done
                if response.text:
                    self.conversation_history.append({"role": "assistant", "content": response.text})
                result = {
                    "text": response.text,
                    "tool_calls": all_tool_calls,
                    "model": response.model,
                }
                # Notify callbacks
                for cb in self._response_callbacks:
                    try:
                        await cb(result)
                    except Exception:
                        pass
                return result

            # Build assistant message with tool_use blocks
            assistant_content: List[Dict[str, Any]] = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            self.conversation_history.append({"role": "assistant", "content": assistant_content})

            # Execute tools and collect results
            tool_results: List[Dict[str, Any]] = []
            for tc in response.tool_calls:
                result_str = await self.executor.execute(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_str,
                })
                all_tool_calls.append({
                    "name": tc.name,
                    "input": tc.input,
                    "result": result_str,
                })

                # Track photos for the web UI
                if tc.name == "take_photo":
                    photo = await self.robot.take_picture(camera=tc.input.get("camera", 0))
                    if photo:
                        last_photo = photo

            self.conversation_history.append({"role": "user", "content": tool_results})

        # Safety: hit max rounds
        self.logger.warning("Hit max tool-call rounds")
        return {
            "text": "I got carried away with actions. Let me know if you need anything else.",
            "tool_calls": all_tool_calls,
            "model": response.model if response else "",
        }

    def _build_system_prompt(self) -> str:
        """Build the system prompt with current robot state."""
        state = self.robot.get_state()
        state_info = (
            f"\n\nCurrent robot state: battery={state.battery_level}%, "
            f"posture={state.posture}, autonomous_life={state.autonomous_life}"
        )
        return SYSTEM_PROMPT + state_info

    def _trim_history(self):
        """Keep conversation history within the context window."""
        if len(self.conversation_history) > self.context_window * 2:
            self.conversation_history = self.conversation_history[-self.context_window * 2:]

    # ------------------------------------------------------------------
    # Callbacks / utility
    # ------------------------------------------------------------------

    def on_response(self, callback: Callable):
        self._response_callbacks.append(callback)

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        return self.conversation_history.copy()

    def clear_conversation_history(self):
        self.conversation_history.clear()
