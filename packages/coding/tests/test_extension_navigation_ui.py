from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("action", ["new", "resume", "branch", "goto", "fork", "fail", "deny", "missing", "expired"])
def test_actual_coding_reviewed_navigation_deferred_validated_with_preserved_draft(tmp_path, action, monkeypatch):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["navigator"], "extension_dir": str(root)}))
    store = SessionManager(tmp_path / "sessions")
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager("ask"),
    )
    session.agent.append_message(UserMessage(content="checkpoint original"))
    session._persist()
    before = session.session_id
    branch = session.create_branch(at_index=0)
    target = store.create_session(working_dir=tmp_path)
    target.messages = [UserMessage(content="resume target")]
    store.save_session(target)
    ui = InteractiveMode(session)

    def wait(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Navigation did not settle")

    try:
        ui._editor.set_text("parent draft")
        values = {"action": action if action in {"new", "resume", "branch", "goto", "fork"} else "new"}
        if action == "resume":
            values["session_id"] = target.session_id
        if action == "missing":
            values = {"action": "resume", "session_id": "nonexistent"}
        if action == "branch":
            values["branch_id"] = branch
        if action in {"goto", "fork"}:
            values["index"] = 0
        if action == "fail":
            values["fail"] = True
        ui._handle_slash_command("ext-navigator-navigate", json.dumps(values))
        wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        late_acks = []
        if action == "expired":
            broker = session.extension_dialogs
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
        assert session.session_id == before and ui._command is not None
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
        wait(lambda: ui._command is None)
        if action == "new":
            assert session.session_id != before
        elif action == "resume":
            assert session.session_id == target.session_id and "resume target" in str(session.messages[0].content)
        elif action == "branch":
            assert session.session_id == before and session.session_data.active_branch == branch
        elif action == "goto":
            assert session.session_id == before and session.session_data.active_branch != branch
        elif action == "fork":
            assert len(session.session_data.branches) == 2
        else:
            assert session.session_id == before
        if action == "expired":
            assert late_acks == [True]  # ACK succeeded, callback actually received None.
            assert ui._extension_navigation is None and ui._extension_navigation_review is None
            assert "NAVIGATION-REVIEWED-False" in str(ui._chat_log.render(160))
        assert not calls and ui._editor.get_text() == "parent draft" and session.permissions.mode == "ask"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_stale_or_failed_reviewed_navigation_does_not_dispatch_session_commands(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.session_id = 'current'
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    sent = []
    ui._handle_slash_command = lambda *args: sent.append(args)
    try:
        target = {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}
        ui._extension_navigation = ('old-command', ui._mcp_context_key(), target)
        assert not ui._finish_extension_navigation('new-command', True)
        ui._extension_navigation = ('command', ui._mcp_context_key(), target)
        assert not ui._finish_extension_navigation('command', False)
        ui._extension_navigation = ('command', ui._mcp_context_key(), target)
        ui._output_epoch += 1
        assert not ui._finish_extension_navigation('command', True)
        assert not sent
    finally:
        ui._request_exit()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('mutation', ['none', 'id', 'extension', 'operation', 'owner', 'epoch', 'closed', 'ack'])
def test_native_navigation_requires_scoped_actual_receipt_not_transport_ack(tmp_path, kind, mutation):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from xdog.claw.channels.tui.tui_app import ChatApp

    if kind == 'coding':
        session = MagicMock()
        session.session_id = 'session'
        session.working_dir = tmp_path
        ui = InteractiveMode(session)
        ui._command = SimpleNamespace(id='owner', name='ext-navigator-navigate', cancel_requested=False, mutating=False)
        deliver = ui._handle_ui_event
    else:
        ui = ChatApp('/tmp/unused-receipt.sock')
        ui._gateway_session_id = 'session'
        ui._connection_ready = True
        ui._extension_navigation_supported = True
        ui._extension_pending = 'owner', 'session'
        ui._extension_editor_owner = 'owner', 'navigator'
        deliver = ui._handle_response
    try:
        ui._editor.set_text('unchanged draft')
        item = {'id': 'dialog', 'extension': 'navigator', 'kind': 'session', 'operation': 'navigate',
                'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
        assert ui._queue_extension_navigation(item, True)
        assert ui._extension_navigation is None and ui._extension_navigation_review is not None
        event = {'type': 'extension_dialog_accepted', 'id': 'dialog', 'extension': 'navigator',
                 'operation': 'navigate', 'extension_session': 'session', 'request_id': 'owner',
                 'group_id': ui.group_id if kind == 'claw' else ''}
        if mutation in {'id', 'extension', 'operation'}:
            event[mutation] = 'foreign'
        elif mutation == 'owner':
            if kind == 'coding':
                ui._command.id = 'foreign'
            else:
                event['request_id'] = 'foreign'
        elif mutation == 'epoch':
            ui._output_epoch += 1
        elif mutation == 'closed':
            event['type'] = 'extension_dialog_closed'
        elif mutation == 'ack':
            event.update(type='extension_dialog_ack', accepted=True)
        deliver(event)
        assert (ui._extension_navigation is not None) is (mutation == 'none')
        assert ui._editor.get_text() == 'unchanged draft'
        assert not ui._finish_extension_navigation('owner', False)
        assert ui._extension_navigation is None and ui._extension_navigation_review is None
    finally:
        if kind == 'coding':
            ui._command = None
        else:
            ui._extension_pending = None
        ui._request_exit()
