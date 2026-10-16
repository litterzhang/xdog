"""Frozen per-input snapshots and a fixed shared turn envelope."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from xdog.agent.core import AgentMessage
from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.ai.types import ImageContent, Message

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

MAX_BATCH_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TurnInput:
    value: SessionInput
    images: tuple[ImageContent, ...] = ()
    input_origin: str | None = None
    raw_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'value', decode_input(input_data(self.value)))
        object.__setattr__(self, 'images', tuple(self.images))


def input_size(item: TurnInput) -> int:
    """Count actual member input, not a second copy of the shared batch envelope."""
    data = input_data(item.value)
    if item.raw_text is not None:
        data['instruction'] = item.raw_text
    if item.images:
        # Count inline content too; images have their own provider count/MIME validation.
        data['images'] = [asdict(image) for image in item.images]
    return len(json.dumps(data, ensure_ascii=True, allow_nan=False).encode())


@dataclass(frozen=True)
class TurnBatch:
    id: str
    session_id: str
    branch_id: str
    generation: str
    items: tuple[TurnInput, ...]

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(item.value.id for item in self.items)


def agent_message(item: TurnInput) -> AgentMessage:
    """Shared pure formatting for selection estimates and actual prompt admission."""
    from xdog.agent.session_input import encode_input
    from xdog.ai.types import TextContent, UserMessage

    origin = item.input_origin or ('user' if item.value.origin == 'human' else 'session_input')
    if origin == 'session_input':
        return encode_input(item.value)
    if origin == 'custom_context':
        raise ValueError('Canonical continuation is a non-batchable control')
    text = item.raw_text if item.raw_text is not None else item.value.instruction
    return UserMessage(content=(TextContent(text=text), *item.images))


def project_message(message: AgentMessage) -> Message | None:
    """Project known canonical history without calling dynamic extension hooks."""
    from xdog.agent.extensions.context_message import llm_message as context_message
    from xdog.agent.external_event import llm_message as historical_event
    from xdog.agent.session_input import llm_message as session_message
    from xdog.ai.types import AssistantMessage, ToolResultMessage, UserMessage

    if isinstance(message, (AssistantMessage, ToolResultMessage, UserMessage)):
        return message
    return context_message(message) or historical_event(message) or session_message(message)


def available_input_tokens(session: AgentSession) -> int:
    """Estimate cached known capacity; final prepared request remains authoritative."""
    from xdog.agent.compaction import estimate_input_tokens
    from xdog.ai.types import Context, Tool

    limits = [value for value in (session.context_limit, session.agent.context_window) if value > 0]
    window = min(limits)
    model = session.model_info
    output = session.agent.options.max_tokens or (model.max_tokens if model is not None else 0)
    prompt_limits = [value for value in (session.max_prompt_tokens, session.agent.max_prompt_tokens) if value > 0]
    limit = min([max(0, window - output), *prompt_limits])
    messages = tuple(projected for message in session.messages
                     if (projected := project_message(message)) is not None)
    tools = tuple(Tool(name=tool.name, description=tool.description, parameters=tool.parameters)
                  for tool in session.agent.active_tools)
    context = Context(system_prompt=session.agent.state.system_prompt, messages=messages, tools=tools)
    return max(0, limit - estimate_input_tokens(context, session.agent.options))


def member_tokens(item: TurnInput) -> int:
    from xdog.agent.compaction import estimate_input_message_tokens

    projected = project_message(agent_message(item))
    return estimate_input_message_tokens(projected) if projected is not None else 0
