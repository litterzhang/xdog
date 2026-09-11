import json

import pytest
from xdog.ai.protocols.anthropic_messages import context_to_anthropic
from xdog.ai.protocols.openai_responses import context_to_responses_input
from xdog.ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)


def test_anthropic_serializes_signed_thinking_as_native_thinking():
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(thinking="Analyze this.", thinking_signature="signed-thinking"),
        )),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages == [{
        "role": "assistant",
        "content": [{
            "type": "thinking",
            "thinking": "Analyze this.",
            "signature": "signed-thinking",
        }],
    }]


def test_anthropic_serializes_unsigned_nonempty_thinking_as_text():
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(thinking="Analyze this."),
        )),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages == [{
        "role": "assistant",
        "content": [{
            "type": "text",
            "text": "Analyze this.",
        }],
    }]


def test_openai_responses_restores_reasoning_item_from_thinking_signature():
    reasoning_item = {
        "id": "rs_123",
        "type": "reasoning",
        "summary": [{
            "type": "summary_text",
            "text": "Checked the facts.",
        }],
        "encrypted_content": "encrypted-payload",
        "status": "completed",
    }
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(
                thinking="Checked the facts.",
                thinking_signature=json.dumps(reasoning_item),
            ),
        )),
    ))

    items = context_to_responses_input(
        context,
        Model(api="openai-responses", reasoning=True),
    )

    assert items == [reasoning_item]


@pytest.mark.parametrize("opaque_id", ["x" * 65, "雪" * 65, "", None, 123])
def test_openai_responses_skips_non_replayable_reasoning_item_id(
    opaque_id: object, caplog: pytest.LogCaptureFixture,
) -> None:
    reasoning_item = {
        "id": opaque_id,
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "encrypted-payload",
    }
    context = Context(messages=(AssistantMessage(content=(
        ThinkingContent(thinking_signature=json.dumps(reasoning_item)),
        TextContent(text="Visible answer"),
    )),))

    items = context_to_responses_input(context, Model(api="openai-responses", reasoning=True))

    assert not caplog.records  # Routine history filtering must not write warnings into the TUI.

    assert items == [{
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Visible answer", "annotations": []}],
        "status": "completed",
    }]


def test_openai_responses_inserts_output_for_unmatched_tool_calls():
    context = Context(messages=(
        AssistantMessage(content=(
            ToolCall(id="call_1|item_1", name="bash", arguments={}),
            ToolCall(id="call_2|item_2", name="bash", arguments={}),
        )),
        ToolResultMessage(
            tool_call_id="call_2|item_2",
            tool_name="bash",
            content=(TextContent(text="second completed"),),
        ),
    ))

    items = context_to_responses_input(
        context,
        Model(api="openai-responses"),
    )

    outputs = {
        item["call_id"]: item["output"]
        for item in items
        if item.get("type") == "function_call_output"
    }
    assert outputs == {
        "call_1": "Tool execution cancelled",
        "call_2": "second completed",
    }


def test_openai_responses_skips_duplicate_tool_results():
    context = Context(messages=(
        AssistantMessage(content=(
            ToolCall(id="call_1|item_1", name="bash", arguments={}),
        )),
        ToolResultMessage(
            tool_call_id="call_1|item_1",
            tool_name="bash",
            content=(TextContent(text="first"),),
        ),
        ToolResultMessage(
            tool_call_id="call_1|stale-item",
            tool_name="bash",
            content=(TextContent(text="duplicate"),),
        ),
    ))

    items = context_to_responses_input(
        context,
        Model(api="openai-responses"),
    )

    outputs = [
        item for item in items
        if item.get("type") == "function_call_output"
    ]
    assert outputs == [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "first",
    }]


def test_openai_responses_does_not_repair_suppressed_error_assistant():
    context = Context(messages=(
        AssistantMessage(
            content=(ToolCall(id="call_1|item_1", name="bash", arguments={}),),
            stop_reason="error",
            error_message="request failed",
        ),
    ))

    assert context_to_responses_input(
        context,
        Model(api="openai-responses"),
    ) == []


def test_anthropic_normalizes_tool_call_and_result_ids_together():
    context = Context(messages=(
        AssistantMessage(content=(
            ToolCall(id="call_123|item_456", name="read_file", arguments={}),
        )),
        ToolResultMessage(
            tool_call_id="call_123|item_456",
            tool_name="read_file",
            content=(TextContent(text="done"),),
        ),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages[0]["content"][0]["id"] == "call_123"
    assert messages[1]["content"][0]["tool_use_id"] == "call_123"
