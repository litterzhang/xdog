from dataclasses import FrozenInstanceError

import pytest
from xdog.agent import Agent, AgentConfig, inspect_context
from xdog.agent.compaction import CompactionBudget, estimate_input_tokens
from xdog.ai.types import AssistantMessage, Context, ImageContent, TextContent, Tool, UserMessage
from xdog.ai.utils.event_stream import EventStream


def test_breakdown_is_exactly_the_shared_estimator_and_never_retains_context():
    context = Context(
        system_prompt="中文 system and placed skills",
        messages=(
            UserMessage(content=(TextContent(text="history"), ImageContent(data="aGVsbG8=", mime_type="image/png"))),
        ),
        tools=(Tool(name="example", description="doc", parameters={"type": "object"}),),
    )
    report = inspect_context(context, model="offline")
    assert report.estimated_tokens == estimate_input_tokens(context)
    assert report.estimated_tokens == (
        report.system_tokens
        + report.tool_schema_tokens
        + report.history_tokens
        + report.image_tokens
        + report.request_overhead_tokens
    )
    assert report.fits is None and report.remaining_tokens is None and report.image_tokens >= 200
    bounded = report.with_budget(CompactionBudget(context_window=100, output_reserve=20, safety_margin=0))
    assert bounded.input_limit == 80 and bounded.fits is False
    assert "placed skills" not in repr(report)
    with pytest.raises(FrozenInstanceError):
        report.model = "changed"


async def test_last_report_observes_post_transform_request_without_extra_model_calls():
    observed = []

    async def transform(messages):
        return [*messages, UserMessage(content="host-added context " * 50)]

    def stream(model, context, options):
        observed.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    agent = Agent(stream, config=AgentConfig(model="offline"), transform_context=transform)
    try:
        assert agent.last_context_report is None
        await (await agent.prompt("input")).collect()
        report = agent.last_context_report
        assert report.model == "offline" and len(observed) == 1
        assert report.estimated_tokens == estimate_input_tokens(observed[0])
        assert report.message_count == len(observed[0].messages)
    finally:
        await agent.aclose()
