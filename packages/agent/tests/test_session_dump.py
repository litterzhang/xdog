"""`Agent.dump()` / `restore()` — the session round trip.

An `Agent` instance is a session; these are its two projections. The fidelity is
the whole point and it is easy to lose silently: an earlier implementation in
another package flattened message content to a string, which dropped every
image, every thinking block including its signature, and all but the first part
of a tool result. Nothing failed — the restored agent was simply having a
different conversation from the one that was saved.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from xdog.agent.agent import Agent
from xdog.agent.core import AgentConfig
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    StreamOptions,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


async def _never_called(*args: object, **kwargs: object) -> object:  # pragma: no cover
    raise AssertionError("these tests must not reach a model")


def _agent(**config: Any) -> Agent:
    return Agent(_never_called, config=AgentConfig(**config))


def _rich_history() -> list[Any]:
    """One message of every shape that has somewhere to lose information."""
    return [
        UserMessage(
            content=(
                TextContent(text="look at this"),
                ImageContent(data="BASE64PAYLOAD", mime_type="image/png"),
            )
        ),
        AssistantMessage(
            content=(
                ThinkingContent(thinking="weighing it up", thinking_signature="SIG-ABC"),
                TextContent(text="I see a cat"),
                ToolCall(id="t1", name="zoom", arguments={"factor": 2}),
            ),
            usage=Usage(input=10, output=20),
        ),
        ToolResultMessage(
            tool_call_id="t1",
            tool_name="zoom",
            content=(TextContent(text="part one"), TextContent(text="part two")),
        ),
    ]


def test_a_session_round_trip_loses_nothing() -> None:
    """The regression test for the bug this whole module exists to prevent."""
    source = _agent(model="gpt-5.6-sol", system_prompt="be terse")
    source.replace_messages(_rich_history())

    target = _agent()
    target.restore(source.dump())

    user, assistant, tool_result = target.messages

    # An image survives — it is not text and a string-flattening dump drops it.
    assert [type(p).__name__ for p in user.content] == ["TextContent", "ImageContent"]
    assert user.content[1].data == "BASE64PAYLOAD"

    # The thinking signature survives. For extended reasoning it is the
    # continuity token; without it a resumed turn starts a new chain.
    thinking = next(p for p in assistant.content if isinstance(p, ThinkingContent))
    assert thinking.thinking_signature == "SIG-ABC"

    call = next(p for p in assistant.content if isinstance(p, ToolCall))
    assert (call.id, call.name, dict(call.arguments)) == ("t1", "zoom", {"factor": 2})

    # Both parts of the tool result survive, not just the first.
    assert [p.text for p in tool_result.content] == ["part one", "part two"]


def test_a_dump_is_plain_json() -> None:
    """flow checkpoints it with `json.dump` and no `default=` handler."""
    source = _agent(model="m", system_prompt="p")
    source.replace_messages(_rich_history())

    assert json.loads(json.dumps(source.dump())) == source.dump()


def test_what_the_agent_was_told_round_trips_too() -> None:
    """A session is not just the history — restoring one must restore the setup."""
    source = _agent(
        model="claude-opus-4-6",
        system_prompt="you are a reviewer",
        options=StreamOptions(thinking="high", temperature=0.2, max_tokens=4096),
    )

    target = _agent()
    target.restore(source.dump())

    assert target.state.model == "claude-opus-4-6"
    assert target.state.system_prompt == "you are a reviewer"
    assert target.options.thinking == "high"
    assert target.options.temperature == 0.2
    assert target.options.max_tokens == 4096


def test_a_block_system_prompt_round_trips_with_its_cache_markers() -> None:
    """The block form is how a long prefix gets marked cacheable; losing the
    flag turns a cached prefix into a re-billed one."""
    source = _agent()
    source.set_system_prompt(
        (SystemPromptBlock(text="long shared preamble", cache=True),
         SystemPromptBlock(text="per-run detail")),
    )

    target = _agent()
    target.restore(source.dump())

    assert target.state.system_prompt == (
        SystemPromptBlock(text="long shared preamble", cache=True),
        SystemPromptBlock(text="per-run detail"),
    )


def test_a_live_cancel_handle_is_not_part_of_the_session() -> None:
    """`StreamOptions.cancel` is an asyncio.Event. A session is a value that
    gets written to a file; a handle to a running thing is not."""
    import asyncio

    source = _agent(options=StreamOptions(cancel=asyncio.Event(), thinking="low"))

    dumped = source.dump()

    assert "cancel" not in dumped
    json.dumps(dumped)  # would raise if the Event had come along
    assert dumped["options"]["thinking"] == "low"


@pytest.mark.parametrize('patch', [{}, {'messages': []}, {'messages': None}, {'system_prompt': 'old'}])
def test_unversioned_dump_is_rejected_atomically(patch):
    target = _agent(model='m', system_prompt='keep me')
    target.replace_messages([UserMessage(content='hello')])
    before = target.dump()
    with pytest.raises(ValueError):
        target.restore(patch)
    assert target.dump() == before
