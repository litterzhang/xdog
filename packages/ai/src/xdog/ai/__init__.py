"""ai — Unified multi-provider LLM API.

Usage::

    import xdog.ai as ai
    copilot = ai.provider("copilot")
    copilot.stream("claude-sonnet-4.5", context)

    runtime = ai.load()
    runtime.provider("copilot").stream(...)
"""

from xdog.ai.api import load, login, provider
from xdog.ai.core import AuthResult, BaseProvider
from xdog.ai.native import (
    NativeEventStream,
    NativeHTTPError,
    NativeOperation,
    NativeResponse,
    NativeResponseStart,
    NativeSSEEvent,
    ProtocolRequest,
)
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    CostBreakdown,
    DoneEvent,
    EmbeddingObject,
    EmbeddingRequest,
    EmbeddingResponse,
    ErrorEvent,
    ImageContent,
    JsonSchemaFormat,
    Message,
    Model,
    ModelCost,
    ProviderType,
    StartEvent,
    StreamOptions,
    SystemPrompt,
    SystemPromptBlock,
    TextContent,
    TextDeltaEvent,
    TextVerbosity,
    ThinkingContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResultMessage,
    Usage,
    UserMessage,
    system_prompt_text,
)
from xdog.ai.utils.event_stream import EventStream

__all__ = [
    # API
    "provider", "load", "login",
    # Interfaces
    "BaseProvider", "AuthResult", "EventStream",
    # Native protocol transport
    "ProtocolRequest", "NativeOperation", "NativeResponse", "NativeResponseStart",
    "NativeSSEEvent", "NativeEventStream", "NativeHTTPError",
    # Types
    "Model", "ModelCost", "Context", "Usage", "StreamOptions",
    "ThinkingLevel", "TextVerbosity", "ProviderType",
    "UserMessage", "AssistantMessage", "ToolResultMessage", "Message",
    "TextContent", "ImageContent", "ThinkingContent", "ToolCall",
    "StartEvent", "DoneEvent", "ErrorEvent", "TextDeltaEvent", "AssistantMessageEvent",
    "EmbeddingObject", "EmbeddingRequest", "EmbeddingResponse",
    "CostBreakdown", "Tool", "ToolChoice", "JsonSchemaFormat",
]
