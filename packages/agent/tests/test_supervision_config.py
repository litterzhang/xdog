from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.supervision import ChildSupervisor, child_prompt
from xdog.agent.supervision_config import ChildConfig, parse_child_start
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize("bad", [
    {"extra": True}, {"label": "bad\nlabel"}, {"label": "x" * 121}, {"instructions": "x" * 16001},
    {"max_model_calls": True}, {"max_model_calls": 17}, {"max_tokens": 0}, {"max_tokens": 32001},
    {"inherit_context": 1}, {"timeout": float("nan")}, {"timeout": 601}, {"timeout": False},
    {"tools": "bash"}, {"tools": ["bash", "bash"]}, {"tools": ["../bash"]}, {"tools": ["agent_spawn"]},
])
def test_configuration_rejects_malformed_or_broader_authority(bad):
    with pytest.raises(ValueError):
        ChildConfig.from_dict(bad)


def test_flags_preserve_task_suffix_and_fresh_subset_and_budget_selection():
    task, access, config = parse_child_start('--inherit --label "code review" --instructions "Inspect only" '
        '--tools filesystem,current_time --timeout 15 --calls 2 --tokens 600 --fresh -- '
        'Check "literal quotes"\nand files')
    assert task == 'Check "literal quotes"\nand files' and access == "inherit"
    assert config.label == "code review" and config.instructions == "Inspect only"
    assert config.tools == ("filesystem", "current_time") and not config.inherit_context
    assert config.timeout == 15 and config.max_model_calls == 2 and config.max_tokens == 600
    assert parse_child_start('A "quoted" plain task')[0] == 'A "quoted" plain task'
    assert parse_child_start('--tools none task')[2].tools == ()
    assert parse_child_start('-- --unknown literal task')[0] == '--unknown literal task'


@pytest.mark.parametrize("value", ["--unknown task", "--calls bad task", "--inherit", "--label", "--label 'broken",
                                    "--fresh --fresh task", "--tokens 0 task", "--tools agent_control task"])
def test_invalid_flags_never_admit_child(value):
    with pytest.raises(ValueError):
        parse_child_start(value)


async def test_selected_tools_and_specialist_prompt_are_applied_and_persisted_without_parent_mutation(tmp_path):
    contexts, limits = [], []
    parent_prompt = "PARENT-RESTRICTIONS"
    async def execute(*args, **kwargs):
        return AgentToolResult()
    def factory(spec, publish):
        def stream(model, context, options):
            contexts.append(context)
            limits.append(options.max_tokens)
            return EventStream.empty(AssistantMessage(content=(TextContent(text="result"),)))
        agent = Agent(stream, config=AgentConfig(model="test/dummy", system_prompt=child_prompt(parent_prompt, spec)),
                      tools=[AgentTool(name="read", execute=execute), AgentTool(name="write", execute=execute)])
        return agent
    store = ChildStore(tmp_path / "children")
    owner = ChildSupervisor(factory, store.save, begin=store.begin, records=store.load())
    try:
        config = {"label": "Reviewer", "instructions": "SPECIALIZED-REVIEW", "tools": ["read"],
                  "inherit_context": False, "timeout": 10, "max_model_calls": 2, "max_tokens": 1234}
        child = await owner.start("task", config=config)
        result = await owner.control("wait", child)
        assert result["status"] == "completed" and result["config"]["label"] == "Reviewer"
        assert [tool.name for tool in contexts[0].tools] == ["read"] and limits == [1234]
        assert "PARENT-RESTRICTIONS" in str(contexts[0].system_prompt)
        assert "SPECIALIZED-REVIEW" in str(contexts[0].system_prompt)
        assert parent_prompt == "PARENT-RESTRICTIONS"
        restored = ChildSupervisor(factory, records=store.load())
        try:
            record = await restored.control("inspect", child)
            assert record["config"] == result["config"] and not restored.active
        finally:
            await restored.aclose()
    finally:
        await owner.aclose()


async def test_lowered_model_call_budget_and_unknown_subset_fail_before_unbounded_work():
    calls = []
    async def execute(*args, **kwargs):
        return AgentToolResult()
    def factory(spec, publish):
        def stream(model, context, options):
            calls.append(options.max_tokens)
            return EventStream.empty(AssistantMessage(content=(ToolCall(id="read", name="read", arguments={}),)))
        return Agent(stream, config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    owner = ChildSupervisor(factory)
    try:
        unknown = await owner.start("task", config={"tools": ["missing"]})
        result = await owner.control("wait", unknown)
        assert result["status"] == "failed" and "unavailable" in result["error"] and not calls
        bounded = await owner.start("task", config={"max_model_calls": 2, "max_tokens": 500})
        result = await owner.control("wait", bounded)
        assert result["status"] == "failed" and "budget" in result["error"]
        assert result["calls"] == 2 and calls == [500, 500]
    finally:
        await owner.aclose()


async def test_nested_config_merges_legacy_explicit_context_and_timeout_controls():
    specs = []
    def factory(spec, publish):
        specs.append(spec)
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(factory)
    try:
        child = await owner.start("task", timeout=5, inherit_context=False, config={"label": "Reviewer"})
        await owner.control("wait", child)
        assert specs[0].timeout == 5 and not specs[0].inherit_context and specs[0].label == "Reviewer"
        spawn = owner.tools()[0]
        result = await spawn.execute("call", {"task": "task", "config": {"tools": [], "max_tokens": 700}})
        record = await owner.control("wait", result.content[0].text)
        assert record["config"]["tools"] == [] and record["config"]["max_tokens"] == 700
    finally:
        await owner.aclose()
