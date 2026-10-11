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

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("foreign", [False, True])
async def test_live_claw_command_catalog_pages_and_tree_use_group_runtime_not_ui_cache(tmp_path, foreign):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "catalog.sock"),
        pid_file=str(tmp_path / "catalog.pid"),
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
    store.replace_transcript(first.session_id, [{"role": "user", "content": "TREE-CATALOG-CHECKPOINT"}])
    store.save_branch(first.session_id, "branch", [{"role": "user", "content": "BRANCH-CATALOG-CHECKPOINT"}])
    for index in range(60):
        meta = store.create_session("main")
        store.replace_transcript(meta.session_id, [])
    other = store.create_session("foreign-group")
    store.replace_transcript(other.session_id, [{"role": "user", "content": "MUST-NOT-DISCLOSE"}])
    store.activate_session("main", first.session_id)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        before = ui._gateway_session_id
        ui._editor.set_text("parent draft")

        async def command(values):
            ui._extension_command("ext-navigator-catalog", json.dumps(values))
            await wait(lambda: ui._extension_pending is None)
            assert ui._extension_dialog_panel is None and ui._navigation_panel is None
            return runtime._active_session.extensions.entries.for_extension("navigator")

        if foreign:
            entries = await command({"scope": "tree", "session_id": other.session_id})
            assert not entries and runtime.get_new_session_id() == before
        else:
            entries = await command({"scope": "sessions"})
            page1 = entries[-1]["data"]
            assert page1["total"] == 61 and len(page1["entries"]) == 50
            entries = await command(
                {"scope": "sessions", "offset": page1["next_offset"], "revision": page1["revision"]}
            )
            page2 = entries[-1]["data"]
            assert len(page2["entries"]) == 11 and not page2["has_more"]
            assert len({row["id"] for page in (page1, page2) for row in page["entries"]}) == 61
            assert not any(row["session_id"] == other.session_id for page in (page1, page2) for row in page["entries"])
            entries = await command({"scope": "tree"})
            tree = entries[-1]["data"]
            assert tree["session_id"] == before and any(row["branch_id"] == "branch" for row in tree["entries"])
            entries = await command({"scope": "tree", "branch_id": "branch"})
            assert "BRANCH-CATALOG-CHECKPOINT" in entries[-1]["data"]["entries"][0]["title"]
        assert not calls and ui._editor.get_text() == "parent draft"
        assert runtime.get_new_session_id() == before
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
