from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def make(tmp_path):
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
    ui = InteractiveMode(session)
    return ui, session, store, calls


def wait(ui, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        ui._poll()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Catalog command did not settle")


def test_live_coding_command_catalog_pages_tree_branch_and_search_are_actual_host_data(tmp_path):
    ui, session, store, calls = make(tmp_path)
    try:
        before = session.session_id
        session.agent.append_message(UserMessage(content="TREE-METADATA-ONLY"))
        session._persist()
        branch = session.create_branch(at_index=0, label="CATALOG-BRANCH")
        for index in range(60):
            saved = store.create_session(working_dir=tmp_path, summary=f"CATALOG-SESSION-{index}")
            store.save_session(saved)
        ui._editor.set_text("parent draft")

        def command(values):
            ui._handle_slash_command("ext-navigator-catalog", json.dumps(values))
            wait(ui, lambda: ui._command is None)
            assert ui._extension_dialog_panel is None and ui._navigation_panel is None
            return session.extensions.entries.for_extension("navigator")[-1]["data"]

        first = command({"scope": "sessions"})
        assert first["total"] == 61 and len(first["entries"]) == 50 and first["next_offset"] == 50
        second = command({"scope": "sessions", "offset": first["next_offset"], "revision": first["revision"]})
        assert len(second["entries"]) == 11 and not second["has_more"]
        assert len({row["id"] for batch in (first, second) for row in batch["entries"]}) == 61
        filtered = command({"scope": "sessions", "query": "CATALOG-SESSION-59"})
        assert filtered["total"] == 1 and filtered["entries"][0]["title"] == "CATALOG-SESSION-59"
        tree = command({"scope": "tree"})
        assert tree["session_id"] == before and any(row["branch_id"] == branch for row in tree["entries"])
        assert any("TREE-METADATA-ONLY" in row["title"] for row in tree["entries"])
        branch_page = command({"scope": "tree", "branch_id": branch})
        assert branch_page["entries"][0]["kind"] == "checkpoint" and branch_page["branch_id"] == branch
        assert session.session_id == before and not calls and ui._editor.get_text() == "parent draft"
        assert session.permissions.mode == "ask" and session.messages[0].role == "user"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_canceling_command_retains_catalog_disk_owner_until_read_settles(tmp_path, monkeypatch):
    ui, session, store, calls = make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store.list_sessions

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "list_sessions", slow)
    try:
        ui._editor.set_text("preserved draft")
        ui._handle_slash_command("ext-navigator-catalog", "")
        assert entered.wait(3)
        ui._cancel_command()
        ui._poll()
        assert ui._command is not None  # Owned filesystem operation still settling.
        release.set()
        wait(ui, lambda: ui._command is None)
        assert not session.extensions.entries.for_extension("navigator")
        assert not calls and ui._editor.get_text() == "preserved draft"
    finally:
        release.set()
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()
