from __future__ import annotations

import asyncio
from unittest.mock import patch

from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent


async def test_live_gateway_client_plan_updates_execute_persistence_and_no_auto_submission(tmp_path):
    calls = []
    def stream(_model, context, _options=None):
        calls.append(context)
        last = context.messages[-1]
        if last.role == "user" and "build" in str(last.content):
            call = ToolCall(id="plan", name="update_plan", arguments={"expected_revision": 1,
                           "steps": [{"description": "Implement", "status": "pending"}]})
            message = AssistantMessage(content=(call,), stop_reason="toolUse")
        elif last.role == "user" and "write" in str(last.content):
            message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "changed"}),), stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    workspace = tmp_path / "workspace"
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True, workspace=str(workspace)),))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text("preserved draft")
        ui._plan_command("start Implement")
        await wait(lambda: ui._plan_snapshot.mode == "planning" and ui._plan_pending is None)
        assert ui._plan_panel is not None and not calls and ui._editor.get_text() == "preserved draft"
        runtime = server._orchestrator._runtimes["main"]
        session_id = runtime.get_new_session_id()
        ui._close_plan()
        ui._handle_submit("build")
        await wait(lambda: len(calls) == 2 and ui._active_run_id is None)
        assert runtime._active_session.plan.state.revision == 2
        ui._handle_submit("write")
        await wait(lambda: len(calls) == 4 and ui._active_run_id is None)
        assert not (workspace / "target").exists()
        # The silent compaction path must not bypass the host planning hook.
        shell = next(tool for tool in runtime._active_session._compaction_tools() if tool.name == "bash")
        result = await shell.execute("flush", {"command": "touch bypass"})
        assert "read-only" in result.content[0].text and not (workspace / "bypass").exists()
        with patch.object(runtime.transcript_store, "needs_daily_reset", return_value=True):
            assert runtime.get_or_create_session().meta.session_id == session_id
        ui._open_plan()
        await wait(lambda: ui._plan_snapshot.revision == 2 and ui._plan_pending is None)
        panel = ui._plan_panel
        panel.render(80)
        panel.handle_input(KeyEvent("e"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._plan_snapshot.mode == "executing" and ui._plan_pending is None)
        assert len(calls) == 4  # No auto-submitted model turn.
        ui._close_plan()
        ui._handle_submit("write")
        await wait(lambda: len(calls) == 6 and ui._active_run_id is None)
        assert (workspace / "target").read_text() == "changed"
        saved = runtime._active_session.create_branch()
        assert saved
        runtime._active_session.plan.change("revise", 3)
        runtime.navigate_session("branch", session_id, saved, -1, session_id)
        assert runtime.get_or_create_session().plan.state.mode == "executing"
        # Forking a selected branch inherits THAT plan snapshot, not a revised head.
        runtime._active_session.plan.change("revise", 3)
        before = set(runtime.transcript_store.list_branches(session_id))
        runtime.navigate_session("fork", session_id, saved, -1, session_id)
        created = set(runtime.transcript_store.list_branches(session_id)) - before
        assert len(created) == 1
        assert runtime.transcript_store.branch_plan(session_id, created.pop())["mode"] == "executing"
        runtime._drop_session()
        assert runtime.get_or_create_session().plan.state.mode == "planning"
        ui._handle_response({"type": "plan_result", "request_id": "stale", "group_id": "main",
                             "success": True, "plan": {"mode": "off", "revision": 100}})
        assert ui._plan_snapshot.mode == "executing"
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_gateway_plan_session_revision_and_group_idle_guards(tmp_path):
    import json

    model_calls = []
    def stream(*args):
        model_calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def control(action, session="", revision=0, objective=""):
        writer.write((json.dumps({"type": "plan_control", "request_id": action, "group_id": "main",
                                  "session_id": session, "revision": revision,
                                  "action": action, "objective": objective}) + "\n").encode())
        await writer.drain()
        async with asyncio.timeout(3):
            while True:
                value = json.loads(await reader.readline())
                if value["type"] == "plan_result":
                    return value
    try:
        assert not (await control("start"))["success"]
        assert not (await control("stop"))["success"]
        assert server._orchestrator._runtimes["main"].get_new_session_id() is None
        response = await control("start", objective="Implement")
        assert response["success"] and response["plan"]["mode"] == "planning"
        session_id = response["session_id"]
        runtime = server._orchestrator._runtimes["main"]
        await runtime._active_session.plan.tool().execute("steps", {"expected_revision": 1, "steps": [
            {"description": "Implement", "status": "pending"}]})
        assert not (await control("execute", "foreign", 2))["success"]
        assert not (await control("execute", session_id, 1))["success"]
        assert server._orchestrator.try_reserve_group("main")
        try:
            assert not (await control("execute", session_id, 2))["success"]
        finally:
            server._orchestrator.release_group_reservation("main")
        assert runtime._active_session.plan.state.mode == "planning"
        assert (await control("execute", session_id, 2))["success"]
        assert runtime._active_session.plan.state.mode == "executing" and not model_calls
        assert not (await control("execute", session_id, 2))["success"]  # No replay after receipt uncertainty.
        assert (await control("stop", session_id, 3))["success"]
        assert runtime._active_session.plan.state.mode == "off" and not model_calls
        runtime._drop_session()
        runtime.transcript_store._plans[f"{session_id}.plan.json"] = {"invalid_saved_plan": True}
        writer.write(b'{"type":"ping","group_id":"main"}\n')
        await writer.drain()
        pong = json.loads(await asyncio.wait_for(reader.readline(), 3))
        assert pong["type"] == "pong" and pong["plan_controls"] == 0 and "Invalid saved plan" in pong["plan_error"]
        assert not (await control("execute", session_id, 4))["success"] and not model_calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_plan_sidecars_survive_catalog_recovery_and_corruption_fails_closed(tmp_path):
    from xdog.agent.planning import PlanState
    from xdog.claw.core.persistence.transcript_store import TranscriptStore

    store = TranscriptStore(tmp_path, owner_group="main")
    meta = store.create_session("main")
    state = PlanState(mode="planning", revision=1, objective="Objective")
    store.save_plan(meta.session_id, state.to_dict())
    store.save_branch(meta.session_id, "branch", [])
    (tmp_path / "catalog.json").write_text("{broken")
    recovered = TranscriptStore(tmp_path, owner_group="main")
    assert PlanState.from_dict(recovered.get_plan(meta.session_id)) == state
    assert PlanState.from_dict(recovered.branch_plan(meta.session_id, "branch")) == state
    import pytest
    for invalid in ("{broken", "null", "[]"):
        (tmp_path / f"{meta.session_id}.plan.json").write_text(invalid)
        broken = TranscriptStore(tmp_path, owner_group="main")
        with pytest.raises(ValueError):
            PlanState.from_dict(broken.get_plan(meta.session_id))
