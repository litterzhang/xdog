"""Exercise installed Native busy intake/controls; no source paths or provider.

Copied into the wheel-only Claw environment by verify_acp_artifacts.py.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime import group_controller
from xdog.claw.core.runtime.gateway import GatewayServer


async def main() -> None:
    assert Path(group_controller.__file__).is_relative_to(Path(sys.prefix))
    root = Path(tempfile.mkdtemp(prefix="installed-native-"))
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    def model(_model, context, options=None):
        body = context.messages[-1].content
        prompt = body if isinstance(body, str) else "".join(part.text for part in body if isinstance(part, TextContent))
        calls.append(prompt)
        message = AssistantMessage(content=(TextContent(text="installed answer"),))

        async def events():
            if prompt == "FIRST":
                entered.set()
                await release.wait()
            yield StartEvent(partial=message)
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    config = ClawConfig(
        model="test/dummy",
        data_dir=str(root / "data"),
        socket_path=str(root / "native.sock"),
        pid_file=str(root / "native.pid"),
        groups=(GroupDef(id="main", workspace=str(root / "work")),),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", model)):
        await server.start()
    writers = []

    async def send(writer, value):
        writer.write((json.dumps(value) + "\n").encode())
        await writer.drain()

    async def receive(reader, kind):
        async with asyncio.timeout(5):
            while line := await reader.readline():
                frame = json.loads(line)
                if frame["type"] == kind:
                    return frame
        raise AssertionError(f"No installed native {kind} receipt")

    def request(run, text):
        return {"type": "message", "run_id": run, "group_id": "main", "content": text}

    try:
        r1, w1 = await asyncio.open_unix_connection(config.socket_path)
        r2, w2 = await asyncio.open_unix_connection(config.socket_path)
        r3, w3 = await asyncio.open_unix_connection(config.socket_path)
        writers.extend((w1, w2, w3))
        await send(w1, request("first", "FIRST"))
        await receive(r1, "run_ack")
        await asyncio.wait_for(entered.wait(), 5)
        await send(w2, request("second", "CANCEL THIS QUEUED INPUT"))
        await receive(r2, "run_ack")
        await send(w3, request("third", "THIRD"))
        await receive(r3, "run_ack")
        assert calls == ["FIRST"]
        await send(
            w2,
            {
                "type": "input_control",
                "run_id": "second",
                "group_id": "main",
                "request_id": "control",
                "input_id": "must-not-enter-first",
                "action": "add",
                "content": "forbidden queued steering",
                "delivery": "steer",
                "direction": 0,
            },
        )
        assert not (await receive(r2, "input_ack"))["success"]
        await send(w2, {"type": "abort", "run_id": "second", "group_id": "main"})
        assert (await receive(r2, "abort_ack"))["status"] == "requested"
        await receive(r2, "aborted")
        app = server._orchestrator
        assert app is not None
        inputs = await app.input_service()
        workspace = app.get_group_workspace("main")
        assert workspace is not None
        scope = InputScope("main", workspace)
        assert (await inputs.store.get(scope, "native:first")).status == "running"
        assert (await inputs.store.get(scope, "native:second")).status == "cancelled"
        release.set()
        assert (await receive(r1, "response"))["content"] == "installed answer"
        assert (await receive(r3, "response"))["content"] == "installed answer"
        assert calls == ["FIRST", "THIRD"]
        assert (await inputs.wait(scope, "native:third")).status == "succeeded"
        print(
            json.dumps(
                {
                    "installed_native": "busy commit, queued controls fenced, exact pending cancellation",
                    "model_calls": len(calls),
                    "root": str(root),
                }
            )
        )
    finally:
        release.set()
        for writer in writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in writers))
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
