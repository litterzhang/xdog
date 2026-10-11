from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("action", ["new", "resume", "branch", "goto", "fork", "fail", "deny", "missing", "expired"])
async def test_live_claw_reviewed_navigation_uses_group_owned_existing_controls(tmp_path, action, monkeypatch):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "nav.sock"),
        pid_file=str(tmp_path / "nav.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("navigator",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    runtime = server._orchestrator._runtimes["main"]
    store = runtime.transcript_store
    first = store.create_session("main")
    original = [{"role": "user", "content": "checkpoint original"}]
    store.replace_transcript(first.session_id, original)
    store.save_branch(first.session_id, "branch", original)
    second = store.create_session("main")
    store.replace_transcript(second.session_id, [{"role": "user", "content": "resume target"}])
    store.activate_session("main", first.session_id)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        before = ui._gateway_session_id
        assert before == first.session_id and ui._extension_navigation_supported
        ui._editor.set_text("parent draft")
        values = {"action": action if action in {"new", "resume", "branch", "goto", "fork"} else "new"}
        if action == "resume":
            values["session_id"] = second.session_id
        if action == "missing":
            values = {"action": "resume", "session_id": "nonexistent"}
        if action == "branch":
            values["branch_id"] = "branch"
        if action in {"goto", "fork"}:
            values["index"] = 0
        if action == "fail":
            values["fail"] = True
        ui._extension_command("ext-navigator-navigate", json.dumps(values))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        late_acks = []
        if action == "expired":
            broker = runtime._active_session.extension_dialogs
            original_resolve = broker.resolve
            def late_answer(key, value):
                if value is True:
                    _dialog, loop, future = broker._pending[key]
                    def lose_to_cancellation():
                        future.set_result(None)
                        late_acks.append(original_resolve(key, value))
                    loop.call_soon_threadsafe(lose_to_cancellation)
                    return True
                return original_resolve(key, value)
            monkeypatch.setattr(broker, "resolve", late_answer)
        assert runtime.get_new_session_id() == before and ui._extension_pending is not None
        if action != "deny":
            panel.handle_input(KeyEvent("right"))
        panel.handle_input(KeyEvent("enter"))
        for _ in range(100):
            panel.render(80)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent("pagedown"))
        assert panel.form._visible
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._extension_pending is None and ui._session_mutation_id is None)
        if action == "new":
            assert runtime.get_new_session_id() != before
        elif action == "resume":
            assert runtime.get_new_session_id() == second.session_id
        elif action == "branch":
            assert store.active_branch(before) == "branch"
        elif action == "goto":
            assert store.active_branch(before) != "branch" and store.active_branch(before)
        elif action == "fork":
            assert len(store.list_branches(before)) == 2
        else:
            assert runtime.get_new_session_id() == before
        if action == "expired":
            assert late_acks == [True]  # ACK succeeded, callback actually received None.
            assert ui._extension_navigation is None and ui._extension_navigation_review is None
            assert "NAVIGATION-REVIEWED-False" in str(ui._chat_log.render(160))
        assert not calls and ui._editor.get_text() == "parent draft"
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_old_navigation_viewer_does_not_adopt_session_mutation_from_callback(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "oldnav.sock"),
        pid_file=str(tmp_path / "oldnav.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("navigator",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch(
        "xdog.claw.core.runtime.gateway._build_model_and_options",
        return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage())),
    ):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        # A previous viewer supports metadata/browser, but not reviewed mutation.
        writer.write(b'{"type":"ping","group_id":"main","extension_session":1}\n')
        await writer.drain()
        state = json.loads(await reader.readline())
        writer.write(
            (
                json.dumps(
                    {
                        "type": "extension_control",
                        "request_id": "old",
                        "group_id": "main",
                        "session_id": state.get("session_id", ""),
                        "action": "command",
                        "name": "ext-navigator-navigate",
                        "args": "new",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        async with asyncio.timeout(8):
            while True:
                reply = json.loads(await reader.readline())
                if reply["type"] == "extension_dialog":
                    assert reply["dialog"]["operation"] == "snapshot"
                    writer.write(
                        (
                            json.dumps(
                                {
                                    "type": "extension_dialog_answer",
                                    "id": reply["dialog"]["id"],
                                    "value": {
                                        "session_id": reply["session_id"],
                                        "workspace": str(tmp_path),
                                        "group_id": "main",
                                    },
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    await writer.drain()
                if reply["type"] == "extension_result":
                    break
        assert reply["success"] and reply["output"] == "NAVIGATION-REVIEWED-False"
        assert server._orchestrator.get_new_session_id("main") == reply["session_id"]
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('reason', ['cancel', 'withdraw'])
def test_local_cancel_or_capability_withdrawal_revokes_actual_review_even_if_callback_succeeds(phase, reason):
    ui = ChatApp('/tmp/unused-revocation.sock')
    sent = []
    ui._navigate_session = lambda *args: sent.append(args)
    try:
        ui._gateway_session_id = 'session'
        ui._connection_ready = True
        ui._extension_navigation_supported = True
        ui._extension_cancel_supported = True
        ui._extension_pending = 'owner', 'session'
        ui._extension_editor_owner = 'owner', 'navigator'
        item = {'id': 'dialog', 'extension': 'navigator', 'kind': 'session', 'operation': 'navigate',
                'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
        assert ui._queue_extension_navigation(item, True)
        def receipt():
            ui._handle_response({'type': 'extension_dialog_accepted', 'id': 'dialog',
                'extension': 'navigator', 'operation': 'navigate', 'request_id': 'owner', 'group_id': ui.group_id})
        if phase == 'after':
            receipt()
            assert ui._extension_navigation is not None
        if reason == 'cancel':
            ui._cancel_extension_command()
        else:
            ui._extension_navigation_supported = False
        if phase == 'before':
            receipt()
        assert not ui._finish_extension_navigation('owner', True)
        assert not sent and ui._extension_navigation is None and ui._extension_navigation_review is None
    finally:
        ui._extension_pending = None
        ui._request_exit()
