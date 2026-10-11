"""agent - Agent runtime with tool calling and state management.

Quick start::

    import xdog.ai as ai
    from xdog.agent import Agent, AgentConfig
    from xdog.agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

    agent = Agent(
        stream_fn_from_provider(ai.provider("copilot")),
        config=AgentConfig(model="claude-sonnet-4.5", system_prompt="You are helpful."),
        tools=[my_tool],
    )

    stream = await agent.prompt("Hello!")
    async for event in stream:
        print(event.type)
"""

# -- Core -------------------------------------------------------------------
# -- Agent class ------------------------------------------------------------
from xdog.agent.agent import Agent, EventListener

# -- Agent loop functions ---------------------------------------------------
from xdog.agent.agent_loop import (
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,
)
from xdog.agent.core import (
    AgentConfig,
    AgentContext,
    AgentMessage,
    AgentState,
    AgentTool,
    AgentToolResult,
    AgentToolUpdateCallback,
    CatalogStreamFn,
    CustomAgentMessage,
    EmbedFn,
    QueueMode,
    StreamFn,
    ToolExecutionMode,
    WebSearchFn,
)

# -- Event stream -----------------------------------------------------------
from xdog.agent.event_stream import AgentEventStream

# -- Events -----------------------------------------------------------------
from xdog.agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    InputTransformedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

# -- Helpers ------------------------------------------------
from xdog.agent.helpers import (
    embed_fn_from_provider,
    stream_fn_from_provider,
    web_search_fn_from_provider,
)
from xdog.agent.history import HistoryPoint, history_points, validate_checkpoint
from xdog.agent.input_queue import Delivery, QueuedInput

# -- ToolDef framework -----------------------------------------------------
from xdog.agent.tool_def import Param, ToolDef, action

# -- Built-in tools ---------------------------------------------------------
from xdog.agent.tools import (
    create_bash_tool,
    create_current_time_tool,
    create_embed_tool_from_fn,
    create_filesystem_tool,
    create_web_search_tool_from_fn,
)

# -- Tool SPI ---------------------------------------------------------------
from xdog.agent.tools.registry import (
    get_registered_tools,
    register_tool,
    registered_tool_names,
    unregister_tool,
)

# -- Types (hooks, callbacks, loop config) ----------------------------------
from xdog.agent.types import (
    AfterToolCallContext,
    AfterToolCallFn,
    AfterToolCallResult,
    AgentEventSink,
    AgentLoopConfig,
    BeforeToolCallContext,
    BeforeToolCallFn,
    BeforeToolCallResult,
    ConvertToLlmFn,
    GetMessagesFn,
    HandledInput,
    InputTransformFn,
    TransformContextFn,
)

__all__ = [
    # Core
    "AgentConfig",
    "AgentContext",
    "AgentMessage",
    "AgentState",
    "AgentTool",
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "CatalogStreamFn",
    "CustomAgentMessage",
    "EmbedFn",
    "QueueMode",
    "StreamFn",
    "ToolExecutionMode",
    "WebSearchFn",
    # Events
    "AgentEndEvent",
    "AgentEvent",
    "AgentStartEvent",
    "MessageEndEvent",
    "InputConsumedEvent",
    "InputTransformedEvent",
    "InputRejectedEvent",
    "InputHandledEvent",
    "Delivery",
    "QueuedInput",
    "HistoryPoint",
    "history_points",
    "validate_checkpoint",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    # Types (hooks, callbacks, loop config)
    "AfterToolCallContext",
    "AfterToolCallFn",
    "AfterToolCallResult",
    "AgentEventSink",
    "AgentLoopConfig",
    "BeforeToolCallContext",
    "BeforeToolCallFn",
    "BeforeToolCallResult",
    "ConvertToLlmFn",
    "GetMessagesFn",
    "TransformContextFn",
    "InputTransformFn",
    "HandledInput",
    # Agent class
    "Agent",
    "EventListener",
    # Loop functions
    "agent_loop",
    "agent_loop_continue",
    "run_agent_loop",
    "run_agent_loop_continue",
    # Event stream
    "AgentEventStream",
    # Helpers
    "stream_fn_from_provider",
    "web_search_fn_from_provider",
    "embed_fn_from_provider",
    # ToolDef framework
    "ToolDef",
    "Param",
    "action",
    # Built-in tools
    "create_bash_tool",
    "create_current_time_tool",
    "create_embed_tool_from_fn",
    "create_filesystem_tool",
    "create_web_search_tool_from_fn",
    # Tool SPI
    "register_tool",
    "unregister_tool",
    "get_registered_tools",
    "registered_tool_names",
]
