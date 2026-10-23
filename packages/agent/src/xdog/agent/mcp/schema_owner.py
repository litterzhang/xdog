"""Owned cancellable schema helper; limits validation time without an OS sandbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from xdog.agent.async_work import wait_owned
from xdog.agent.mcp.schema import Schema, json_value
from xdog.agent.mcp.stdio import McpError
from xdog.agent.process_io import close_process_pipes

MAX_FRAME = 2 * 1024 * 1024
_BOOTSTRAP = ("import runpy,sys;sys.path.insert(0,sys.argv[1]);"
              "runpy.run_module('xdog.agent.mcp.schema_worker',run_name='__main__')")


@dataclass(frozen=True, slots=True)
class SchemaDocument:
    schema: Schema
    key: str
    input: bool
    legacy: bool
    model_parameters: dict[str, Any] | None = None


class SchemaOwner:
    def __init__(self, *, timeout: float = 3, startup_timeout: float = 10) -> None:
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._sequence = 0
        self._active: asyncio.Task[Any] | None = None

    async def compile(self, value: object, *, input_schema: bool = False, legacy: bool = False) -> SchemaDocument:
        snapshot = json.loads(json_value(value, maximum=256 * 1024))
        key = hashlib.sha256(json_value([snapshot, input_schema, legacy]).encode()).hexdigest()
        document = SchemaDocument(snapshot, key, input_schema, legacy)
        reply = await self._request(document, "compile")
        schema, model = reply.get("schema"), reply.get("model")
        if not isinstance(schema, (dict, bool)) or input_schema and not isinstance(model, dict):
            raise McpError("Invalid local schema compilation result")
        return SchemaDocument(schema, key, input_schema, legacy, model)

    async def validate(self, document: SchemaDocument, value: Any, *, label: str) -> None:
        try:
            await self._request(document, "validate", value)
        except McpError as exc:
            if "timed out" in str(exc) or "closed" in str(exc):
                raise
            raise McpError(f"MCP {label} does not satisfy its JSON Schema") from None

    async def _request(self, document: SchemaDocument, action: str, value: Any = None) -> dict[str, Any]:
        # Validate/serialize the bounded data before a child sees it; no tuple/key/NaN coercion.
        json_value(value)
        request: dict[str, Any] = {
            "action": action,
            "key": document.key,
            "schema": document.schema,
            "input": document.input,
            "legacy": document.legacy,
            "value": value,
        }
        process: asyncio.subprocess.Process | None = None
        phase = 'validation'
        try:
            async with asyncio.timeout(self.timeout) as deadline, self._lock:
                if self._closed:
                    raise McpError("MCP schema owner is closed")
                self._active = asyncio.current_task()
                if self._process is not None and self._process.returncode is not None:
                    await self._stop(self._process)
                if self._process is None:
                    # Cold interpreter/package imports are not schema execution.
                    # Admit them under a separate bounded startup deadline; do
                    # not lengthen the budget for an expensive hostile schema.
                    phase = 'startup'
                    deadline.reschedule(asyncio.get_running_loop().time() + self.startup_timeout)
                    creation = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            sys.executable,
                            "-I",
                            "-c",
                            _BOOTSTRAP,
                            str(Path(__file__).resolve().parents[3]),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            limit=MAX_FRAME,
                        )
                    )
                    try:
                        self._process = await asyncio.shield(creation)
                    except asyncio.CancelledError:
                        # A canceled creation may still own a spawned child. Join
                        # that exact operation and reap it before releasing the lock.
                        try:
                            await wait_owned(creation)
                        finally:
                            # wait_owned re-raises repeated caller cancellation
                            # after settlement; retrieve the actual handle even then.
                            if (creation.done() and not creation.cancelled()
                                    and creation.exception() is None):
                                await self._stop(creation.result())
                        raise
                    process = self._process
                    assert process.stdout is not None
                    ready = await process.stdout.readline()
                    if ready != b'{"schema_helper_ready":1}\n':
                        await self._stop(process)
                        raise McpError('Invalid MCP schema helper readiness; no tool call replay')
                    phase = 'validation'
                    deadline.reschedule(asyncio.get_running_loop().time() + self.timeout)
                process = self._process
                assert process.stdin is not None and process.stdout is not None
                self._sequence += 1
                request["id"] = self._sequence
                raw = json_value(request, maximum=MAX_FRAME - 1).encode() + b"\n"
                process.stdin.write(raw)
                await process.stdin.drain()
                line = await process.stdout.readline()
                if not line or len(line) > MAX_FRAME:
                    await self._stop(process)
                    raise McpError("MCP schema helper disconnected; no tool call was replayed")
                response = json.loads(line)
                if (
                    not isinstance(response, dict)
                    or response.get("id") != self._sequence
                    or type(response.get("ok")) is not bool
                ):
                    await self._stop(process)
                    raise McpError("Invalid MCP schema helper response")
                if not response["ok"]:
                    raise McpError(str(response.get("error", "Invalid MCP schema")))
                return cast(dict[str, Any], response)
        except TimeoutError:
            if process is not None:
                await self._stop(process)
            raise McpError(f"MCP schema {phase} timed out; helper reaped, no tool call replay") from None
        except asyncio.CancelledError:
            if process is not None:
                await self._stop(process)
            raise
        except (OSError, ValueError):
            if process is not None:
                await self._stop(process)
            raise McpError("MCP schema helper failed; no tool call replay") from None
        finally:
            if self._active is asyncio.current_task():
                self._active = None

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        async def stop() -> None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await close_process_pipes(process)
            if self._process is process:
                self._process = None  # Release only after actual pipe/process settlement.
        await wait_owned(asyncio.create_task(stop()))

    async def close(self) -> None:
        self._closed = True
        active = self._active
        if active is not None and active is not asyncio.current_task() and not active.done():
            if not active.cancelling():
                active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        if self._process is not None:
            await self._stop(self._process)
