"""Typed additive Agent options; repositories remain explicit assembly inputs."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, TypedDict

from xdog.agent.core import EmbedFn, WebSearchFn
from xdog.agent.input_queue import QueuedInput
from xdog.agent.types import (
    AfterToolCallFn,
    BeforeToolCallFn,
    ConvertToLlmFn,
    InputTransformFn,
    ToolExecutionGuard,
    TransformContextFn,
)

if TYPE_CHECKING:
    from xdog.agent.child_policy import ParentHandle
    from xdog.agent.mcp import McpRegistry
    from xdog.agent.skills.types import SkillPlacement


class AgentOptions(TypedDict, total=False):
    tool_ctx: dict[str, Any] | None
    prepared_skills: SkillPlacement | None
    embed_fn: EmbedFn | None
    web_search_fn: WebSearchFn | None
    convert_to_llm: ConvertToLlmFn | None
    transform_context: TransformContextFn | None
    before_tool_call: BeforeToolCallFn | None
    tool_execution_guard: ToolExecutionGuard | None
    after_tool_call: AfterToolCallFn | None
    input_transform: InputTransformFn | None
    input_settled: Callable[[tuple[QueuedInput, ...]], Awaitable[None]] | None
    parent: ParentHandle | None
    mcp_registry: McpRegistry | None
    custom_message_validators: Mapping[str, Callable[[dict[str, Any]], None]] | None
