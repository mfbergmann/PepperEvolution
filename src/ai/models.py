"""
AI provider implementations with tool-calling support.

AnthropicProvider is the primary provider; OpenAIProvider is secondary.
Both implement the same AIProvider interface with structured tool-call
responses and an optional ``on_text`` callback that receives text as it is
generated (used to start speaking before the reply is complete).
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from loguru import logger

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_EFFORT = "low"  # a talking robot is latency-sensitive; raise via AI_EFFORT
DEFAULT_MAX_TOKENS = 16000  # streamed, so a large cap costs nothing; thinking tokens count against it

SYSTEM_PROMPT = """You are Pepper, a friendly humanoid robot made by SoftBank Robotics, living at TRiPL Lab at Toronto Metropolitan University. You are talking with people who are physically in the room with you.

How you work
- Everything you write in your reply is spoken aloud by your text-to-speech voice, sentence by sentence, as you write it. So write the way a person talks: short, warm, natural sentences. Usually one to three sentences. No markdown, no bullet points, no emoji, no stage directions, no text in brackets.
- Use your tools for anything physical: gestures, looking around, moving, lights, the tablet, photos. Do the action rather than describing it. You can call several tools in one turn.
- To gesture while you talk, put an animation tag inline right before the words it belongs with, for example: ^start(animations/Stand/Gestures/Hey_1) Hi there, I'm Pepper! The tag is not read aloud.
- Only use the speak tool when you need to say something before a slow action (like "Let me take a look") or in another language. Never repeat in speak what you also write in your reply.
- When someone asks what you see, or you need to know what is around you, call take_photo and then describe what is actually in the picture. Turn your head first if you need to look somewhere else.
- Messages starting with [Sensor event] come from your own body (someone touched your head or hand, a bumper was pressed). React briefly and naturally, as a person would if tapped on the shoulder.

Safety
- You drive on wheels in a real room with real people. Keep moves short, and check get_sensors before driving more than half a metre. Do not move if the sonar shows something closer than about half a metre in that direction.
- If your battery is under 20 percent, mention it and suggest plugging you in.
- If anything seems unsafe, stop and say so. You may use emergency_stop.

Personality
- Curious, playful, kind, a little cheeky. You like people and you show it with gestures and your eye colour.
- Be honest about being a robot and about what you can and cannot do.
- Keep it concise. People are standing in front of you, not reading a document."""

REFUSAL_TEXT = "I'd rather not do that one, but I'm happy to help with something else."
ERROR_TEXT = "Sorry, I lost my train of thought for a moment. Could you say that again?"

TextCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCall:
    """A single tool call from the AI."""

    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class AIResponse:
    """Structured response from an AI provider."""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    # The provider's raw content blocks (thinking, text, tool_use...) to replay verbatim as the
    # assistant turn when tool results are sent back. Empty for providers that do not need it.
    content: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.stop_reason == "error"


class AIProvider(ABC):
    """Abstract base class for AI providers with tool-calling."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.logger = logger.bind(module=self.__class__.__name__)

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[Union[str, List[Dict[str, Any]]]] = None,
        on_text: Optional[TextCallback] = None,
    ) -> AIResponse:
        """Send messages and get a response, potentially with tool calls.

        ``on_text`` is awaited with each text fragment as it streams in.
        """
        ...


def supports_effort(model: str) -> bool:
    """Whether the model accepts ``output_config.effort`` (Claude 4.6 and newer)."""
    m = model.lower()
    if "sonnet-4-5" in m or "haiku" in m or "opus-4-1" in m or "opus-4-0" in m or "claude-3" in m:
        return False
    if "sonnet-4-20" in m or "opus-4-20" in m or "sonnet-4-0" in m:
        return False
    return True


