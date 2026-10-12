"""Extraction keeps Coding's whole-turn/extension/prompt compatibility surface."""
from __future__ import annotations

from xdog.agent import Agent, AgentConfig
from xdog.agent.compaction import (
    SUMMARY_TEMPLATE,
    estimate_history_tokens,
    estimate_message_tokens,
    estimate_tokens,
    split_messages,
)
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.compaction import utils
from xdog.coding.core.compaction.compaction import _extract_text, compact_messages, prepare_index
from xdog.coding.core.prompt_templates import COMPACTION_SUMMARY_TEMPLATE


def test_utilities_are_shared_without_changing_legacy_estimates():
    assert utils.estimate_tokens is estimate_tokens
    assert utils.estimate_message_tokens is estimate_message_tokens
    assert utils.estimate_history_tokens is estimate_history_tokens
    assert utils.split_messages is split_messages
    assert estimate_tokens("abc") == 0
    assert estimate_message_tokens(AssistantMessage(content=(ToolCall(arguments={"big": "x" * 50000}),))) == 210
    assert SUMMARY_TEMPLATE == COMPACTION_SUMMARY_TEMPLATE
    assert _extract_text(UserMessage(content="text")) == "text"


async def test_whole_latest_user_turn_remains_unsplittable_and_no_summary_call():
    calls = []
    def adapter(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="summary"),)))
    agent = Agent(adapter, config=AgentConfig(model="offline/model", context_window=4000))
    messages = [UserMessage(content="goal")]
    for index in range(6):
        messages.extend([AssistantMessage(content=(ToolCall(id=str(index), name="read"),)),
                         ToolResultMessage(tool_call_id=str(index), tool_name="read",
                                           content=(TextContent(text="x" * 2000),))])
    assert prepare_index(messages, agent, force=True) == 0
    assert await compact_messages(messages, agent, force=True) == messages
    assert not calls


async def test_existing_override_and_prepared_whole_turn_boundary_remain_supported():
    def forbidden(*args):
        raise AssertionError("Override must not call the adapter")
    agent = Agent(forbidden, config=AgentConfig(model="offline/model", context_window=4000))
    messages = [UserMessage(content="old " * 2000), AssistantMessage(content=(TextContent(text="answer"),)),
                UserMessage(content="new task"), AssistantMessage(content=(ToolCall(id="pending", name="read"),))]
    assert prepare_index(messages, agent, force=True) == 2
    result = await compact_messages(messages, agent, prepared_index=2, summary_override="Extension-owned summary")
    assert result == [UserMessage(content=COMPACTION_SUMMARY_TEMPLATE.format(summary="Extension-owned summary")),
                      *messages[2:]]
