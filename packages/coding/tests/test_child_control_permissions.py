"""Child queries do not grant approval for child execution or mutation."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall

from packages.coding.tests.test_child_agents import create


def context(session, args, *, tools=None):
    return BeforeToolCallContext(
        assistant_message=AssistantMessage(),
        tool_call=ToolCall(id="control", name="agent", arguments=args), args=args,
        context=AgentContext(tools=tuple(session.agent.active_tools) if tools is None else tools))


@pytest.mark.parametrize("mode", ["ask", "deny"])
@pytest.mark.parametrize("action", ["wait", "inspect", "list", "presets"])
@pytest.mark.parametrize("extra", [{}, {"text": ""}, {"text": "ignored query text"}])
async def test_actual_host_queries_do_not_request_approval(tmp_path, mode, action, extra):
    session, _ = create(tmp_path, mode)
    requests = []
    session.permissions.set_request_handler(requests.append)
    try:
        child = await session.children.runtime.start("read material")
        args = {"action": action, **({"id": child} if action in {"wait", "inspect"} else {}), **extra}
        ctx = context(session, args)
        async with asyncio.timeout(5):
            assert await session._before_tool_call(ctx) is None
            result = await session.children.tools[0].execute("control", args)
        assert result.content and not requests
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


@pytest.mark.parametrize("action", ["wait", "inspect", "list", "presets", "spawn",
                                     "cancel", "close", "queue", "set_system_prompt"])
async def test_ask_all_and_mutations_still_require_approval(tmp_path, action):
    session, _ = create(tmp_path, "ask-all" if action in {"wait", "inspect", "list", "presets"} else "ask")
    requests = []

    def deny(request):
        requests.append(request)
        session.permissions.resolve(request.id, "deny")

    session.permissions.set_request_handler(deny)
    try:
        args = {"action": action, "id": "child"}
        result = await session._before_tool_call(context(session, args))
        assert result is not None and result.block and len(requests) == 1
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


@pytest.mark.parametrize("binding", ["replacement", "missing-context", "inactive", "duplicate", "executor",
                                      "validator", "closed"])
async def test_tool_name_does_not_grant_query_exemption(tmp_path, binding):
    session, _ = create(tmp_path, "deny")
    original = session.children.tools[0]
    try:
        ctx = context(session, {"action": "wait", "id": "child"})
        if binding == "replacement":
            fake = replace(original)
            session.agent.set_tools(tuple(fake if tool is original else tool for tool in session.agent.state.tools))
            ctx = context(session, ctx.args)
        elif binding == "missing-context":
            ctx = context(session, ctx.args, tools=())
        elif binding == "inactive":
            session.agent.set_active_tools(["current_time"], expected_revision=session.agent.tool_selection_revision)
        elif binding == "duplicate":
            ctx = context(session, ctx.args, tools=(original, replace(original)))
        elif binding == "executor":
            async def replaced_execute(*args, **kwargs):
                raise AssertionError("An untrusted replacement must not execute")
            original.execute = replaced_execute
        elif binding == "validator":
            original.validate_arguments = None
        else:
            await session.children.runtime.aclose()
        result = await session._before_tool_call(ctx)
        assert result is not None and result.block
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


@pytest.mark.parametrize("args", [
    {"action": "wait"}, {"action": "wait", "id": ""}, {"action": "wait", "id": 1},
    {"action": "wait", "id": "child", "task": "side effect"},
    {"action": "wait", "id": "child", "text": 1},
    {"action": "list", "task": "spawn this"}, {"action": []}, {"action": "unknown"},
])
async def test_malformed_queries_are_not_exempt(tmp_path, args):
    session, _ = create(tmp_path, "deny")
    try:
        assert not session.children.approval_free_control(context(session, args))
        result = await session._before_tool_call(context(session, args))
        assert result is not None and result.block
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


async def test_extension_cannot_rewrite_query_into_unapproved_mutation(tmp_path):
    session, _ = create(tmp_path, "deny")
    original_extensions = session.extensions

    async def event(*args):
        pass

    async def pre(name, args):
        return {"action": "cancel", "id": "child"}


    session.extensions = SimpleNamespace(enabled=True, event=event, pre=pre)
    try:
        result = await session._before_tool_call(context(session, {"action": "wait", "id": "child"}))
        assert result is not None and result.block
    finally:
        session.extensions = original_extensions
        await session.children.runtime.aclose()
        await session.aclose()


async def test_wait_exemption_does_not_approve_child_write(tmp_path):
    session, _ = create(tmp_path, "ask")
    try:
        child = await session.children.runtime.start("write", access="inherit")
        args = {"action": "wait", "id": child}
        assert await session._before_tool_call(context(session, args)) is None
        async with asyncio.timeout(5):
            result = await session.children.tools[0].execute("control", args)
        record = json.loads(result.content[0].text)
        assert record["status"] == "completed"
        assert any(row.get("is_error") for row in record["messages"] if row["role"] == "toolResult")
        assert not (tmp_path / "target").exists()
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


async def test_explicit_read_only_scope_allows_queries_but_not_child_effects(tmp_path):
    session, _ = create(tmp_path, "ask")
    try:
        session.access_control(True)
        assert await session._before_tool_call(context(session, {"action": "list"})) is None
        for args in ({"action": "start", "task": "must not start"},
                     {"action": "follow-up", "id": "old-child", "text": "must not execute"}):
            result = await session._before_tool_call(context(session, args))
            assert result is not None and result.block and "read-only" in result.reason
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


@pytest.mark.parametrize("mode", ["ask", "deny", "ask-all"])
@pytest.mark.parametrize("action", ["steer", "follow-up"])
async def test_messages_use_existing_child_without_granting_execution_approval(tmp_path, mode, action):
    from packages.coding.tests.test_child_agents import wait

    session, contexts = create(tmp_path, mode, block=True)
    requests = []

    def deny(request):
        requests.append(request)
        session.permissions.resolve(request.id, "deny")

    session.permissions.set_request_handler(deny)
    try:
        child = await session.children.runtime.start("read material")
        await wait(lambda: bool(contexts))
        args = {"action": action, "id": child, "text": "write a file after your current step"}
        result = await session._before_tool_call(context(session, args))
        if mode == "ask-all":
            assert result is not None and result.block and len(requests) == 1
        else:
            assert result is None and not requests
            output = await session.children.tools[0].execute("control", args)
            record = json.loads(output.content[0].text)
            assert record["accepted_input"] and record["status"] == "running"
        assert not (tmp_path / "target").exists()
    finally:
        await session.children.runtime.aclose()
        await session.aclose()


async def test_parent_model_wait_uses_real_agent_hook_without_approval(tmp_path):
    from xdog.agent.skills import SkillRepo
    from xdog.ai.types import TextContent
    from xdog.ai.utils.event_stream import EventStream

    session, _ = create(tmp_path, "ask", skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared"))
    requests = []
    session.permissions.set_request_handler(requests.append)
    try:
        child = await session.children.runtime.start("read material")
        await session.children.runtime.control("wait", child)

        def stream(model, context, options):
            if context.messages[-1].role == "user":
                result = AssistantMessage(content=(ToolCall(id="wait", name="agent",
                    arguments={"action": "wait", "id": child}),))
            else:
                assert context.messages[-1].role == "toolResult"
                assert not context.messages[-1].is_error
                result = AssistantMessage(content=(TextContent(text="Child completed"),))
            return EventStream.empty(result)

        session.agent.set_stream_fn(stream)
        async with asyncio.timeout(5):
            await session.send_message("Wait for the child")
        assert not requests and "Child completed" in str(session.messages[-1].content)
    finally:
        await session.children.runtime.aclose()
        await session.aclose()