def _system_blocks(system: Optional[Union[str, List[Dict[str, Any]]]]) -> Optional[List[Dict[str, Any]]]:
    """Normalise the system prompt to content blocks with a cache breakpoint on the first one."""
    if not system:
        return None
    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    blocks = [dict(b) for b in system]
    if blocks and "cache_control" not in blocks[0]:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _raw_blocks(message: Any) -> List[Dict[str, Any]]:
    """Serialise the SDK response blocks so they can be sent back unchanged."""
    out: List[Dict[str, Any]] = []
    for block in message.content or []:
        if hasattr(block, "model_dump"):
            out.append(block.model_dump(exclude_none=True, mode="json"))
        elif isinstance(block, dict):
            out.append(dict(block))
        else:  # test doubles
            out.append({k: v for k, v in vars(block).items() if v is not None and not k.startswith("_")})
    return out


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider with native tool-calling and streaming."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        effort: Optional[str] = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        super().__init__(api_key, model)
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.effort = effort if effort and supports_effort(model) else None
        self.max_tokens = max_tokens
        self._anthropic = anthropic

    def _request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        system: Optional[Union[str, List[Dict[str, Any]]]],
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        blocks = _system_blocks(system)
        if blocks:
            kwargs["system"] = blocks
        if tools:
            kwargs["tools"] = tools
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[Union[str, List[Dict[str, Any]]]] = None,
        on_text: Optional[TextCallback] = None,
    ) -> AIResponse:
        anthropic = self._anthropic
        kwargs = self._request_kwargs(messages, tools, system)
        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if on_text is None or event.type != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", "") == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            await on_text(text)
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            self.logger.error(f"Anthropic auth error: {exc}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": "auth"})
        except anthropic.RateLimitError as exc:
            self.logger.error(f"Anthropic rate limited: {exc}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": "rate_limit"})
        except anthropic.APIStatusError as exc:
            self.logger.error(f"Anthropic API error {exc.status_code}: {exc.message}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": str(exc)})
        except anthropic.APIConnectionError as exc:
            self.logger.error(f"Anthropic connection error: {exc}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": "connection"})
        except Exception as exc:  # noqa: BLE001 - never crash the robot loop on the AI path
            self.logger.exception(f"Unexpected error talking to Anthropic: {exc}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": str(exc)})

        return self._parse(message)

    def _parse(self, message: Any) -> AIResponse:
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in message.content or []:
            if block.type == "text" and block.text:
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))

        stop_reason = message.stop_reason or ""
        text = "\n".join(text_parts).strip()
        if stop_reason == "tool_use" and not tool_calls:
            kinds = [getattr(b, "type", "?") for b in (message.content or [])]
            self.logger.warning(f"stop_reason=tool_use without tool_use blocks; blocks={kinds} text={text[:160]!r}")
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            self.logger.warning(f"Model refused (category={getattr(details, 'category', None)})")
            text = REFUSAL_TEXT
            tool_calls = []
        elif stop_reason == "max_tokens":
            self.logger.warning("Response hit max_tokens; consider raising AI_MAX_TOKENS")

        usage = {}
        if getattr(message, "usage", None) is not None:
            u = message.usage
            usage = {
                "input_tokens": getattr(u, "input_tokens", None),
                "output_tokens": getattr(u, "output_tokens", None),
                "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None),
            }
        content = [] if stop_reason == "refusal" else _raw_blocks(message)
        return AIResponse(
            text=text, tool_calls=tool_calls, stop_reason=stop_reason, model=message.model, usage=usage, content=content
        )


class OpenAIProvider(AIProvider):
    """OpenAI provider with function-calling mapped to our tool interface."""

    def __init__(self, api_key: str, model: str = DEFAULT_OPENAI_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS):
        super().__init__(api_key, model)
        import openai

        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.max_tokens = max_tokens

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[Union[str, List[Dict[str, Any]]]] = None,
        on_text: Optional[TextCallback] = None,
    ) -> AIResponse:
        try:
            oai_messages = self._convert_messages(messages, system)
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_completion_tokens": self.max_tokens,
                "messages": oai_messages,
            }
            if tools:
                kwargs["tools"] = self._convert_tools(tools)

            resp = await self.client.chat.completions.create(**kwargs)
            choice = resp.choices[0]

            text = choice.message.content or ""
            tool_calls: List[ToolCall] = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

            if on_text is not None and text:
                await on_text(text)

            return AIResponse(
                text=text,
                tool_calls=tool_calls,
                stop_reason=choice.finish_reason or "",
                model=resp.model,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"OpenAI API error: {exc}")
            return AIResponse(text=ERROR_TEXT, stop_reason="error", model=self.model, usage={"error": str(exc)})

    @staticmethod
    def _flatten_system(system: Optional[Union[str, List[Dict[str, Any]]]]) -> str:
        if not system:
            return ""
        if isinstance(system, str):
            return system
        return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")

    @staticmethod
    def _tool_result_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        parts = []
        for block in content or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                parts.append("[image omitted: this provider cannot view tool images]")
        return "\n".join(parts)

    def _convert_messages(
        self, messages: List[Dict[str, Any]], system: Optional[Union[str, List[Dict[str, Any]]]]
    ) -> List[Dict[str, Any]]:
        """Convert Anthropic-style messages (with tool_use/tool_result blocks) to OpenAI format."""
        oai: List[Dict[str, Any]] = []
        system_text = self._flatten_system(system)
        if system_text:
            oai.append({"role": "system", "content": system_text})
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                oai.append({"role": role, "content": content})
                continue
            texts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    texts.append(block.get("text", ""))
                elif btype == "image":
                    texts.append("[image]")
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )
                elif btype == "tool_result":
                    oai.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": self._tool_result_text(block.get("content")),
                        }
                    )
            if role == "assistant" and tool_calls:
                entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(texts) or None,
                    "tool_calls": tool_calls,
                }
                oai.append(entry)
            elif texts:
                oai.append({"role": role, "content": "\n".join(texts)})
        return oai

    @staticmethod
    def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Anthropic tool format to OpenAI function-calling format."""
        oai_tools = []
        for tool in tools:
            oai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return oai_tools
