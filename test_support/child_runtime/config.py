from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.supervision import child_prompt
from xdog.agent.supervision_config import ChildConfig, parse_child_start
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


class TestChildConfig:
    async def test_selected_tools_and_specialist_prompt_are_applied_and_persisted_without_parent_mutation(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        contexts, limits = [], []
        parent_prompt = "PARENT-RESTRICTIONS"

        async def execute(*args, **kwargs):
            return AgentToolResult()

        def factory(spec, publish):
            def stream(model, context, options):
                contexts.append(context)
                limits.append(options.max_tokens)
                return EventStream.empty(AssistantMessage(content=(TextContent(text="result"),)))

            agent = Agent(
                stream,
                config=AgentConfig(model="test/dummy", system_prompt=child_prompt(parent_prompt, spec)),
                tools=[AgentTool(name="read", execute=execute), AgentTool(name="write", execute=execute)],
            )
            return agent

        store = child_store_type(tmp_path / "children")
        owner = runtime_factory(factory, store.save, begin=store.begin, records=store.load())
        try:
            config = {
                "label": "Reviewer",
                "instructions": "HISTORICAL-INSTRUCTIONS",
                "system_prompt": "CHILD-SYSTEM",
                "tools": ["read"],
                "inherit_context": False,
            }
            child = await owner.start("task", config=config)
            result = await owner.control("wait", child)
            assert result["status"] == "completed" and result["config"]["label"] == "Reviewer"
            assert [tool.name for tool in contexts[0].tools] == ["read"] and limits == [None]
            assert "PARENT-RESTRICTIONS" not in str(contexts[0].system_prompt)
            assert "CHILD-SYSTEM" in str(contexts[0].system_prompt)
            assert "HISTORICAL-INSTRUCTIONS" not in str(contexts[0].system_prompt)
            assert len(contexts[0].messages) == 1
            assert contexts[0].messages[0].role == "user"
            first_user = contexts[0].messages[0].content[0].text
            assert first_user == "Task materials:\nHISTORICAL-INSTRUCTIONS\n\nTask:\ntask"
            assert result["messages"][0]["content"][0]["text"] == first_user
            assert parent_prompt == "PARENT-RESTRICTIONS"
            restored = runtime_factory(factory, records=store.load())
            try:
                record = await restored.control("inspect", child)
                assert record["config"] == result["config"] and not restored.active
            finally:
                await restored.aclose()
        finally:
            await owner.aclose()

    async def test_unknown_tool_subset_fails_before_model_work(self, *, runtime_factory):
        calls = []

        async def execute(*args, **kwargs):
            return AgentToolResult()

        def factory(spec, publish):
            def stream(model, context, options):
                calls.append(options.max_tokens)
                return EventStream.empty(AssistantMessage(content=(ToolCall(id="read", name="read", arguments={}),)))

            return Agent(
                stream,
                config=AgentConfig(model="test/dummy"),
                tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)],
            )

        owner = runtime_factory(factory)
        try:
            unknown = await owner.start("task", config={"tools": ["missing"]})
            result = await owner.control("wait", unknown)
            assert result["status"] == "failed" and "unavailable" in result["error"] and not calls
        finally:
            await owner.aclose()

    async def test_nested_config_merges_explicit_context_controls(self, *, runtime_factory):
        specs = []

        def factory(spec, publish):
            specs.append(spec)
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        owner = runtime_factory(factory)
        try:
            child = await owner.start("task", inherit_context=False, config={"label": "Reviewer"})
            await owner.control("wait", child)
            assert not specs[0].inherit_context and specs[0].label == "Reviewer"
            spawn = owner.tools()[0]
            result = await spawn.execute("call", {"action": "spawn", "task": "task", "config": {"tools": []}})
            record = await owner.control("wait", result.content[0].text)
            assert record["config"]["tools"] == []
        finally:
            await owner.aclose()

    @pytest.mark.parametrize(
        "key,value,flag",
        [
            ("timeout", 120, "--timeout 120"),
            ("max_model_calls", 16, "--calls 16"),
            ("max_tokens", 32000, "--tokens 32000"),
        ],
    )
    async def test_removed_budget_controls_are_not_exposed_or_silently_accepted(
        self, key, value, flag, *, runtime_factory, execution_owner, runtime_type
    ):
        import inspect

        from xdog.agent.supervision_config import ChildPresets

        assert key not in ChildConfig().to_dict()
        assert key not in inspect.signature(runtime_type.start).parameters
        with pytest.raises(ValueError):
            parse_child_start(flag + " task")
        with pytest.raises(ValueError):
            ChildPresets({"old": {"config": {key: value}}})
        owner = runtime_factory(lambda *args: pytest.fail("Must not admit invalid config"))
        try:
            spawn = owner.tools()[0]
            properties = spawn.parameters["properties"]
            assert key not in properties and key not in properties["config"]["properties"]
            with pytest.raises(ValueError):
                await spawn.execute("call", {"action": "spawn", "task": "task", key: value})
            with pytest.raises(ValueError):
                await owner.start("task", config={key: value})
            assert not owner.active
            execution_owner.assert_unstarted(owner)
        finally:
            await owner.aclose()
