import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


def ui(tmp_path):
    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    data = manager.create_session(model="test", working_dir=tmp_path)
    value = AgentSession(
        agent=Agent(lambda *_: EventStream.empty(AssistantMessage(content=())), config=AgentConfig(model="test")),
        session_data=data, session_manager=manager, settings=SettingsManager(), tool_registry=None, bash=None,
        working_dir=tmp_path,
    )
    started, notices = [], []
    result = SimpleNamespace(
        _session=value, _worker_active=False, _worker_thread=None, _command=None, _command_thread=None,
        _child_image_starting=False, _exit_requested=False, _is_busy=False, _pending_messages=[], _routed_inputs={},
        _editor=SimpleNamespace(get_text=lambda: ""), _composer=SimpleNamespace(items={}),
        _event_queue=queue.Queue(), _chat_log=SimpleNamespace(add_system=notices.append),
        _queue_lock=threading.Lock(), _extension_navigation=None, _extension_navigation_review=None,
        _terminal_worker=SimpleNamespace(busy=False),
        _start_turn=lambda message, **options: started.append((message, options)),
    )
    result.started, result.notices = started, notices
    return result


def admit(value):
    body = json.loads((Path(__file__).resolve().parents[3] / "docs/fixtures/eyes-host-v1.json").read_text())
    value._session.external_events.inbox.register("fixture-observer", "fixture-route", "Review tests")
    value._session.external_events.inbox.human_input()
    assert value._session.external_events.inbox.accept(body) == "accepted"


@pytest.mark.parametrize("block", ["worker", "command", "children", "context", "compaction", "authorization", "draft"])
def test_native_dispatch_waits_for_all_owners_and_human_input(tmp_path, block, monkeypatch):
    from xdog.coding.modes.interactive.external_events import NativeExternalEvents

    value = ui(tmp_path)
    adapter = NativeExternalEvents(value)
    adapter.start()
    try:
        admit(value)
        if block == "worker":
            value._worker_active = True
        elif block == "command":
            value._command = object()
        elif block == "children":
            monkeypatch.setattr(type(value._session.children.supervisor), "active", property(lambda _: True))
        elif block == "context":
            value._session._context_changes = 1
        elif block == "compaction":
            value._session._compacting = True
        elif block == "authorization":
            value._session._auth_changing = True
        else:
            value._editor.get_text = lambda: "human draft"
        adapter.poll()
        assert value.started == []
        monkeypatch.undo()
        value._worker_active = False
        value._command = None
        value._session._context_changes = 0
        value._session._compacting = False
        value._session._auth_changing = False
        value._editor.get_text = lambda: ""
        adapter.poll()
        assert len(value.started) == 1
        message, options = value.started[0]
        assert json.loads(message)["instruction"] == "Review tests"
        assert options == {"echo": False, "input_origin": "external_event"}
        assert any("External event" in notice for notice in value.notices)
        adapter.finish(message, status="succeeded")
        assert value._session.external_events.inbox.snapshot()["processing"][0]["status"] == "succeeded"
    finally:
        value._session.dispose()
