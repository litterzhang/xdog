"""Bounded current stateless and legacy Streamable HTTP MCP, without call replay."""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast

import httpx
from xdog.agent.mcp.client_capabilities import RootCapability
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.input_requests import INPUT_METHODS, InputRequests
from xdog.agent.mcp.oauth_discovery import OAuthChallenge, challenge
from xdog.agent.mcp.protocol import header_value, modern_discovery
from xdog.agent.mcp.resource_metadata import UNSUPPORTED_WATCH_METHODS, catalog_notice
from xdog.agent.mcp.stdio import CURRENT_VERSION, LEGACY_VERSIONS, MAX_FRAME, McpError


class HttpMcpError(McpError):
    def __init__(self, message: str, *, status: int = 0, code: int | None = None, data: Any = None,
                 auth_challenge: OAuthChallenge | None = None, challenge_invalid: bool = False) -> None:
        super().__init__(message, code=code, data=data)
        self.status = status
        self.auth_challenge = auth_challenge
        self.challenge_invalid = challenge_invalid


MAX_RESUME_ATTEMPTS = 3


@dataclass(slots=True)
class _SseState:
    cursor: str = ""
    retry: float = 0.25


class _SseInterrupted(McpError):
    """Incomplete transport, not invalid protocol data; never authorizes POST replay."""


async def _sse_lines(response: httpx.Response) -> AsyncIterator[bytes]:
    buffer = b""
    async for block in response.aiter_bytes():
        buffer += block
        if len(buffer) > MAX_FRAME:
            raise McpError("MCP SSE line exceeds the frame limit")
        while True:
            lf, cr = buffer.find(b"\n"), buffer.find(b"\r")
            position = min(value for value in (lf, cr) if value >= 0) if lf >= 0 or cr >= 0 else -1
            if position < 0 or buffer[position:] == b"\r":
                break
            delimiter = 2 if buffer[position:position + 2] == b"\r\n" else 1
            line, buffer = buffer[:position], buffer[position + delimiter:]
            yield line
    if buffer.endswith(b"\r"):
        yield buffer[:-1]
    elif buffer:
        raise _SseInterrupted("MCP SSE stream ended with an incomplete event; POST not replayed")


async def _sse_messages(response: httpx.Response, state: _SseState | None = None) -> AsyncIterator[dict[str, Any]]:
    state = state if state is not None else _SseState()
    data: list[bytes] = []
    event_size = 0
    pending_id = state.cursor
    # Preserve UTF-8 and CR/LF boundaries. Cursor commitment waits for a
    # complete event: a truncated successor cannot skip unreceived data.
    async for line in _sse_lines(response):
        if not line:
            state.cursor = pending_id
            payload = b"\n".join(data)
            if payload:
                try:
                    message = json.loads(payload)
                except (ValueError, UnicodeError) as exc:
                    raise McpError("Invalid MCP SSE JSON") from exc
                if not isinstance(message, dict):
                    raise McpError("MCP SSE data must contain one JSON-RPC object")
                yield message
            data, event_size = [], 0
            continue
        field, separator, value = line.partition(b":")
        if separator and value.startswith(b" "):
            value = value[1:]
        if field == b"data":
            data.append(value)
            event_size += len(value) + 1
            if event_size > MAX_FRAME:
                raise McpError("MCP SSE event exceeds the frame limit")
        elif field == b"id" and b"\x00" not in value:
            try:
                identifier = value.decode("utf-8")
            except UnicodeError as exc:
                raise McpError("Invalid MCP SSE event identifier") from exc
            if len(value) > 1024 or any(ord(char) < 0x20 or ord(char) == 0x7f for char in identifier):
                raise McpError("Unsafe/oversized MCP SSE event identifier")
            pending_id = identifier  # Empty id explicitly resets resumption.
        elif field == b"retry" and value and all(48 <= char <= 57 for char in value):
            milliseconds = int(value) if len(value) <= 16 else 5000
            state.retry = min(5.0, max(0.05, milliseconds / 1000))
        # event/comments are not interpreted as tool content.
    if data:
        raise _SseInterrupted("MCP SSE stream ended with an incomplete event; POST not replayed")


