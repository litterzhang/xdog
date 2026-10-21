"""Regression coverage for the single, owner-bound child tool."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading

import pytest
from xdog.agent import (
    Agent,
    AgentConfig,
    AgentTool,
    BeforeToolCallResult,
    ToolExecutionEndEvent,
)
from xdog.agent.supervision_config import ChildPresets
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

CONTROL_ACTIONS = (
    "list",
    "presets",
    "inspect",
    "steer",
    "follow-up",
    "cancel",
    "wait",
    "close",
    "queue",
    "set_system_prompt",
)
ID_ACTIONS = tuple(action for action in CONTROL_ACTIONS if action not in {"list", "presets"})
RESERVED_NAMES = ("agent", "agent_spawn", "agent_control", "update_plan")


def completed_agent():
    return Agent(
        lambda *args: EventStream.empty(AssistantMessage(content=(TextContent(text="child answer"),))),
        config=AgentConfig(model="test/dummy"),
    )


def merged_tool(owner):
    tools = owner.tools()
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, AgentTool)
    assert tool.name == "agent"
    assert tool.execute is not None
    return tool


async def invoke(tool, action, **fields):
    return await tool.execute("test-call", {"action": action, **fields})


def json_result(result):
    assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
    return json.loads(result.content[0].text)


# Both validation entry points must reject cross-branch fields, even harmless
# values: schemas alone are not a permission boundary, nor are direct execute calls.
INVALID_ARGUMENTS = [
    {},
    {"action": "unknown"},
    {"action": []},
    {"action": None},
    {"action": "spawn"},
    {"action": "spawn", "task": ""},
    {"action": "spawn", "task": 1},
    {"action": "spawn", "task": "work", "access": "all"},
    {"action": "spawn", "task": "work", "inherit_context": 1},
    {"action": "spawn", "task": "work", "config": {"unknown": True}},
    *(
        {"action": "spawn", "task": "work", key: value}
        for key, value in (("owner", "forged"), ("id", "child"), ("text", ""))
    ),
    *({"action": action} for action in ID_ACTIONS),
    *(
        {"action": action, "id": "child", key: value}
        for action in CONTROL_ACTIONS
        for key, value in (
            ("task", "work"),
            ("access", "inherit"),
            ("inherit_context", False),
            ("config", {}),
            ("owner", "forged"),
        )
    ),
    *({"action": "inspect", "id": value} for value in ("", None, 1)),
    {"action": "steer", "id": "child", "text": 1},
]


async def parent_call(tool, arguments, before_hook):
    responses = iter(
        (
            AssistantMessage(
                content=(ToolCall(id="parent-call", name="agent", arguments=arguments),), stop_reason="toolUse"
            ),
            AssistantMessage(content=(TextContent(text="parent finished"),)),
        )
    )
    parent = Agent(
        lambda *args: EventStream.empty(next(responses)),
        config=AgentConfig(model="test/dummy"),
        tools=[tool],
        before_tool_call=before_hook,
    )
    try:
        async with asyncio.timeout(5):
            stream = await parent.prompt("Delegate only with permission")
            return [event async for event in stream]
    finally:
        await parent.cancel_and_wait()


class TestChildTool:
    @pytest.fixture
    async def owner(self, *, runtime_factory):
        supervisor = runtime_factory(lambda spec, publish: completed_agent())
        try:
            yield supervisor
        finally:
            await supervisor.aclose()

    async def test_only_merged_agent_tool_is_exposed_with_all_actions(self, owner, *, runtime_factory):
        tool = merged_tool(owner)
        properties = tool.parameters["properties"]
        assert set(properties) == {"action", "task", "access", "inherit_context", "config", "id", "text"}
        assert set(properties["action"]["enum"]) == {"spawn", *CONTROL_ACTIONS}
        assert "action" in tool.parameters["required"]
        assert tool.parameters.get("additionalProperties") is False
        assert callable(tool.validate_arguments)
        assert json_result(await invoke(tool, "list")) == {"children": []}
        assert json_result(await invoke(tool, "presets")) == {"presets": []}
        assert not owner.active

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {
                "access": "inherit",
                "inherit_context": False,
                "config": {"label": "Reviewer", "instructions": "Inspect only", "tools": []},
            },
        ],
    )
    async def test_merged_entry_really_spawns_lists_waits_and_closes(self, fields, *, runtime_factory):
        specs, saved = [], []

        def factory(spec, publish):
            specs.append(spec)
            return completed_agent()

        owner = runtime_factory(factory, saved.append)
        try:
            tool = merged_tool(owner)
            spawned = await invoke(tool, "spawn", task="Investigate", **fields)
            identifier = spawned.content[0].text
            assert isinstance(identifier, str) and identifier
            listed = json_result(await invoke(tool, "list"))["children"]
            assert [row["id"] for row in listed] == [identifier]
            async with asyncio.timeout(5):
                record = json_result(await invoke(tool, "wait", id=identifier))
            assert record["id"] == identifier and record["status"] == "completed"
            assert record["result"] == "child answer" and record["calls"] == 1
            assert len(specs) == len(saved) == 1
            assert specs[0].task == "Investigate"
            assert specs[0].access == fields.get("access", "read-only")
            assert specs[0].inherit_context is fields.get("inherit_context", False)
            if fields:
                assert record["config"]["label"] == "Reviewer"
                assert record["config"]["instructions"] == "Inspect only"
                assert record["config"]["tools"] == []
            assert json_result(await invoke(tool, "inspect", id=identifier))["result"] == "child answer"
            await invoke(tool, "close", id=identifier)
            assert json_result(await invoke(tool, "list")) == {"children": []}
            assert not owner.active and len(saved) == 1
            with pytest.raises(ValueError):
                await invoke(tool, "inspect", id=identifier)
        finally:
            await owner.aclose()

    async def test_merged_presets_are_read_only_and_expand_on_spawn(self, *, runtime_factory):
        specs = []

        def factory(spec, publish):
            specs.append(spec)
            return completed_agent()

        presets = ChildPresets(
            {
                "reviewer": {
                    "description": "Read only",
                    "config": {"instructions": "REVIEW", "inherit_context": False, "tools": []},
                }
            }
        )
        owner = runtime_factory(factory, presets=presets)
        try:
            tool = merged_tool(owner)
            assert json_result(await invoke(tool, "presets")) == {"presets": list(presets.snapshot())}
            assert not specs and owner.snapshot() == {"children": []}
            identifier = (await invoke(tool, "spawn", task="Review", config={"preset": "reviewer"})).content[0].text
            record = json_result(await invoke(tool, "wait", id=identifier))
            assert record["status"] == "completed" and len(specs) == 1
            assert specs[0].preset == "reviewer" and specs[0].instructions == "REVIEW"
            assert not specs[0].inherit_context and specs[0].tools == ()
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("arguments", INVALID_ARGUMENTS)
    @pytest.mark.parametrize("entry", ["validate_arguments", "execute"])
    async def test_strict_action_branch_validation_never_admits_work(
        self, arguments, entry, *, runtime_factory, execution_owner
    ):
        created = []
        owner = runtime_factory(lambda spec, publish: created.append(spec) or completed_agent())
        try:
            tool = merged_tool(owner)
            with pytest.raises(ValueError):
                if entry == "validate_arguments":
                    checked = tool.validate_arguments(arguments)
                    if inspect.isawaitable(checked):
                        await checked
                else:
                    await tool.execute("invalid-call", arguments)
            assert created == [] and owner.snapshot() == {"children": []} and not owner.active
            # This private observation is intentional: rejecting input must not even
            # lazily create the supervisor's worker thread.
            execution_owner.assert_unstarted(owner)
        finally:
            await owner.aclose()

    async def test_real_parent_before_tool_call_can_deny_spawn_without_child_or_thread(
        self, *, runtime_factory, execution_owner
    ):
        created, seen = [], []
        owner = runtime_factory(lambda spec, publish: created.append(spec) or completed_agent())

        async def deny(context, cancel):
            seen.append(context.tool_call)
            return BeforeToolCallResult(block=True, reason="spawn requires approval")

        try:
            events = await parent_call(merged_tool(owner), {"action": "spawn", "task": "work"}, deny)
            assert len(seen) == 1 and seen[0].name == "agent" and seen[0].arguments["action"] == "spawn"
            ended = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
            assert len(ended) == 1 and ended[0].is_error
            assert "spawn requires approval" in str(ended[0].result)
            assert created == [] and owner.snapshot() == {"children": []} and not owner.active
            execution_owner.assert_unstarted(owner)
        finally:
            await owner.aclose()

    @pytest.mark.parametrize(
        "arguments",
        [
            {"action": "spawn", "task": "work", "owner": "forged"},
            {"action": "spawn", "task": "work", "id": "child"},
            {"action": "list", "task": "work"},
            {"action": "list", "config": {}},
        ],
    )
    async def test_invalid_branches_fail_before_real_parent_permission_hook(
        self, owner, arguments, *, runtime_factory, execution_owner
    ):
        seen = []

        async def allow(context, cancel):
            seen.append(context)
            return BeforeToolCallResult()

        events = await parent_call(merged_tool(owner), arguments, allow)
        assert not seen
        ended = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
        assert len(ended) == 1 and ended[0].is_error
        assert owner.snapshot() == {"children": []}
        execution_owner.assert_unstarted(owner)

    @pytest.mark.parametrize("name", RESERVED_NAMES)
    async def test_factory_child_cannot_use_merged_or_legacy_supervision_tools(self, name, *, runtime_factory):
        model_calls = []

        def factory(spec, publish):
            def stream(*args):
                model_calls.append(spec.id)
                return EventStream.empty(AssistantMessage())

            tools = owner.tools() if name == "agent" else [AgentTool(name=name)]
            return Agent(stream, config=AgentConfig(model="test/dummy"), tools=tools)

        owner = runtime_factory(factory)
        try:
            tool = merged_tool(owner)
            identifier = (await invoke(tool, "spawn", task="No recursive delegation")).content[0].text
            record = json_result(await invoke(tool, "wait", id=identifier))
            assert record["status"] == "failed" and record["error"]
            assert record["calls"] == 0 and not model_calls and not owner.active
            assert len(owner.snapshot()["children"]) == 1
        finally:
            await owner.aclose()

    async def test_merged_steer_follow_up_queue_and_cancel_use_real_child(self, *, runtime_factory):
        entered = threading.Event()

        def factory(spec, publish):
            def stream(*args):
                async def generate():
                    entered.set()
                    await asyncio.Event().wait()
                    yield DoneEvent(message=AssistantMessage())  # Unreachable until explicitly canceled.

                return EventStream.from_async_generator(generate())

            return Agent(stream, config=AgentConfig(model="test/dummy"))

        owner = runtime_factory(factory)
        try:
            tool = merged_tool(owner)
            identifier = (await invoke(tool, "spawn", task="Wait for direction")).content[0].text
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.002)
            await invoke(tool, "steer", id=identifier, text="new direction")
            await invoke(tool, "follow-up", id=identifier, text="later task")
            record = json_result(await invoke(tool, "queue", id=identifier, text=json.dumps({"action": "list"})))
            assert [row["content"] for row in record["queue"]["inputs"]] == ["new direction", "later task"]
            async with asyncio.timeout(5):
                canceled = json_result(await invoke(tool, "cancel", id=identifier))
            assert canceled["status"] == "canceled" and not owner.active
            await invoke(tool, "close", id=identifier)
            assert json_result(await invoke(tool, "list")) == {"children": []}
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("action", ID_ACTIONS)
    async def test_merged_tools_are_session_scoped_and_foreign_ids_cannot_control_children(
        self, owner, action, *, runtime_factory
    ):
        other = runtime_factory(lambda spec, publish: completed_agent())
        try:
            tool, foreign_tool = (
                merged_tool(owner),
                merged_tool(other),
            )
            identifier = (await invoke(tool, "spawn", task="Owned by the first session")).content[0].text
            await invoke(tool, "wait", id=identifier)
            assert json_result(await invoke(foreign_tool, "list")) == {"children": []}
            text = json.dumps({"action": "list"}) if action == "queue" else "foreign input"
            with pytest.raises(ValueError):
                await invoke(foreign_tool, action, id=identifier, text=text)
            assert other.snapshot() == {"children": []}
            # Refreshed tool objects retain their original session owner.
            record = json_result(await invoke(merged_tool(owner), "inspect", id=identifier))
            assert record["status"] == "completed" and record["task"] == "Owned by the first session"
            await invoke(merged_tool(other), "spawn", task="Separate session child")
            assert identifier not in {row["id"] for row in other.snapshot()["children"]}
        finally:
            await other.aclose()

    @pytest.mark.parametrize("action", ["spawn", "list", "presets"])
    @pytest.mark.parametrize("revocation", ["cancel", "close"])
    async def test_merged_entry_refuses_canceled_calls_and_closed_owners(
        self, action, revocation, *, runtime_factory, execution_owner
    ):
        created = []
        owner = runtime_factory(lambda spec, publish: created.append(spec) or completed_agent())
        try:
            tool = merged_tool(owner)
            cancel = asyncio.Event()
            if revocation == "cancel":
                cancel.set()
            else:
                await owner.aclose()
            arguments = {"action": action}
            if action == "spawn":
                arguments["task"] = "Must not start"
            with pytest.raises(ValueError):
                await tool.execute("revoked-call", arguments, cancel=cancel)
            assert not created and owner.snapshot() == {"children": []} and not owner.active
            execution_owner.assert_unstarted(owner)
        finally:
            await owner.aclose()
