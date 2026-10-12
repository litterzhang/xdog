"""Offline tests of the shared policy; Agent/loop commit integration is separate."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from xdog.agent.compaction import (
    TARGET_RATIO,
    TRIGGER_RATIO,
    CompactionBudget,
    CompactionError,
    estimate_input_tokens,
    plan_compaction,
    resolve_compaction_budget,
    safe_cut_indices,
    summarize_compaction,
)
from xdog.agent.core import CustomAgentMessage
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    JsonSchemaFormat,
    Model,
    StreamOptions,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream


def history(batches=8, size=1000):
    messages = [UserMessage(content="Implement the requested task; never discard this goal.")]
    for index in range(batches):
        messages.extend([
            AssistantMessage(content=(ToolCall(id=f"a{index}", name="read", arguments={"path": "a.py"}),
                                      ToolCall(id=f"b{index}", name="read", arguments={"path": "b.py"}))),
            ToolResultMessage(tool_call_id=f"b{index}", tool_name="read", content=(TextContent(text="b" * size),)),
            ToolResultMessage(tool_call_id=f"a{index}", tool_name="read", content=(TextContent(text="a" * size),)),
        ])
    return messages


def prepare(messages=None, **kwargs):
    messages = history() if messages is None else messages
    return plan_compaction(messages, context=Context(messages=tuple(messages)),
                           budget=CompactionBudget(10000, 512, safety_margin=128), force=True, **kwargs)


class Adapter:
    def __init__(self, result=None):
        self.calls = []
        self.reply = result or AssistantMessage(
            content=(TextContent(text="Task state, decisions, paths, next steps."),), usage=Usage(input=17, output=9))

    def __call__(self, model, context, options):
        self.calls.append((model, context, options))
        return EventStream.empty(self.reply)


def test_policy_trigger_target_and_force_are_coding_defaults():
    assert (TRIGGER_RATIO, TARGET_RATIO) == (0.8, 0.5)
    messages = history()
    context = Context(messages=tuple(messages))
    budget = CompactionBudget(10000, 512, safety_margin=128)
    assert plan_compaction(messages, context=context, budget=budget) is None
    plan = plan_compaction(messages, context=context, budget=budget, force=True)
    assert plan is not None and plan.force
    # Force clamps the target to half the history estimate, not half the capacity.
    history_tokens = estimate_input_tokens(context) - estimate_input_tokens(Context())
    assert plan.target_recent_tokens == history_tokens // 2
    assert plan.first_kept_index in safe_cut_indices(messages)
    assert plan.first_kept_index > 1  # A cut INSIDE the only user turn.
    assert plan.kept_indices[0] == 0  # Original task goal survives verbatim.
    assert plan.generation == 1


def test_auto_threshold_uses_actual_input_including_fixed_context():
    messages = history(batches=5)
    budget = CompactionBudget(5000, 500, safety_margin=100)
    context = Context(messages=tuple(messages))
    assert plan_compaction(messages, context=context, budget=budget) is None
    context = replace(context, system_prompt="session skill " * 260)
    plan = plan_compaction(messages, context=context, budget=budget)
    assert plan and plan.estimated_input_before == estimate_input_tokens(context)


@pytest.mark.parametrize("model,options,kwargs", [
    (None, StreamOptions(), {}),
    (Model(context_window=10000), StreamOptions(), {}),
    (None, StreamOptions(max_tokens=100), {"context_window": True}),
    (None, StreamOptions(max_tokens=100), {"context_window": 10000, "output_reserve": 99}),
    (Model(context_window=10000, max_tokens=100), StreamOptions(max_tokens=101), {}),
])
def test_unknown_capacity_and_invalid_reserves_fail_closed(model, options, kwargs):
    with pytest.raises(ValueError):
        resolve_compaction_budget(model, options, **kwargs)


def test_budget_uses_real_input_context_and_reserves_not_200k():
    model = Model(context_window=64000, max_prompt_tokens=48000, max_tokens=8000)
    assert resolve_compaction_budget(model).input_limit == 48000 - 1024
    assert resolve_compaction_budget(model, context_window=20000, output_reserve=3000,
                                     safety_margin=100).input_limit == 16900
    assert resolve_compaction_budget(model, context_window=200000).context_window == 64000
    assert resolve_compaction_budget(context_window=3000, output_reserve=400, safety_margin=100).input_limit == 2500
    with pytest.raises(CompactionError, match="reserves"):
        CompactionBudget(100, 100)


def test_full_estimate_counts_schemas_arguments_thinking_images_skills_not_tool_details():
    empty = estimate_input_tokens(Context())
    context = Context(system_prompt=(SystemPromptBlock(text="s" * 400), SystemPromptBlock(text="skill" * 100)),
                      tools=(Tool(name="read", description="d" * 400, parameters={"schema": "p" * 4000}),),
                      messages=(UserMessage(content="turn skill " * 400),
                                AssistantMessage(content=(ToolCall(id="x", name="read", arguments={"x": "a" * 4000}),
                                                          ThinkingContent(thinking="t" * 4000))),
                                ToolResultMessage(tool_call_id="x", tool_name="read", content=(
                                    ImageContent(data="x" * 4000, mime_type="image/png"),),
                                    details={"secret": "a" * 9999})))
    estimate = estimate_input_tokens(context)
    assert estimate > empty + 4000
    last = replace(context.messages[-1], details=None)
    assert estimate_input_tokens(replace(context, messages=context.messages[:-1] + (last,))) == estimate


@pytest.mark.parametrize("tail", [
    [AssistantMessage(content=(ToolCall(id="pending", name="read"),))],
    [ToolResultMessage(tool_call_id="orphan", tool_name="read")],
    [AssistantMessage(content=(ToolCall(id="p", name="read"),)), UserMessage(content="interleaved")],
    [AssistantMessage(content=(ToolCall(id="p", name="read"), ToolCall(id="p", name="read")))],
    [AssistantMessage(content=(ToolCall(id="p", name="read"),)),
     ToolResultMessage(tool_call_id="p", tool_name="wrong")],
    [AssistantMessage(content=(ToolCall(id="p", name="read"),)),
     ToolResultMessage(tool_call_id="p", tool_name="read"), ToolResultMessage(tool_call_id="p", tool_name="read")],
])
def test_incomplete_or_malformed_tool_batches_are_not_compacted(tail):
    messages = history() + tail
    before = deepcopy(messages)
    with pytest.raises(CompactionError):
        prepare(messages)
    assert messages == before


async def test_success_current_adapter_no_tools_original_export_and_summary_metadata():
    messages = history()
    before = deepcopy(messages)
    plan = prepare(messages)
    adapter = Adapter()
    options = StreamOptions(web_search=True, thinking="low", max_tokens=200)
    result = await summarize_compaction(plan, adapter, "selected/model", options=options,
                                        instructions="Remember failing checks and next steps.")
    assert messages == before
    assert result.usage == adapter.reply.usage
    model, request, used = adapter.calls[0]
    assert model == "selected/model" and request.tools == ()
    assert used.max_tokens == plan.budget.output_reserve
    assert not used.web_search and used.thinking == "low" and used.cancel.is_set()
    assert used.tool_choice is None and used.response_format is None
    assert options.web_search and options.cancel is None
    assert "Remember failing checks" in request.messages[0].content
    assert "never discard this goal" in request.messages[0].content
    assert result.messages[0].content.startswith("<conversation_summary>")
    assert result.messages[1] == messages[0]
    assert result.removed_messages == tuple(messages[index] for index in plan.removed_indices)
    assert result.metadata.estimated_input_after < result.metadata.estimated_input_before
    assert result.metadata.summary_index == 0
    safe_cut_indices(result.messages)
    # Message codec + dataclass metadata are sufficient for the parent snapshot API.
    restored = dicts_to_messages(messages_to_dicts(list(result.messages)))
    assert messages_to_dicts(restored) == messages_to_dicts(list(result.messages))
    safe_cut_indices(restored)
    assert asdict(result.metadata)["removed_indices"] == plan.removed_indices


async def test_effective_system_tools_request_only_and_history_skills_remain():
    skill = UserMessage(content="Active turn skill: obey the test-first workflow.")
    messages = [skill] + history()
    prefix = (UserMessage(content="Request-only active skill body"),)
    context = Context(system_prompt=(SystemPromptBlock(text="stable session skill", cache=True),),
                      tools=(Tool(name="read", parameters={"type": "object"}),), messages=prefix + tuple(messages))
    plan = plan_compaction(messages, context=context, budget=CompactionBudget(10000, 512), force=True,
                           protected_indices=(0,), prefix_messages=prefix)
    result = await summarize_compaction(plan, Adapter(), "model")
    assert skill in result.messages and skill not in result.removed_messages
    assert result.context.system_prompt == context.system_prompt and result.context.tools == context.tools
    assert result.context.messages[:1] == prefix
    assert prefix[0] not in result.messages


async def test_custom_history_requires_exact_per_message_projection_and_exports_original():
    custom = CustomAgentMessage(type="host-note", data={"message": "Visible context", "secret": "Not model input"})
    messages = [custom] + history()
    with pytest.raises(CompactionError, match="projections"):
        prepare(messages)
    projected = [(UserMessage(content="Visible context"),)] + [(msg,) for msg in messages[1:]]
    context = Context(messages=tuple(msg for group in projected for msg in group))
    plan = plan_compaction(messages, context=context, projections=projected,
                           budget=CompactionBudget(10000, 512), force=True)
    adapter = Adapter()
    result = await summarize_compaction(plan, adapter, "model")
    assert custom in result.removed_messages
    assert "Visible context" in adapter.calls[0][1].messages[0].content
    assert "Not model input" not in adapter.calls[0][1].messages[0].content
    with pytest.raises(CompactionError, match="does not match"):
        plan_compaction(messages, context=replace(context, messages=context.messages[:-1]), projections=projected,
                        budget=plan.budget, force=True)


async def test_plan_owns_payloads_and_does_not_alias_archived_originals():
    messages = history()
    plan = prepare(messages)
    messages[1].content[0].arguments["path"] = "changed after planning"
    result = await summarize_compaction(plan, Adapter(), "model")
    assert result.removed_messages[0].content[0].arguments["path"] == "a.py"
    result.removed_messages[0].content[0].arguments["path"] = "changed result"
    assert plan.original_messages[1].content[0].arguments["path"] == "a.py"


@pytest.mark.parametrize("reply", [
    AssistantMessage(),
    AssistantMessage(content=(TextContent(text="partial"),), stop_reason="length", usage=Usage(input=7)),
    AssistantMessage(content=(TextContent(text="failed"),), stop_reason="error", error_message="PRIVATE"),
    AssistantMessage(content=(ToolCall(id="bad", name="bash"),)),
    AssistantMessage(content=(TextContent(text="too big" * 20000),)),
])
async def test_bad_summaries_retain_state_and_do_not_retry(reply):
    messages = history()
    original = deepcopy(messages)
    adapter = Adapter(reply)
    with pytest.raises(CompactionError, match="original history retained") as failure:
        await summarize_compaction(prepare(messages), adapter, "model")
    assert failure.value.usage == reply.usage
    assert "PRIVATE" not in str(failure.value)
    assert messages == original and len(adapter.calls) == 1


async def test_summary_request_capacity_is_checked_before_adapter_call():
    messages = [UserMessage(content="x" * 50000), UserMessage(content="recent request")]
    plan = prepare(messages)
    adapter = Adapter()
    with pytest.raises(CompactionError, match="Summary request exceeds"):
        await summarize_compaction(plan, adapter, "model")
    assert not adapter.calls


def test_no_safe_cut_and_required_context_overflow_fail_without_destructive_pruning():
    with pytest.raises(CompactionError, match="No safe boundary"):
        prepare([UserMessage(content="single large request" * 2000)])
    messages = history()
    with pytest.raises(CompactionError, match="system/tools/skills"):
        plan_compaction(messages, context=Context(messages=tuple(messages), system_prompt="s" * 60000),
                        budget=CompactionBudget(10000, 512), force=True)


async def test_cancellation_closes_selected_stream_and_retains_state():
    entered, closed = asyncio.Event(), asyncio.Event()
    seen = []

    def adapter(model, context, options):
        seen.append(options)
        async def wait():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield None
            finally:
                closed.set()
        return EventStream.from_async_generator(wait())

    plan = prepare()
    task = asyncio.create_task(summarize_compaction(plan, adapter, "model"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and seen[0].cancel.is_set()
    assert len(plan.original_messages) == 25


async def test_repeated_long_turn_compaction_can_continue_at_completed_batches():
    messages = history()
    generation = 0
    for cycle in range(3):
        plan = prepare(messages, generation=generation)
        result = await summarize_compaction(plan, Adapter(), "model")
        assert result.metadata.generation == generation + 1
        generation = result.metadata.generation
        messages = list(result.messages)
        # Simulate the next completed batch in the SAME logical user turn.
        messages.extend([AssistantMessage(content=(ToolCall(id=f"new{cycle}", name="read"),)),
                         ToolResultMessage(tool_call_id=f"new{cycle}", tool_name="read",
                                           content=(TextContent(text="new material " * 400),))])
        safe_cut_indices(messages)
    assert generation == 3


async def test_response_schema_overhead_and_candidate_estimate_are_not_omitted():
    messages = history()
    context = Context(messages=tuple(messages))
    options = StreamOptions(response_format=JsonSchemaFormat.from_schema({"description": "x" * 8000}))
    plan = plan_compaction(messages, context=context, budget=CompactionBudget(10000, 512),
                           force=True, options=options)
    assert plan.estimated_input_before == estimate_input_tokens(context, options)
    assert plan.extra_input_tokens > 2000
    result = await summarize_compaction(plan, Adapter(), "model", options=options)
    assert result.metadata.estimated_input_after == estimate_input_tokens(result.context, options)


async def test_async_adapter_and_cleanup_failure_do_not_commit_or_lose_usage():
    async def adapter(*args):
        return EventStream.empty(AssistantMessage(content=(TextContent(text="clean summary"),)))
    result = await summarize_compaction(prepare(), adapter, "model")
    assert result.summary == "clean summary"

    class BrokenIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            raise RuntimeError("PRIVATE CLEANUP FAILURE")

    class BrokenStream:
        def __aiter__(self):
            return BrokenIterator()

        async def result(self):
            return AssistantMessage(content=(TextContent(text="clean summary"),), usage=Usage(input=31))

    plan = prepare()
    with pytest.raises(CompactionError, match="original history retained") as failure:
        await summarize_compaction(plan, lambda *args: BrokenStream(), "model")
    assert failure.value.usage.input == 31
    assert "PRIVATE" not in str(failure.value)
    assert len(plan.original_messages) == 25


async def test_current_adapter_failure_does_not_fallback_and_focus_validation_is_preflight():
    calls = []
    def adapter(*args):
        calls.append(args)
        raise OSError("PRIVATE CREDENTIAL")
    plan = prepare()
    with pytest.raises(ValueError, match="instructions"):
        await summarize_compaction(plan, adapter, "model", instructions="\x00")
    assert not calls
    with pytest.raises(CompactionError) as failure:
        await summarize_compaction(plan, adapter, "model")
    assert len(calls) == 1 and "PRIVATE" not in str(failure.value)


async def test_export_reconstructs_original_history_without_retaining_an_archive_in_new_state():
    original = history()
    result = await summarize_compaction(prepare(original), Adapter(), "model")
    reconstructed = dict(zip(result.metadata.removed_indices, result.removed_messages, strict=True))
    reconstructed.update(zip(result.metadata.kept_indices, result.messages[1:], strict=True))
    assert [reconstructed[index] for index in range(result.metadata.original_count)] == original
    assert len(result.messages) < len(original)


@pytest.mark.parametrize("fails", [False, True])
async def test_summary_response_aclose_is_awaited_before_candidate_return(fails):
    closed = []
    class Response(EventStream):
        async def aclose(self):
            closed.append(True)
            await super().aclose()
            if fails:
                raise RuntimeError("PRIVATE RESPONSE CLEANUP ERROR")
    def adapter(*args):
        return Response.empty(AssistantMessage(content=(TextContent(text="valid summary"),), usage=Usage(output=5)))
    if fails:
        with pytest.raises(CompactionError, match="cleanup failed") as failure:
            await summarize_compaction(prepare(), adapter, "model")
        assert failure.value.usage.output == 5
    else:
        result = await summarize_compaction(prepare(), adapter, "model")
        assert result.usage.output == 5
    assert closed == [True]
