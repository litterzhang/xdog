from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.extensions.state import validate_entries
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


async def test_actual_gateway_entries_and_context_survive_cache_branch_restore_and_catalog_recovery(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    contexts = []
    def stream(model, context, options=None):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="CLAW-CONTEXT-ANSWER"),)))
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True),),
                        extensions=("contextual",), extension_dir=str(root))
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
        ui._extension_command("ext-contextual-remember", "first")
        await wait(lambda: ui._extension_pending is None and ui._gateway_session_id)
        runtime = server._orchestrator._runtimes["main"]
        session_id = runtime.get_new_session_id()
        ui._handle_submit("request")
        await wait(lambda: len(contexts) == 1 and ui._active_run_id is None)
        assert "EXTENSION-CONTEXT-first" in str(contexts[-1].messages[-1].content)
        assert "EXTENSION-CONTEXT" not in json.dumps(runtime.get_session_info()["history"])
        branch = runtime._active_session.create_branch()
        ui._extension_command("ext-contextual-remember", "second")
        await wait(lambda: ui._extension_pending is None)
        assert len(runtime._active_session.extensions.entries.for_extension("contextual")) == 2
        runtime.navigate_session("branch", session_id, branch, -1, session_id)
        ui._handle_submit("branch request")
        await wait(lambda: len(contexts) == 2 and ui._active_run_id is None)
        assert "EXTENSION-CONTEXT-first" in str(contexts[-1].messages[-1].content)
        runtime._drop_session()
        assert runtime.get_or_create_session().extensions.entries.for_extension(
            "contextual")[0]["data"] == {"text": "first"}
        store = runtime.transcript_store
        (store._sessions_dir / "catalog.json").write_text("{broken")
        recovered = TranscriptStore(store._sessions_dir, owner_group="main")
        assert recovered.get_extension_entries(session_id)["contextual"][0]["data"] == {"text": "first"}
        assert recovered.get_extension_entries(session_id, branch)["contextual"][0]["data"] == {"text": "first"}
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_extension_entry_sidecar_corruption_does_not_silently_reset_plugin_state(tmp_path):
    store = TranscriptStore(tmp_path, owner_group="main")
    session = store.create_session("main")
    for value in ("null", "[]", "{broken"):
        (tmp_path / f"{session.session_id}.extensions.json").write_text(value)
        recovered = TranscriptStore(tmp_path, owner_group="main")
        with pytest.raises(ValueError):
            validate_entries(recovered.get_extension_entries(session.session_id))
