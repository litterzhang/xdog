"""Owned offline process for real SIGTERM shutdown acceptance; no user services."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime import gateway

root = Path(sys.argv[1])
with_memory = sys.argv[2] == "memory"
config = ClawConfig(model="offline/test", data_dir=str(root / "data"),
                    socket_path=str(root / "gateway.sock"), pid_file=str(root / "gateway.pid"))
gateway._build_model_and_options = lambda config: ("offline/test", lambda *args: EventStream.empty(AssistantMessage()))
server = gateway.GatewayServer(config)
start = server.start


async def ready_start():
    await start()
    assert server._orchestrator is not None
    if with_memory:
        runtime = server._orchestrator._runtimes["main"]
        memory = runtime.memory
        await memory._searcher._indexer.get_all_chunks()
        supervisor = runtime.get_or_create_session().children.supervisor
        await asyncio.to_thread(supervisor._ensure_loop)
    stop = server._orchestrator.stop
    async def slow_stop():
        (root / "cleanup-entered").write_text("yes")
        await asyncio.sleep(0.05)
        await stop()
        if with_memory:
            assert not supervisor._thread.is_alive()
        (root / "cleanup-complete").write_text("yes")
    server._orchestrator.stop = slow_stop
    asyncio.get_running_loop().call_soon((root / "ready").write_text, "yes")


server.start = ready_start
asyncio.run(server.run_forever())
(root / "main-returned").write_text("yes")
