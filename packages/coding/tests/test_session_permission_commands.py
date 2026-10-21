"""Session-only native permission commands use the real Coding lifecycle gates."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent import Agent, AgentConfig, AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.slash_commands import BUILTIN_COMMANDS, command_reservations, execute_command
from xdog.coding.core.tools import get_default_tools


def make_session(tmp_path, *, mode="ask", stream=None):
    def default_stream(model, context, options):
        if context.messages[-1].role == "user" and "write" in str(context.messages[-1].content):
            message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "child change"}),))
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        return EventStream.empty(message)

    store = SessionManager(tmp_path / "sessions")
    return AgentSession(
        agent=Agent(stream or default_stream, config=AgentConfig(model="test/model"),
                    tools=get_default_tools(tmp_path), tool_ctx={"fs_workspace": str(tmp_path)}),
        session_data=store.create_session(model="test/model", working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=tmp_path / "settings.json", project_dir=tmp_path),
        tool_registry=None, bash=None, working_dir=tmp_path, permissions=PermissionManager(mode))


@pytest.fixture
async def session(tmp_path):
    value = make_session(tmp_path)
    try:
        yield value
    finally:
        await value.aclose()


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("args", ["", "status", " status "])
async def test_status_is_read_only_and_does_not_create_an_owner(session, args):
    before = session.permissions.policy_revision
    result = await execute_command("permissions", args, session)
    assert "Permissions: ask" in result.output and "current session only" in result.output
    assert not result.prompt and not result.exit_requested
    assert session.permissions.policy_revision == before
    assert session._sdk_controller is None


@pytest.mark.parametrize("mode", ["ask", "ask-all", "allow-all", "deny", "full-access"])
async def test_four_modes_and_alias_are_live_canonical_and_never_persist_config(session, tmp_path, mode):
    # Existing global/project files must stay byte-for-byte identical, including unknown keys.
    global_path = tmp_path / "settings.json"
    project_path = tmp_path / ".coding" / "settings.json"
    project_path.parent.mkdir(exist_ok=True)
    global_path.write_text('{"permission_mode":"ask", "unrelated":"global"}\n')
    project_path.write_text('{"permission_mode":"deny", "unrelated":"project"}\n')
    settings_before = (session.settings._global.model_dump(), session.settings._project.model_dump(),
                       session.settings._session.model_dump())
    files_before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    selected = "allow-all" if mode == "full-access" else mode
    result = await execute_command("permissions", mode, session)
    assert f"Permissions: {selected}" in result.output
    assert session.permissions.mode == selected
    assert session.session_info()["permission_mode"] == selected
    assert session.extensions.context()["permission_mode"] == selected
    assert settings_before == (session.settings._global.model_dump(), session.settings._project.model_dump(),
                               session.settings._session.model_dump())
    assert files_before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert session._sdk_controller is None


@pytest.mark.parametrize("args", ["bogus", "allow_all", "allow-all extra", "status extra", "ASK"])
async def test_unknown_mode_is_explicitly_rejected_without_policy_or_grant_changes(session, args):
    session.permissions._session_rules.add("original exact grant")
    before = session.permissions.policy_revision
    result = await execute_command("permissions", args, session)
    assert "Unknown permission mode" in result.output and "Permissions unchanged" in result.output
    assert session.permissions.mode == "ask" and session.permissions.policy_revision == before
    assert session.permissions._session_rules == {"original exact grant"}
    with pytest.raises(ValueError, match="Unknown permission mode"):
        session.set_permission_mode(args)


async def test_builtin_registration_reserves_command_and_does_not_expose_model_tool(session):
    assert "permissions" in BUILTIN_COMMANDS
    builtins, _ = await asyncio.to_thread(command_reservations)
    assert "permissions" in builtins
    assert not any(tool.name in {"permissions", "set_permission_mode"} for tool in session.agent.state.tools)
    result = await execute_command("help", "permissions", session)
    assert "/permissions" in result.output
    ctx = BeforeToolCallContext(AssistantMessage(), ToolCall(id="fake", name="permissions", arguments={}),
                                {}, AgentContext(tools=tuple(session.agent.active_tools)))
    session.permissions.set_mode("deny")
    denied = await session._before_tool_call(ctx)
    assert denied is not None and denied.block
    assert session.permissions.mode == "deny"


async def test_busy_model_rejects_not_queues_switch_and_status_remains_available(tmp_path):
    started, release = threading.Event(), threading.Event()

    def stream(model, context, options):
        message = AssistantMessage(content=(TextContent(text="done"),))
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        async def generate():
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            from xdog.ai.types import DoneEvent
            yield DoneEvent(message=message)
        return EventStream.from_async_generator(generate(), result_future=future)

    session = make_session(tmp_path, stream=stream)
    turn = asyncio.create_task(session.send_message("busy"))
    try:
        await wait_until(started.is_set)
        assert session._sdk_controller is not None  # Exercise the real owner, not a mock streaming flag.
        revision = session.permissions.policy_revision
        async with asyncio.timeout(1):
            assert "Permissions: ask" in (await execute_command("permissions", "status", session)).output
            result = await execute_command("permissions", "allow-all", session)
            assert "idle session" in result.output and "unchanged" in result.output
            with pytest.raises(RuntimeError, match="idle session"):
                await asyncio.to_thread(session.set_permission_mode, "allow-all")
        assert session.permissions.policy_revision == revision
    finally:
        release.set()
        await turn
        assert session.permissions.mode == "ask"  # No delayed grant after the model settles.
        await session.aclose()


async def test_actual_pending_model_approval_rejects_switch_without_losing_request(session):
    requests = []
    session.permissions.set_request_handler(requests.append)
    turn = asyncio.create_task(session.send_message("write"))
    try:
        await wait_until(lambda: bool(requests))
        assert session.permissions.has_pending_requests
        revision = session.permissions.policy_revision
        async with asyncio.timeout(1):
            result = await execute_command("permissions", "allow-all", session)
        assert "unchanged" in result.output
        assert session.permissions.policy_revision == revision
        assert session.permissions.has_pending(requests[0].id)
        assert session.permissions.resolve(requests[0].id, "deny")
    finally:
        session.permissions.deny_all()
        await turn
    assert not (session.working_dir / "target").exists()


async def test_pending_approval_even_without_streaming_prevents_policy_change(session):
    requests = []
    session.permissions.set_request_handler(requests.append)
    args = {"command": "echo mutation"}
    ctx = BeforeToolCallContext(AssistantMessage(), ToolCall(id="pending", name="bash", arguments=args),
                                args, AgentContext(tools=tuple(session.agent.active_tools)))
    approval = asyncio.create_task(session._before_tool_call(ctx))
    try:
        await wait_until(lambda: bool(requests))
        assert session.agent.is_idle
        result = await execute_command("permissions", "allow-all", session)
        assert "unchanged" in result.output and session.permissions.mode == "ask"
    finally:
        session.permissions.deny_all()
        assert (await approval).block


@pytest.mark.parametrize("gate", ["_compacting", "_auth_changing", "_context_changes", "_tool_selection_retiring"])
async def test_existing_context_change_gates_remain_authoritative(session, gate):
    previous = getattr(session, gate)
    setattr(session, gate, 1 if gate == "_context_changes" else True)
    try:
        result = await execute_command("permissions", "allow-all", session)
        assert "unchanged" in result.output and session.permissions.mode == "ask"
        assert "Permissions: ask" in (await execute_command("permissions", "status", session)).output
    finally:
        setattr(session, gate, previous)


async def test_deny_revokes_session_grants_and_switches_keep_revision_fences(session):
    session.permissions._session_rules.add("original exact grant")
    revision = session.permissions.policy_revision
    await execute_command("permissions", "deny", session)
    assert not session.permissions._session_rules
    assert session.permissions.policy_revision == revision + 1
    await execute_command("permissions", "ask", session)
    assert session.permissions.policy_revision == revision + 2
    await execute_command("permissions", "ask", session)
    assert session.permissions.policy_revision == revision + 2
    assert not session.permissions._session_rules


async def test_child_pending_approval_blocks_grant_and_keeps_parent_cache_isolated(session):
    requests = []
    session.permissions._session_rules.add(
        'filesystem\0{"action": "write", "content": "child change", "path": "target"}')
    session.children.approver = lambda child, request: requests.append((child, request))
    child = await session.children.runtime.start("write", access="inherit")
    try:
        await wait_until(lambda: bool(requests))
        child_manager = session.children.permissions[child]
        assert child_manager is not session.permissions and not child_manager._session_rules
        assert child_manager.has_pending_requests and not session.permissions.has_pending_requests
        assert session.agent.is_idle
        result = await execute_command("permissions", "allow-all", session)
        assert "Child work" in result.output and "unchanged" in result.output
        assert session.permissions.mode == "ask" and child_manager.mode == "ask"
        assert child_manager.has_pending(requests[0][1].id)
    finally:
        session.children.resolve(child, requests[0][1].id, "deny")
        await session.children.runtime.control("wait", child)
    assert not (session.working_dir / "target").exists()
    assert "Permissions: allow-all" in (await execute_command("permissions", "allow-all", session)).output


async def test_new_children_use_current_policy_but_keep_independent_approval_cache(session):
    original_stream = session.agent.stream_fn
    observed = []

    def stream(model, context, options):
        # Completed child managers are deliberately retired; capture the live
        # manager during the real model call instead of relying on retained caches.
        observed.extend((manager, manager.mode, set(manager._session_rules))
                        for manager in session.children.permissions.values())
        return original_stream(model, context, options)

    session.agent.set_stream_fn(stream)
    session.permissions._session_rules.add(
        'filesystem\0{"action": "write", "content": "child change", "path": "target"}')
    await execute_command("permissions", "allow-all", session)
    read_only = await session.children.runtime.start("write", access="read-only")
    await session.children.runtime.control("wait", read_only)
    assert not (session.working_dir / "target").exists()
    assert observed and observed[-1][0] is not session.permissions
    assert observed[-1][1:] == ("allow-all", set())
    inherited = await session.children.runtime.start("write", access="inherit")
    await session.children.runtime.control("wait", inherited)
    assert (session.working_dir / "target").exists()
    assert observed[-1][1:] == ("allow-all", set())
    await execute_command("permissions", "deny", session)
    (session.working_dir / "target").unlink()
    denied = await session.children.runtime.start("write", access="inherit")
    await session.children.runtime.control("wait", denied)
    assert observed[-1][1:] == ("deny", set())
    assert not (session.working_dir / "target").exists()


async def test_permission_change_does_not_relax_readonly_scope(session):
    session.access_control(True)
    await execute_command("permissions", "allow-all", session)
    assert session.permissions.mode == "allow-all" and session.access.state.read_only
    args = {"action": "write", "path": "target", "content": "not allowed"}
    ctx = BeforeToolCallContext(AssistantMessage(), ToolCall(id="write", name="filesystem", arguments=args),
                                args, AgentContext(tools=tuple(session.agent.active_tools)))
    denied = await session._before_tool_call(ctx)
    assert denied is not None and denied.block and "read-only" in denied.reason


@pytest.mark.parametrize("owner", ["sdk", "runtime"])
async def test_idle_owner_commit_runs_under_existing_controller_and_child_gate(session, monkeypatch, owner):
    if owner == "sdk":
        controller, _ = await session._acquire_owner()
    else:
        runtime = session.enable_runtime(bound=False)
        runtime.start(own_thread=True, setup_pending=True)
        controller = runtime.controller
    actual_set_mode = session.permissions.set_mode
    commits = []

    def set_mode(mode):
        commits.append((controller.owns_current_task, session._context_changes,
                        session.children.runtime._admission_pauses))
        actual_set_mode(mode)

    monkeypatch.setattr(session.permissions, "set_mode", set_mode)
    result = await execute_command("permissions", "allow-all", session)
    assert "Permissions: allow-all" in result.output
    assert commits == [(True, 1, 1)]
    assert session._context_changes == 0 and session.children.runtime._admission_pauses == 0


async def test_commands_never_create_config_or_change_another_live_session(session, tmp_path):
    other = make_session(tmp_path)
    global_path = tmp_path / "settings.json"
    project_path = tmp_path / ".coding" / "settings.json"
    assert not global_path.exists() and not project_path.exists()
    try:
        for mode in ("ask-all", "allow-all", "deny", "ask"):
            result = await execute_command("permissions", mode, session)
            assert f"Permissions: {mode}" in result.output
            assert other.permissions.mode == "ask" and other.permissions.policy_revision == 0
            assert not global_path.exists() and not project_path.exists()
    finally:
        await other.aclose()