class HttpClient:
    def __init__(self, config: McpServerConfig, credential: Callable[[], str | None] | None = None) -> None:
        self.config = config
        self.root_capability = RootCapability(config.roots)
        self.inputs = InputRequests(self.root_capability)
        self._credential = credential
        self.authorization_challenge: OAuthChallenge | None = None
        self.challenge_invalid = False
        self.version = ""
        self.modern = False
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.dirty = False
        self.state = "disconnected"
        self.error = ""
        self._client: httpx.AsyncClient | None = None
        self._headers: dict[str, str] = {}
        self._session = ""
        self._sequence = 0
        self._active: set[asyncio.Task[Any]] = set()

    async def connect(self) -> None:
        if self._client is not None:
            raise McpError("HTTP connection already exists; disconnect before reconnecting")
        self.modern, self.version = False, ""
        self.root_capability.active = False
        self.inputs.active = False
        self.inputs._failed = False
        self.state = "connecting"
        self._headers = dict(self.config.headers)
        for header, variable in self.config.headers_env:
            value = os.environ.get(variable)
            if not value or any(ord(char) < 0x20 or ord(char) > 0x7e for char in value):
                raise McpError("Configured HTTP credential/header environment value is missing or unsafe")
            self._headers[header] = value
        # No redirects, ambient proxies, network retries or automatic OAuth/token
        # requests. Configured credentials go only to this exact endpoint.
        self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                         timeout=self.config.timeout, limits=httpx.Limits(max_connections=24))
        try:
            try:
                response = await self.request("server/discover", {}, version=CURRENT_VERSION)
            except HttpMcpError as exc:
                recognized = exc.code in {-32020, -32021, -32022} or exc.status == 404 and exc.code == -32601
                legacy = (exc.status in {400, 404, 405} and exc.code is None
                          or exc.status == 200 and exc.code == -32601)
                if recognized or not legacy:
                    raise
                self.root_capability.active = True
                response = await self.request("initialize", {
                    "protocolVersion": LEGACY_VERSIONS[0],
                    "capabilities": self.root_capability.advertised(),
                    "clientInfo": {"name": "xdog", "version": "2.4.7"},
                })
                version = response.get("protocolVersion")
                if version not in LEGACY_VERSIONS:
                    raise McpError("Unsupported legacy MCP HTTP protocol version")
                self.version = version
                self.capabilities = response.get("capabilities", {})
                self.server_info = response.get("serverInfo", {})
                if not isinstance(self.capabilities, dict) or not isinstance(self.server_info, dict):
                    raise McpError("Invalid legacy HTTP discovery metadata")
                await self.notify("notifications/initialized", {})
            else:
                self.version, self.capabilities, self.server_info = modern_discovery(response)
                self.modern = True
                self.root_capability.active = True
                self.inputs.active = True
            if not ({"tools", "resources", "prompts"} & self.capabilities.keys()
                    or isinstance(self.capabilities.get("completions"), dict)):
                raise McpError("HTTP server does not advertise a supported capability")
            self.state = "connected"
        except BaseException:
            await self.close()
            raise

    def _request_headers(self, method: str, version: str = "", mirrors: dict[str, str] | None = None) -> dict[str, str]:
        modern = self.modern or bool(version)
        headers = {**self._headers, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        selected = version or self.version
        if selected:
            headers["MCP-Protocol-Version"] = selected
        if modern:
            headers.update({"Mcp-Method": header_value(method), "Mcp-Client-Info": "xdog/2.4.7"})
            headers.update(mirrors or {})
        elif self._session:
            headers["Mcp-Session-Id"] = self._session
        token = self._credential() if self._credential is not None else None
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        return headers

    async def request(self, method: str, params: dict[str, Any], *, version: str = "",
                      mirrors: dict[str, str] | None = None) -> dict[str, Any]:
        if method in UNSUPPORTED_WATCH_METHODS:
            raise McpError("MCP subscriptions are not supported", code=-32601)
        if self.modern and self.state == "connected" and not version and method in INPUT_METHODS:
            return await self.inputs.run(method, params,
                                         lambda value: self._request_once(method, value, mirrors=mirrors))
        result = await self._request_once(method, params, version=version, mirrors=mirrors)
        if (self.modern or version) and result.get('resultType', 'complete') != 'complete':
            raise McpError('Unexpected MCP result type outside an owned input request')
        return result

    async def _request_once(self, method: str, params: dict[str, Any], *, version: str = "",
                            mirrors: dict[str, str] | None = None) -> dict[str, Any]:
        if self.state != "connected" and method not in {"server/discover", "initialize"}:
            raise McpError("HTTP MCP server is not connected; reconnect explicitly, no call replay")
        self._sequence += 1
        identifier = self._sequence
        payload = dict(params)
        if self.modern or version:
            payload["_meta"] = {**payload.get("_meta", {}),
                                "io.modelcontextprotocol/protocolVersion": version or self.version,
                                "io.modelcontextprotocol/clientInfo": {"name": "xdog", "version": "2.4.7"},
                                "io.modelcontextprotocol/clientCapabilities": self.inputs.advertised()}
        message = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": payload}
        task = asyncio.current_task()
        if task is not None:
            self._active.add(task)
        try:
            async with asyncio.timeout(self.config.timeout):
                return await self._post(message, version=version, mirrors=mirrors)
        except asyncio.CancelledError:
            if not self.modern and method not in {"initialize", "server/discover"}:
                try:
                    await self.notify("notifications/cancelled", {"requestId": identifier, "reason": "Client canceled"})
                except (McpError, OSError, TimeoutError):
                    pass
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise McpError("MCP HTTP request failed/timed out; effects may have completed, not replayed") from exc
        finally:
            if task is not None:
                self._active.discard(task)

    async def _post(self, message: dict[str, Any], *, version: str = "",
                    mirrors: dict[str, str] | None = None) -> dict[str, Any]:
        if self._client is None:
            raise McpError("HTTP MCP owner is closed")
        data = json.dumps(message).encode()
        if len(data) > MAX_FRAME:
            raise McpError("MCP HTTP request exceeds the frame limit")
        headers = self._request_headers(str(message.get("method", "")), version, mirrors)
        async with self._client.stream("POST", self.config.url, headers=headers, content=data) as response:
            if response.status_code in {401, 403}:
                raise self._authorization_error(response, "MCP HTTP authorization required; configure explicit consent")
            if 300 <= response.status_code < 400:
                raise HttpMcpError("MCP endpoint redirect refused; credentials were not forwarded",
                                   status=response.status_code)
            if response.status_code == 404 and not version and self._session:
                self.state = "disconnected"
                raise HttpMcpError("MCP session expired; reconnect explicitly, never replay tools/call", status=404)
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if message.get("method") == "initialize" and response.is_success:
                session = response.headers.get("mcp-session-id", "")
                if any(ord(char) < 0x21 or ord(char) > 0x7e for char in session) or len(session) > 512:
                    raise McpError("Unsafe MCP session identifier")
                self._session = session
            if media == "text/event-stream" and response.is_success:
                state = _SseState()
                modern = self.modern or bool(version)
                try:
                    return await self._stream_result(response, message.get("id"), state, modern=modern)
                except (_SseInterrupted, httpx.TransportError) as exc:
                    # Modern transport is stateless and forbids GET. Legacy
                    # resumption requires a server cursor and a known requested/
                    # negotiated version, retrieving the original result with GET.
                    initializing = (message.get("method") == "initialize" and self.state == "connecting"
                                    and message.get("params", {}).get("protocolVersion") in LEGACY_VERSIONS)
                    if modern or not state.cursor or not (self.state == "connected" or initializing):
                        raise McpError(
                            "MCP stream interrupted without resumable state; effects uncertain, no replay") from exc
                    await response.aclose()
                    selected = message["params"]["protocolVersion"] if initializing else ""
                    return await self._resume_result(message.get("id"), state, initializing_version=selected)
            body = bytearray()
            async for block in response.aiter_bytes():
                body.extend(block)
                if len(body) > MAX_FRAME:
                    raise McpError("MCP HTTP response exceeds the frame limit")
            if ("id" not in message or "method" not in message) and response.status_code in {200, 202, 204}:
                return {}
            try:
                incoming = json.loads(body)
            except (ValueError, UnicodeError) as exc:
                raise HttpMcpError("MCP endpoint returned non-protocol HTTP data", status=response.status_code) from exc
            result = await self._message(incoming, message.get("id"), response.status_code,
                                         modern=self.modern or bool(version))
            if result is None:
                raise HttpMcpError("MCP HTTP response did not contain its result", status=response.status_code)
            if not response.is_success:
                raise HttpMcpError("MCP HTTP endpoint rejected request", status=response.status_code)
            return result

    async def _message(self, message: Any, expected: Any, status: int, *, modern: bool) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise HttpMcpError("Invalid MCP HTTP JSON-RPC object", status=status)
        if "method" in message:
            if modern:
                if ("id" not in message and message.get("method") in {"notifications/progress", "notifications/message"}
                        and isinstance(message.get("params"), dict)):
                    return None  # Request-scoped telemetry is not model/tool content.
                raise McpError("Current MCP HTTP streams cannot send unrelated requests/notifications")
            if "id" in message:
                answer = self.root_capability.response(message)
                await self._post({"jsonrpc": "2.0", "id": message["id"], **answer})
            elif catalog_notice(message) == 'tools':
                self.dirty = True  # No background subscriptions or automatic catalog reload.
            return None
        if (type(message.get("id")) is not int or message.get("id") != expected
                or ("result" in message) == ("error" in message)):
            raise McpError("MCP HTTP response identity/shape mismatch; result not accepted")
        if "error" in message:
            error = message["error"]
            if not isinstance(error, dict) or type(error.get("code")) is not int:
                raise McpError("Invalid MCP HTTP error")
            # Do not display response bodies/URLs/headers that may contain credentials.
            raise HttpMcpError("MCP endpoint returned a protocol error", status=status,
                               code=error["code"], data=error.get("data"))
        if not isinstance(message.get("result"), dict):
            raise McpError("Invalid MCP HTTP result")
        return cast(dict[str, Any], message["result"])

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if method in UNSUPPORTED_WATCH_METHODS:
            raise McpError("MCP subscriptions are not supported", code=-32601)
        async with asyncio.timeout(min(3, self.config.timeout)):
            await self._post({"jsonrpc": "2.0", "method": method, "params": params})

    async def _stream_result(self, response: httpx.Response, expected: Any, state: _SseState,
                             *, modern: bool) -> dict[str, Any]:
        async for incoming in _sse_messages(response, state):
            result = await self._message(incoming, expected, response.status_code, modern=modern)
            if result is not None:
                return result
        raise _SseInterrupted("MCP stream ended without its correlated result; POST not replayed")

    def _get_headers(self, state: _SseState, initializing_version: str = "") -> httpx.Headers:
        # Event IDs are UTF-8, including non-ASCII. Header control characters and
        # excessive IDs were rejected by the parser, never sent to another URL.
        headers = self._request_headers("")
        headers["Accept"] = "text/event-stream"
        if initializing_version:
            headers["MCP-Protocol-Version"] = initializing_version
        raw = [(key.encode("ascii"), value.encode("ascii")) for key, value in headers.items()]
        if state.cursor:
            raw.append((b"Last-Event-ID", state.cursor.encode("utf-8")))
        return httpx.Headers(raw)

    def _authorization_error(self, response: httpx.Response, message: str) -> HttpMcpError:
        try:
            hint = challenge(", ".join(response.headers.get_list("www-authenticate")))
        except ValueError:
            self.authorization_challenge, self.challenge_invalid = None, True
            return HttpMcpError("Invalid MCP authorization challenge; no redirect/auth replay",
                                status=response.status_code, challenge_invalid=True)
        self.authorization_challenge, self.challenge_invalid = hint, False
        return HttpMcpError(message, status=response.status_code, auth_challenge=hint)

    def _check_get(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise self._authorization_error(response, "MCP stream authorization required; no automatic credentials")
        if 300 <= response.status_code < 400:
            raise HttpMcpError("MCP stream redirect refused; credentials were not forwarded",
                               status=response.status_code)
        if response.status_code == 404 and self._session:
            self.state = "disconnected"
            raise HttpMcpError("MCP session expired during resumption; reconnect explicitly, no replay", status=404)
        if (response.status_code != 200
                or response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/event-stream"):
            raise HttpMcpError("Legacy MCP stream unavailable; no call replay", status=response.status_code)

    async def _resume_result(self, expected: Any, state: _SseState,
                             *, initializing_version: str = "") -> dict[str, Any]:
        assert self._client is not None
        for _attempt in range(MAX_RESUME_ATTEMPTS):
            if not state.cursor or (self.state != "connected"
                                    and not (initializing_version and self.state == "connecting")):
                break
            await asyncio.sleep(state.retry)
            try:
                async with self._client.stream("GET", self.config.url,
                                               headers=self._get_headers(state, initializing_version)) as response:
                    self._check_get(response)
                    return await self._stream_result(response, expected, state, modern=False)
            except (_SseInterrupted, httpx.TransportError):
                continue
        raise McpError("MCP response resumption exhausted/unavailable; effects uncertain, POST not replayed")

    async def close(self) -> None:
        self.root_capability.active = False
        await self.inputs.close()
        self.state = "disconnected"
        current = asyncio.current_task()
        tasks = tuple(task for task in self._active if task is not current)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            if self._session and not self.modern:
                try:
                    async with asyncio.timeout(min(1, self.config.timeout)):
                        async with self._client.stream("DELETE", self.config.url,
                                                       headers=self._request_headers("")):
                            pass
                except (httpx.HTTPError, TimeoutError):
                    pass
            await self._client.aclose()
        self._client = None
        self._session = ""
        self._headers.clear()
