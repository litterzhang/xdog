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


def test_flags_preserve_task_suffix_and_fresh_subset():
    task, access, config = parse_child_start('--inherit --label "code review" --instructions "Inspect only" '
        '--tools filesystem,current_time --fresh -- '
        'Check "literal quotes"\nand files')
    assert task == 'Check "literal quotes"\nand files' and access == "inherit"
    assert config.label == "code review" and config.instructions == "Inspect only"
    assert config.tools == ("filesystem", "current_time") and not config.inherit_context
    assert parse_child_start('A "quoted" plain task')[0] == 'A "quoted" plain task'
    assert parse_child_start('--tools none task')[2].tools == ()
    assert parse_child_start('-- --unknown literal task')[0] == '--unknown literal task'


def test_invalid_flags_never_admit_child() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            parse_child_start(value)

    for value in [
        "--unknown task",
        "--calls bad task",
        "--inherit",
        "--label",
        "--label 'broken",
        "--fresh --fresh task",
        "--tokens 0 task",
        "--tools agent_control task",
    ]:
        _check_case(value)


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
                  "inherit_context": False}
        child = await owner.start("task", config=config)
        result = await owner.control("wait", child)
        assert result["status"] == "completed" and result["config"]["label"] == "Reviewer"
        assert [tool.name for tool in contexts[0].tools] == ["read"] and limits == [None]
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


async def test_unknown_tool_subset_fails_before_model_work():
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
    finally:
        await owner.aclose()


async def test_nested_config_merges_explicit_context_controls():
    specs = []
    def factory(spec, publish):
        specs.append(spec)
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(factory)
    try:
        child = await owner.start("task", inherit_context=False, config={"label": "Reviewer"})
        await owner.control("wait", child)
        assert not specs[0].inherit_context and specs[0].label == "Reviewer"
        spawn = owner.tools()[0]
        result = await spawn.execute("call", {"task": "task", "config": {"tools": []}})
        record = await owner.control("wait", result.content[0].text)
        assert record["config"]["tools"] == []
    finally:
        await owner.aclose()


@pytest.mark.parametrize("key,value,flag", [
    ("timeout", 120, "--timeout 120"),
    ("max_model_calls", 16, "--calls 16"),
    ("max_tokens", 32000, "--tokens 32000"),
])
async def test_removed_budget_controls_are_not_exposed_or_silently_accepted(key, value, flag):
    import inspect

    from xdog.agent.supervision_config import ChildPresets
    assert key not in ChildConfig().to_dict()
    assert key not in inspect.signature(ChildSupervisor.start).parameters
    with pytest.raises(ValueError):
        parse_child_start(flag + " task")
    with pytest.raises(ValueError):
        ChildPresets({"old": {"config": {key: value}}})
    owner = ChildSupervisor(lambda *args: pytest.fail("Must not admit invalid config"))
    try:
        spawn = owner.tools()[0]
        properties = spawn.parameters["properties"]
        assert key not in properties and key not in properties["config"]["properties"]
        with pytest.raises(ValueError):
            await spawn.execute("call", {"task": "task", key: value})
        with pytest.raises(ValueError):
            await owner.start("task", config={key: value})
        assert not owner.active and owner._thread is None
    finally:
        await owner.aclose()


def test_historical_preset_snapshot_budgets_are_inert_and_unknown_fields_still_rejected():
    from xdog.agent.supervision_config import ChildPresets
    snapshot = [{"name": "old", "description": "", "config": {
        "timeout": 1, "max_model_calls": 1, "max_tokens": 1, "instructions": "Preserved"}}]
    restored = ChildPresets.from_snapshot(snapshot).resolve({"preset": "old"})
    assert restored.instructions == "Preserved" and "max_tokens" not in restored.to_dict()
    with pytest.raises(ValueError):
        ChildConfig.from_record({"timeout": 1, "permission_mode": "allow-all"})
