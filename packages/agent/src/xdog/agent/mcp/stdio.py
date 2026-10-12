"""Owned asynchronous MCP stdio transport, including current and legacy negotiation."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.agent.mcp.client_capabilities import RootCapability, advertised
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.input_requests import INPUT_METHODS, InputRequests
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.subscriptions import CATALOGS, ResourceSubscriptions, ResourceWatch, resource_uri

CURRENT_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_FRAME = 8 * 1024 * 1024


class McpError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.data = code, data


class StdioClient:
    """One process/reader and multiplexed requests. Never automatically replay calls."""
    def __init__(self, config: McpServerConfig, cwd: Path) -> None:
        self.config, self.cwd = config, cwd
        self.root_capability = RootCapability(config.roots)
        self.reverse = ReverseRequests()
        self.inputs = InputRequests(self.root_capability, self.reverse)
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._sequence = 0
        self.version = ""
        self.modern = False
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.dirty = False
        self.state = "disconnected"
        self.error = ""
        self.resources = ResourceSubscriptions()
        self.stderr_tail: deque[bytes] = deque(maxlen=4)

    async def connect(self) -> None:
        if self.process is not None:
            raise McpError("Server already owns a process; disconnect before reconnecting")
        self.modern, self.version = False, ""
        self.root_capability.active = False
        self.reverse.active = False
        self.inputs.active = False
        self.inputs._failed = False
        self.state = "connecting"
        self.process = await asyncio.create_subprocess_exec(
            *self.config.command, cwd=self.cwd, env={**os.environ, **dict(self.config.env)},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME, start_new_session=os.name == "posix",
        )
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._drain_stderr())
        try:
            try:
                response = await self.request("server/discover", {}, version=CURRENT_VERSION)
            except McpError as exc:
                if exc.code in {-32020, -32021, -32022}:
                    raise
                # Current specification recommends legacy initialize only when
                # discover is not recognized, not when negotiation is rejected.
                if exc.code != -32601:
                    raise
                self.root_capability.active = True
                self.reverse.active = True
                response = await self.request("initialize", {
                    "protocolVersion": LEGACY_VERSIONS[0],
                    "capabilities": {**self.root_capability.advertised(), **self.reverse.advertised()},
                    "clientInfo": {"name": "xdog", "version": "2.3.1"},
                })
                chosen = response.get("protocolVersion")
                if chosen not in LEGACY_VERSIONS:
                    raise McpError("Server selected an unsupported legacy protocol version")
                self.version = chosen
                await self.reverse.negotiated(chosen)
                await self.notify("notifications/initialized")
            else:
                from xdog.agent.mcp.protocol import modern_discovery
                self.version, self.capabilities, self.server_info = modern_discovery(response)
                self.modern = True
                await self.reverse.negotiated(self.version)
                self.root_capability.active = True
                self.inputs.active = True
            if not self.modern and (not isinstance(response.get("capabilities", {}), dict)
                    or not isinstance(response.get("serverInfo", {}), dict)):
                raise McpError("Invalid server discovery metadata")
            if not self.modern:
                self.capabilities, self.server_info = response.get("capabilities", {}), response.get("serverInfo", {})
            if not ({"tools", "resources", "prompts"} & self.capabilities.keys()
                    or isinstance(self.capabilities.get("completions"), dict)):
                raise McpError("Server does not advertise a supported capability")
            self.state = "connected"
        except BaseException:
            await self.close()
            raise

    async def _write(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise McpError("MCP server disconnected; previous effects may have completed, no replay")
        data = (json.dumps(message, ensure_ascii=True) + "\n").encode()
        if len(data) > MAX_FRAME:
            raise McpError("MCP request exceeds the frame limit")
        async with self._write_lock:
            process.stdin.write(data)
            try:
                await asyncio.wait_for(process.stdin.drain(), self.config.timeout)
            except (OSError, TimeoutError) as exc:
                raise McpError("MCP write failed; outcome uncertain, not replayed") from exc

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def request(self, method: str, params: dict[str, Any], *, version: str = "") -> dict[str, Any]:
        if self.modern and self.state == "connected" and not version and method in INPUT_METHODS:
            return await self.inputs.run(method, params, lambda value: self._request_once(method, value))
        result = await self._request_once(method, params, version=version)
        if (self.modern or version) and result.get('resultType', 'complete') != 'complete':
            raise McpError('Unexpected MCP result type outside an owned input request')
        return result

    async def _request_once(self, method: str, params: dict[str, Any], *, version: str = "") -> dict[str, Any]:
        if self.state != "connected" and method not in {"server/discover", "initialize"}:
            raise McpError("MCP server is not connected; reconnect explicitly, calls are not replayed")
        self._sequence += 1
        identifier = self._sequence
        payload = dict(params)
        if self.modern or version:
            payload["_meta"] = {**payload.get("_meta", {}),
                                "io.modelcontextprotocol/protocolVersion": version or self.version,
                                "io.modelcontextprotocol/clientInfo": {"name": "xdog", "version": "2.3.1"},
                                "io.modelcontextprotocol/clientCapabilities": self.inputs.advertised()}
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        sent = False
        try:
            await self._write({"jsonrpc": "2.0", "id": identifier, "method": method, "params": payload})
            sent = True
            return await asyncio.wait_for(asyncio.shield(future), self.config.timeout)
        except (TimeoutError, asyncio.CancelledError):
            if sent and not self.modern and method not in {"initialize", "server/discover"}:
                # Best effort. Cancellation is NOT a rollback guarantee.
                try:
                    await self.notify("notifications/cancelled", {"requestId": identifier, "reason": "Client canceled"})
                except (McpError, OSError):
                    pass
            raise
        finally:
            self._pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Consume an exception racing a timeout/cancel.

    async def watch_resource(self, uri: str, sink: Callable[[dict[str, Any]], None]) -> ResourceWatch:
        uri = resource_uri(uri)
        resources = self.capabilities.get('resources')
        if self.state != 'connected' or not isinstance(resources, dict) or resources.get('subscribe') is not True:
            raise McpError('Server does not advertise resource subscriptions')
        async with self.resources.lock:
            shared = any(watch.uri == uri and watch.state == 'active' for watch in self.resources.watches.values())
            watch = self.resources.add(uri, sink, self.stop_resource_watch)
            try:
                if self.modern:
                    await self._write({'jsonrpc': '2.0', 'id': watch.id, 'method': 'subscriptions/listen', 'params': {
                        '_meta': {'io.modelcontextprotocol/protocolVersion': self.version,
                                  'io.modelcontextprotocol/clientInfo': {'name': 'xdog', 'version': '2.3.1'},
                                  'io.modelcontextprotocol/clientCapabilities': advertised()},
                        'notifications': {'resourceSubscriptions': [uri]}}})
                    await asyncio.wait_for(asyncio.shield(watch.ready), self.config.timeout)
                else:
                    if not shared:
                        await self.request('resources/subscribe', {'uri': uri})
                    watch.activate()
                return watch
            except BaseException:
                self.resources.remove(watch, 'failed')
                if self.modern:
                    try:
                        await self.notify('notifications/cancelled', {'requestId': watch.id})
                    except (McpError, OSError):
                        pass
                raise

    async def watch_catalog(self, kind: str, sink: Callable[[dict[str, Any]], None]) -> ResourceWatch:
        capability = self.capabilities.get(kind)
        if (kind not in CATALOGS or self.state != 'connected' or not isinstance(capability, dict)
                or not self.modern and capability.get('listChanged') is not True):
            raise McpError('Server does not advertise this catalog change capability')
        async with self.resources.lock:
            watch = self.resources.add('', sink, self.stop_resource_watch, kind=kind)
            try:
                if self.modern:
                    await self._write({'jsonrpc': '2.0', 'id': watch.id, 'method': 'subscriptions/listen', 'params': {
                        '_meta': {'io.modelcontextprotocol/protocolVersion': self.version,
                                  'io.modelcontextprotocol/clientInfo': {'name': 'xdog', 'version': '2.3.1'},
                                  'io.modelcontextprotocol/clientCapabilities': advertised()},
                        'notifications': watch.filters()}})
                    await asyncio.wait_for(asyncio.shield(watch.ready), self.config.timeout)
                else:
                    watch.activate()  # Legacy declared list-change notices need no resource subscribe/read.
                return watch
            except BaseException:
                self.resources.remove(watch, 'failed')
                if self.modern:
                    try:
                        await self.notify('notifications/cancelled', {'requestId': watch.id})
                    except (McpError, OSError):
                        pass
                raise

    async def watch_notifications(self, selected: dict[str, Any],
                                  sink: Callable[[dict[str, Any]], None]) -> ResourceWatch:
        from xdog.agent.mcp.subscriptions import notification_filters
        filters = notification_filters(selected)
        if not self.modern or self.state != 'connected':
            raise McpError('Combined notification filters require a connected current-protocol server')
        if any(not isinstance(self.capabilities.get(kind), dict)
               for kind, flag in CATALOGS.items() if filters.get(flag)):
            raise McpError('Server does not advertise a requested catalog capability')
        resources = self.capabilities.get('resources')
        if filters.get('resourceSubscriptions') and (not isinstance(resources, dict)
                                                    or resources.get('subscribe') is not True):
            raise McpError('Server does not advertise resource subscriptions')
        async with self.resources.lock:
            watch = self.resources.add_notifications(filters, sink, self.stop_resource_watch)
            try:
                await self._write({'jsonrpc': '2.0', 'id': watch.id, 'method': 'subscriptions/listen', 'params': {
                    '_meta': {'io.modelcontextprotocol/protocolVersion': self.version,
                              'io.modelcontextprotocol/clientInfo': {'name': 'xdog', 'version': '2.3.1'},
                              'io.modelcontextprotocol/clientCapabilities': advertised()},
                        'notifications': watch.filters()}})
                await asyncio.wait_for(asyncio.shield(watch.ready), self.config.timeout)
                return watch
            except BaseException:
                self.resources.remove(watch, 'failed')
                try:
                    await self.notify('notifications/cancelled', {'requestId': watch.id})
                except (McpError, OSError):
                    pass
                raise

    async def stop_resource_watch(self, watch: ResourceWatch) -> None:
        async with self.resources.lock:
            owned = self.resources.watches.get(watch.id) is watch
            self.resources.remove(watch)
            if not owned or self.state != 'connected':
                return
            if self.modern:
                await self.notify('notifications/cancelled', {'requestId': watch.id})
            elif watch.kind == 'resource' and not any(other.kind == 'resource' and other.uri == watch.uri
                                                      for other in self.resources.watches.values()):
                await self.request('resources/unsubscribe', {'uri': watch.uri})

    def _internal_notification(self, message: dict[str, Any]) -> bool:
        """Reserved adapter hook; ordinary MCP never admits unsolicited current frames."""
        return False

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    raise McpError("MCP stdout closed; outstanding effects may have completed, no replay")
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise McpError("Invalid MCP JSON-RPC frame")
                if self._internal_notification(message):
                    continue
                if self.modern and self.resources.modern_frame(message):
                    continue
                if "method" in message:
                    if self.modern:
                        raise McpError("Current MCP stdio servers cannot send unsolicited requests/notifications")
                    if self.reverse.notification(message) or self.reverse.cancel(message):
                        continue
                    if "id" in message:
                        if self.reverse.admit(message, self._write, timeout=self.config.timeout):
                            continue
                        response = self.root_capability.response(message)
                        await self._write({"jsonrpc": "2.0", "id": message["id"], **response})
                    elif message.get("method") in {"notifications/" + kind + "/list_changed" for kind in CATALOGS}:
                        kind = str(message["method"]).split('/')[1]
                        self.dirty = self.dirty or kind == 'tools'
                        self.resources.legacy_catalog_notice(kind)
                    elif message.get("method") == "notifications/resources/updated":
                        self.resources.legacy_notice(message.get("params"))
                    continue
                identifier = message.get("id")
                if type(identifier) is not int:
                    raise McpError("Invalid MCP response identity")
                if ("result" in message) == ("error" in message):
                    raise McpError("MCP response must contain exactly one result or error")
                future = self._pending.get(identifier)
                if future is None or future.done():
                    continue  # Late canceled response cannot settle a successor.
                if "error" in message:
                    error = message["error"]
                    if not isinstance(error, dict) or type(error.get("code")) is not int:
                        raise McpError("Invalid MCP error response")
                    future.set_exception(McpError(str(error.get("message", "MCP error")),
                                                  code=error["code"], data=error.get("data")))
                elif isinstance(message.get("result"), dict):
                    future.set_result(message["result"])
                else:
                    raise McpError("Invalid MCP result response")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state, self.error = "disconnected", type(exc).__name__
            self.root_capability.active = False
            await self.inputs.close(failed=True)
            await self.reverse.close()
            self.resources.close()
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(McpError("MCP connection lost; outcome uncertain, not replayed"))

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while block := await self.process.stderr.read(4096):
            self.stderr_tail.append(block)

    async def close(self) -> None:
        self.root_capability.active = False
        await self.inputs.close()
        await self.reverse.close()
        process = self.process
        self.state = "disconnected"
        self.resources.close()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(McpError("MCP connection closed; outcome uncertain"))
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 0.2)
            except TimeoutError:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 0.5)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (self._reader, self._stderr) if task is not None),
                             return_exceptions=True)
        self.process = None
