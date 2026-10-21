"""Loop-owned MCP connections bridged into the existing Agent tool interface."""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.async_work import wait_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.cross_loop import submit_remote
from xdog.agent.mcp.client_capabilities import advertised, inventory
from xdog.agent.mcp.completion import McpCompletion, bounded, values
from xdog.agent.mcp.completion import arguments as completion_arguments
from xdog.agent.mcp.config import parse_servers
from xdog.agent.mcp.content import content as validate_content
from xdog.agent.mcp.context import McpDocument, McpEntry, context_entry, prompt_blocks, resource_blocks
from xdog.agent.mcp.http import HttpClient, HttpMcpError
from xdog.agent.mcp.oauth import OAuthOwner
from xdog.agent.mcp.oauth_discovery import OAuthChallenge
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore, storage_backend
from xdog.agent.mcp.oauth_store import CredentialStore, SavedCredential, capture, restore
from xdog.agent.mcp.protocol import argument_headers, header_value, schema_headers
from xdog.agent.mcp.resource_metadata import ResourceMetadataError, resource_uri
from xdog.agent.mcp.schema import Schema, compile_schema, json_value
from xdog.agent.mcp.schema_owner import SchemaDocument, SchemaOwner
from xdog.agent.mcp.stdio import McpError, StdioClient
from xdog.ai.types import ImageContent, TextContent

T = TypeVar("T")


def _schema(value: object) -> dict[str, Any]:
    """Compatibility codec for trusted extension declarations; MCP uses its owned helper."""
    if not isinstance(value, dict) or value.get("type") != "object":
        raise McpError("Tool schema must describe an object")
    compiled = compile_schema(value, input_schema=True, legacy=True)
    assert compiled.model_parameters is not None
    return compiled.model_parameters


@dataclass(frozen=True, slots=True)
class McpTool:
    server: str
    remote_name: str
    alias: str
    description: str
    input_schema: dict[str, Any]
    output_schema: Schema | None
    input_validation: SchemaDocument
    output_validation: SchemaDocument | None
    headers: tuple[tuple[tuple[str, ...], str, str], ...] = ()
    registration_id: str = ""  # Optional owned adapter provenance; generic MCP leaves it unset.


@dataclass(frozen=True, slots=True)
class _CredentialConsent:
    id: str
    lease: str
    operation: str
    created: float
    record: SavedCredential = field(repr=False)
    stamp: tuple[Any, ...] = field(repr=False)



class McpManager:
    """One background loop survives Coding's per-turn loops. No automatic startup."""
    def __init__(self, configs: Mapping[str, Any], cwd: Path, *, credential_dir: Path | None = None,
                 credential_backend: str = 'file') -> None:
        self.configs = {config.name: config for config in parse_servers(configs)}
        self.cwd = cwd
        selected_backend = storage_backend(credential_backend)
        self.credential_store = (None if credential_dir is None else
            NativeCredentialStore(credential_dir, backend=selected_backend) if selected_backend != 'file'
            else CredentialStore(credential_dir))
        self._credential_consents: dict[str, _CredentialConsent] = {}
        self._clients: dict[str, StdioClient | HttpClient] = {}
        self._oauth: dict[str, OAuthOwner] = {}
        self._auth_challenges: dict[str, tuple[str, object, OAuthChallenge | None]] = {}
        self._auth_leases: dict[str, str] = {}
        self._auth_preparing: dict[str, asyncio.Task[Any]] = {}
        self._tools: tuple[McpTool, ...] = ()
        self._rejected: dict[str, tuple[str, ...]] = {}
        self._completion_at: dict[str, float] = {}
        self._schemas = SchemaOwner()
        self._completion_locks: dict[str, asyncio.Lock] = {}
        self._completion_waiters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False
        self._close_future: concurrent.futures.Future[None] | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._closed:
                raise McpError("MCP manager is closed")
            if self._thread is None:
                def own() -> None:
                    loop = asyncio.new_event_loop()
                    self._loop = loop
                    asyncio.set_event_loop(loop)
                    self._ready.set()
                    try:
                        loop.run_forever()
                    finally:
                        tasks = asyncio.all_tasks(loop)
                        for task in tasks:
                            task.cancel()
                        if tasks:
                            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        # Cancelling an asyncio.to_thread task does not stop its
                        # actual IO. Keep this owner alive until those workers
                        # settle, even if close waiters have been cancelled.
                        loop.run_until_complete(loop.shutdown_default_executor())
                        loop.close()
                self._thread = threading.Thread(target=own, daemon=True, name="xdog-mcp-owner")
                self._thread.start()
            if not self._ready.wait(3) or self._loop is None:
                raise McpError("MCP owner loop did not start")
            return self._loop

    async def _bridge(self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            loop = await asyncio.to_thread(self._ensure_loop)
        except BaseException:
            coroutine.close()
            raise
        with self._start_lock:
            if self._closed:
                coroutine.close()
                raise McpError("MCP manager is closed")
            pending = submit_remote(loop, coroutine)
        return await pending

    def status(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            tools = self._tools
            return tuple({"server": name,
                          "state": self._clients[name].state if name in self._clients else "disconnected",
                          "protocol": self._clients[name].version if name in self._clients else "",
                          "tools": sum(tool.server == name for tool in tools),
                          "changed": self._clients[name].dirty if name in self._clients else False,
                          "allow_tools": config.allow_tools, "transport": config.transport,
                          "client_capabilities": (self._clients[name].inputs.advertised()
                                                  if name in self._clients and self._clients[name].modern else
                                                  self._clients[name].root_capability.advertised()
                                                  if name in self._clients else advertised()),
                          "client_capability_support": {**inventory(), 'roots': (
                              ('modern-configured' if name in self._clients and self._clients[name].modern
                               else 'legacy-configured') if config.roots else 'unsupported')},
                          "rejected_tools": self._rejected.get(name, ()),
                          "error": self._clients[name].error if name in self._clients else ""}
                         for name, config in self.configs.items())

    def context_aliases(self) -> dict[str, tuple[str, str]]:
        """Copied original MCP alias provenance, not inferred from arbitrary tool names."""
        with self._lock:
            return {tool.alias: (tool.server, tool.remote_name) for tool in self._tools}






    async def _credential_disk(self, action: Callable[[Callable[[], bool]], Any],
                               alive: Callable[[], bool]) -> Any:
        canceled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(action, lambda: not canceled.is_set() and alive()))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    continue
            if not task.cancelled():
                task.exception()
            raise

    async def _credential_action(self, name: str, action: str, receipt: str, lease: str,
                                 owner: OAuthOwner, alive: Callable[[], bool]) -> dict[str, Any]:
        store = self.credential_store
        if store is None:
            raise McpError('Private credential persistence is not configured for this host')
        base = action.removesuffix('_prepare')
        if base not in {'credentials_save', 'credentials_restore', 'credentials_forget'}:
            raise McpError('Unknown credential storage operation')
        def live() -> bool:
            return (self._auth_leases.get(name) == lease and alive() and self._oauth.get(name) is owner)
        self._auth_leases[name] = lease
        task = asyncio.current_task()
        assert task is not None
        self._auth_preparing[name] = task
        try:
            if action.endswith('_prepare'):
                if len(self._credential_consents) >= 16 and name not in self._credential_consents:
                    raise McpError('Credential consent capacity reached')
                stamp = (owner._token, owner._refresh, owner._grant, owner._expires)
                snapshot = OAuthOwner(owner.resource, owner.config)
                snapshot._token, snapshot._refresh, snapshot._grant, snapshot._expires = stamp
                record = (await self._credential_disk(lambda _guard: capture(snapshot), live)
                          if base == 'credentials_save' else
                          await self._credential_disk(lambda guard: store.read(owner.resource, guard), live))
                if base == 'credentials_save' and stamp != (owner._token, owner._refresh, owner._grant, owner._expires):
                    raise McpError('Credential grant changed while preparing save review')
                if base == 'credentials_restore':
                    probe = OAuthOwner(owner.resource, owner.config)
                    await self._credential_disk(lambda _guard: restore(probe, record), live)
                if not live():
                    raise McpError('Credential review owner changed')
                identifier = secrets.token_hex(16)
                stamp = (owner._token, owner._refresh, owner._grant, owner._expires)
                prepared = _CredentialConsent(identifier, lease, base, time.monotonic(), record, stamp)
                self._credential_consents[name] = prepared
                return {**store.public(record, base), 'review_id': identifier}
            consent = self._credential_consents.pop(name, None)
            if (consent is None or consent.id != receipt or consent.lease != lease or consent.operation != base
                    or time.monotonic() - consent.created > 300 or not live()):
                raise McpError("Prepare and confirm this view's current credential review first")
            if base == 'credentials_save':
                if (owner._token, owner._refresh, owner._grant, owner._expires) != consent.stamp:
                    raise McpError('Credential grant changed after save review')
                await self._credential_disk(lambda guard: store.save(consent.record, guard), live)
            else:
                current = await self._credential_disk(lambda guard: store.read(owner.resource, guard), live)
                if current != consent.record or not live():
                    raise McpError('Saved credential changed after review')
                if base == 'credentials_restore':
                    if owner.flow is not None and owner.flow.status == 'waiting':
                        raise McpError('Cancel the pending authorization before explicit restore')
                    snapshot = OAuthOwner(owner.resource, owner.config)
                    await self._credential_disk(lambda _guard: restore(snapshot, current), live)
                    if not live():
                        raise McpError('Credential restore owner changed before admission')
                    owner._token, owner._refresh = snapshot._token, snapshot._refresh
                    owner._expires, owner._grant = snapshot._expires, snapshot._grant
                    owner.flow, owner.review, owner._refresh_review = None, None, None
                    owner._refresh_state = "idle"
                else:
                    await self._credential_disk(lambda guard: store.forget(owner.resource, guard), live)
            if not live():
                raise McpError('Credential operation owner changed; inspect storage before retrying')
            return {**owner.status(), 'storage_state': {'credentials_save': 'saved', 'credentials_restore': 'restored',
                                      'credentials_forget': 'forgotten_saved'}[base],
                    'review': owner._grant.to_dict() if owner._grant is not None else consent.record.review,
                    'notice': 'Explicit local credential operation completed; no connection, token POST or tool grant'}
        finally:
            if self._auth_preparing.get(name) is task:
                self._auth_preparing.pop(name, None)

    async def manage_auth(self, name: str, action: str, review_id: str = "", *, lease: str = "sdk",
                          alive: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Caller-owned consent; no agent tools or permission decisions."""
        return await self._bridge(self._manage_auth(name, action, review_id, lease, alive or (lambda: True)))

    async def detach_auth(self, lease: str) -> None:
        await self._bridge(self._detach_auth(lease))

    async def _detach_auth(self, lease: str) -> None:
        for name, owner_lease in tuple(self._auth_leases.items()):
            if owner_lease != lease:
                continue
            self._auth_leases.pop(name, None)  # Invalidates guards BEFORE waiting for remote settlement.
            self._credential_consents.pop(name, None)
            task = self._auth_preparing.get(name)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            owner = self._oauth.get(name)
            if owner is not None:
                owner.review = None
                await owner.cancel()  # Finished credentials persist; pending code exchanges do not.

    async def _manage_auth(self, name: str, action: str, review_id: str, lease: str,
                           alive: Callable[[], bool]) -> dict[str, Any]:
        storage = action.startswith("credentials_")
        if not storage and action not in {"prepare", "start", "refresh_prepare", "refresh",
                                          "status", "cancel", "forget"}:
            raise McpError("Unknown MCP authorization action")
        if not isinstance(lease, str) or not lease or len(lease) > 512:
            raise McpError("Invalid OAuth consent lease")
        previous = self._auth_leases.get(name)
        if ((storage or action in {"prepare", "start", "refresh_prepare", "refresh", "cancel", "forget"})
                and previous is not None and previous != lease):
            raise McpError("OAuth consent belongs to another view/connection")
        if (storage or action in {"prepare", "start", "refresh_prepare", "refresh", "forget"}) and not alive():
            raise McpError("OAuth consent owner is no longer active")
        config = self.configs.get(name)
        owner = self._oauth.get(name)
        if action == "forget" and owner is not None:
            forget_store, resource = self.credential_store, owner.resource
            if forget_store is not None:
                await self._credential_disk(lambda guard: forget_store.forget(resource, guard), alive)
            self._credential_consents.pop(name, None)
            await self._disconnect(name)
            await owner.close()
            self._oauth.pop(name, None)
            self._auth_leases.pop(name, None)
            return {"state": "forgotten", "notice": "Local credentials erased; remote revocation is not implied"}
        if config is None or config.oauth is None:
            raise McpError("Configure an explicit pre-registered OAuth profile for this HTTP resource")
        if owner is not None and (owner.resource != config.url or owner.config != config.oauth):
            raise McpError("OAuth resource/profile changed; forget credentials before a fresh authorization")
        if owner is None:
            owner = OAuthOwner(config.url, config.oauth)
            self._oauth[name] = owner
        if storage:
            return await self._credential_action(name, action, review_id, lease, owner, alive)
        if action == "refresh_prepare":
            self._auth_leases[name] = lease
            owner.guard = lambda: self._auth_leases.get(name) == lease and alive()
            try:
                return await owner.prepare_refresh()
            except BaseException:
                if previous is None and self._auth_leases.get(name) == lease:
                    self._auth_leases.pop(name, None)
                raise
        if action == "refresh":
            if self._auth_leases.get(name) != lease:
                raise McpError("Prepare and confirm this view's refresh review first")
            owner.guard = lambda: self._auth_leases.get(name) == lease and alive()
            task = asyncio.current_task()
            assert task is not None
            self._auth_preparing[name] = task  # Detach cancels/settles the actual potentially rotating POST.
            try:
                store = self.credential_store
                if store is not None:
                    review = owner._refresh_review
                    if review is None or review.id != review_id or time.monotonic() - review.created > 300:
                        raise McpError('Refresh consent changed; saved credentials not consumed')
                    await self._credential_disk(lambda guard: store.forget(owner.resource, guard), alive)
                return await owner.refresh(review_id)
            finally:
                if self._auth_preparing.get(name) is task:
                    self._auth_preparing.pop(name, None)
        if action == "prepare":
            self._auth_leases[name] = lease
            owner.guard = lambda: self._auth_leases.get(name) == lease and alive()
            task = asyncio.current_task()
            assert task is not None
            self._auth_preparing[name] = task
            try:
                retained = self._auth_challenges.get(name)
                client = self._clients.get(name)
                if (isinstance(client, HttpClient)
                        and (client.authorization_challenge is not None or client.challenge_invalid)):
                    retained = client.config.url, client.config.oauth, client.authorization_challenge
                applicable = retained is not None and retained[:2] == (config.url, config.oauth)
                if applicable and retained is not None and retained[2] is None:
                    raise McpError("Malformed resource authentication challenge; it was not ignored for fallback")
                hint = retained[2] if applicable and retained is not None else None
                result = await owner.prepare(hint)
                if self._auth_leases.get(name) != lease or not alive():
                    raise McpError("OAuth review became stale before delivery")
                return result
            except BaseException:
                if self._auth_leases.get(name) == lease:
                    self._auth_leases.pop(name, None)
                    owner.review = None
                raise
            finally:
                if self._auth_preparing.get(name) is task:
                    self._auth_preparing.pop(name, None)
        if action == "start":
            if self._auth_leases.get(name) != lease:
                raise McpError("Prepare and confirm this view's OAuth review first")
            owner.guard = lambda: self._auth_leases.get(name) == lease and alive()
            return await owner.start(review_id)
        if action == "cancel":
            if previous == lease:
                await self._detach_auth(lease)
            else:
                owner.review = None
                await owner.cancel()
        if action == "forget":
            await self._disconnect(name)
            await owner.close()
            self._oauth.pop(name, None)
            self._auth_leases.pop(name, None)
            return {"state": "forgotten",
                    "notice": "Local credentials erased; no remote revocation or tool approval implied"}
        status = owner.status()
        if owner._grant is not None:
            status["review"] = owner._grant.to_dict()
        elif owner.flow is not None and owner.flow.status in {"authorized", "expired", "failed", "canceled"}:
            status["review"] = owner.flow.review.to_dict()  # Completed, consumed receipt; never a waiting flow's URI.
        return status

    async def connect(self, name: str) -> None:
        await self._bridge(self._connect(name))

    async def _connect(self, name: str) -> None:
        if name not in self.configs:
            raise McpError("Unknown configured MCP server")
        if name in self._clients:
            raise McpError("Server already has a connection; disconnect explicitly before reconnecting")
        config = self.configs[name]
        owner = self._oauth.get(name)
        if owner is not None and (owner.resource != config.url or owner.config != config.oauth):
            raise McpError("OAuth resource/profile changed; forget credentials before connecting")
        credential = owner.credential if owner is not None else None
        client: StdioClient | HttpClient
        if config.transport == "http":
            client = HttpClient(config, credential) if credential is not None else HttpClient(config)
        else:
            client = StdioClient(config, self.cwd)
        with self._lock:
            self._clients[name] = client
        try:
            await client.connect()
            if "tools" in client.capabilities:
                await self._discover(name)
        except BaseException as exc:
            if isinstance(exc, HttpMcpError) and (exc.auth_challenge is not None or exc.challenge_invalid):
                self._auth_challenges[name] = config.url, config.oauth, exc.auth_challenge
            await client.close()
            with self._lock:
                self._clients.pop(name, None)
            raise

    async def refresh(self, name: str) -> None:
        await self._bridge(self._discover(name))

    async def _discover(self, name: str) -> None:
        client = self._clients.get(name)
        if client is None or client.state != "connected":
            raise McpError("Connect this MCP server explicitly before discovering tools")
        if "tools" not in client.capabilities:
            with self._lock:
                self._tools = tuple(tool for tool in self._tools if tool.server != name)
                self._rejected[name] = ()
            return
        discovered: list[McpTool] = []
        rejected: list[str] = []
        seen: set[str] = set()
        cursors: set[str] = set()
        cursor: str | None = None
        while True:
            response = await client.request("tools/list", {"cursor": cursor} if cursor is not None else {})
            if not isinstance(response.get("tools"), list):
                raise McpError("Invalid MCP tool list")
            for value in response["tools"]:
                if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"]:
                    raise McpError("Invalid MCP tool identity")
                remote = value["name"]
                if remote in seen or len(seen) >= 512:
                    raise McpError("Duplicate MCP tool name or tool limit exceeded")
                seen.add(remote)
                slug = re.sub(r"[^A-Za-z0-9_]", "_", remote)[:20]
                alias = f"mcp_{name[:24]}_{slug}_{hashlib.sha256(remote.encode()).hexdigest()[:8]}"
                description = value.get("description", "")
                if not isinstance(description, str):
                    raise McpError("Invalid MCP tool description")
                input_validation = await self._schemas.compile(
                    value.get("inputSchema"), input_schema=True, legacy=not client.modern)
                assert input_validation.model_parameters is not None
                input_schema = input_validation.model_parameters
                output_validation = (await self._schemas.compile(value["outputSchema"], legacy=not client.modern)
                                     if "outputSchema" in value else None)
                headers: tuple[tuple[tuple[str, ...], str, str], ...] = ()
                if isinstance(client, HttpClient) and client.modern:
                    try:
                        headers = schema_headers(input_schema)
                    except McpError:
                        rejected.append(remote)
                        continue
                discovered.append(McpTool(name, remote, alias, f"External MCP server {name}. {description}",
                                          input_schema,
                                          output_validation.schema if output_validation is not None else None,
                                          input_validation, output_validation, headers))
            cursor = response.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursors) >= 128:
                raise McpError("Invalid/cyclic MCP pagination cursor")
            cursors.add(cursor)
        aliases = [tool.alias for tool in discovered]
        if len(set(aliases)) != len(aliases):
            raise McpError("MCP alias collision")
        with self._lock:
            self._tools = tuple(tool for tool in self._tools if tool.server != name) + tuple(discovered)
            self._rejected[name] = tuple(rejected)
        client.dirty = False

    async def catalog(self, server: str, kind: str) -> tuple[McpEntry, ...]:
        return await self._bridge(self._catalog(server, kind))

    async def _catalog(self, server: str, kind: str) -> tuple[McpEntry, ...]:
        methods = {"resource": ("resources/list", "resources", "resources"),
                   "template": ("resources/templates/list", "resourceTemplates", "resources"),
                   "prompt": ("prompts/list", "prompts", "prompts")}
        if kind not in methods:
            raise McpError("Unknown MCP catalog kind")
        client = self._clients.get(server)
        method, key, capability = methods[kind]
        if client is None or client.state != "connected" or capability not in client.capabilities:
            raise McpError("Connect a server advertising this capability explicitly first")
        cursor: str | None = None
        seen: set[str] = set()
        ids: set[str] = set()
        entries: list[McpEntry] = []
        while True:
            result = await client.request(method, {"cursor": cursor} if cursor else {})
            if result.get("resultType", "complete") != "complete" or not isinstance(result.get(key), list):
                raise McpError("Unsupported/invalid MCP catalog result")
            for value in result[key]:
                entry = context_entry(server, kind, value)
                if entry.name in ids or len(entries) >= 512:
                    raise McpError("Duplicate or excessive MCP catalog entries")
                ids.add(entry.name)
                entries.append(entry)
            cursor = result.get("nextCursor")
            if cursor is None:
                return tuple(entries)
            if not isinstance(cursor, str) or not cursor or cursor in seen or len(seen) >= 128:
                raise McpError("Invalid/cyclic MCP catalog pagination")
            seen.add(cursor)

    async def resource(self, server: str, uri: str) -> McpDocument:
        return await self._bridge(self._resource(server, uri))

    async def _resource(self, server: str, uri: str) -> McpDocument:
        client = self._clients.get(server)
        if client is None or client.state != "connected" or "resources" not in client.capabilities:
            raise McpError("Server resources are unavailable")
        try:
            uri = resource_uri(uri)
        except ResourceMetadataError as exc:
            raise McpError(str(exc)) from None
        if isinstance(client, HttpClient):
            result = await client.request("resources/read", {"uri": uri}, mirrors={"Mcp-Name": header_value(uri)})
        else:
            result = await client.request("resources/read", {"uri": uri})
        if result.get("resultType", "complete") != "complete":
            raise McpError("Interactive/deferred resource input is not silently accepted")
        return McpDocument(server, "resource", uri, resource_blocks(result.get("contents")))

    async def prompt(self, server: str, name: str, arguments: dict[str, str]) -> McpDocument:
        return await self._bridge(self._prompt(server, name, arguments))

    async def _prompt(self, server: str, name: str, arguments: dict[str, str]) -> McpDocument:
        if not isinstance(arguments, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                  for k, v in arguments.items()):
            raise McpError("Prompt arguments must be named strings")
        entries = await self._catalog(server, "prompt")
        entry = next((entry for entry in entries if entry.name == name), None)
        if entry is None:
            raise McpError("Prompt is no longer advertised by this server")
        required = {key for key, _description, needed in entry.arguments if needed}
        allowed = {key for key, _description, _needed in entry.arguments}
        if required - arguments.keys() or arguments.keys() - allowed:
            raise McpError("Required/unknown prompt argument; review the advertised argument list")
        client = self._clients[server]
        params = {"name": name, "arguments": arguments}
        if isinstance(client, HttpClient):
            result = await client.request("prompts/get", params, mirrors={"Mcp-Name": header_value(name)})
        else:
            result = await client.request("prompts/get", params)
        if result.get("resultType", "complete") != "complete":
            raise McpError("Interactive/deferred prompt input is not silently accepted")
        return McpDocument(server, "prompt", name, prompt_blocks(result.get("messages")))

    async def complete(self, server: str, kind: str, name: str, argument: str, value: str,
                       context: dict[str, str] | None = None) -> McpCompletion:
        """Explicit read-only suggestions, never prompt retrieval or tool execution."""
        return await self._bridge(self._complete(
            server, kind, name, argument, value, {} if context is None else context))

    async def _complete(self, server: str, kind: str, name: str, argument: str, value: str,
                        context: dict[str, str]) -> McpCompletion:
        if kind not in ("prompt", "template", "resource"):
            raise McpError("Unknown MCP completion reference kind")
        name, argument = bounded(name, 8192, empty=False), bounded(argument, 256, empty=False)
        value = bounded(value, 8192)
        context = completion_arguments(context)
        client = self._clients.get(server)
        if (client is None or client.state != "connected"
                or not isinstance(client.capabilities.get("completions"), dict)):
            raise McpError("Explicitly connect a server advertising completions first")
        count = self._completion_waiters.get(server, 0)
        if count >= 2:
            raise McpError("A completion and one successor are already pending for this server")
        self._completion_waiters[server] = count + 1
        lock = self._completion_locks.setdefault(server, asyncio.Lock())
        try:
            async with asyncio.timeout(self.configs[server].timeout), lock:
                delay = max(0.0, 0.2 - (time.monotonic() - self._completion_at.get(server, 0)))
                if delay:
                    await asyncio.sleep(delay)  # Debounce this explicit intent, never retry a remote POST.
                self._completion_at[server] = time.monotonic()
                if self._clients.get(server) is not client or client.state != "connected":
                    raise McpError("MCP completion connection changed while queued")
                if kind == "resource":
                    # Opaque resource references have no declared variable schema.
                    # The explicit caller supplies the URI/name/context; the server
                    # validates their meaning. Never list/read/follow the URI here.
                    if argument in context:
                        raise McpError("Current completion argument cannot also be context")
                else:
                    entries = await self._catalog(server, kind)
                    entry = next((entry for entry in entries if entry.name == name), None)
                    if entry is None:
                        raise McpError("Completion reference is no longer advertised")
                    allowed = {key for key, _description, _required in entry.arguments}
                    if argument not in allowed or context.keys() - allowed or argument in context:
                        raise McpError("Unknown/current completion argument in context; review advertised variables")
                if self._clients.get(server) is not client or client.state != "connected":
                    raise McpError("MCP completion connection changed")
                reference = ({"type": "ref/prompt", "name": name} if kind == "prompt"
                             else {"type": "ref/resource", "uri": name})
                params: dict[str, Any] = {"ref": reference, "argument": {"name": argument, "value": value}}
                if context:
                    params["context"] = {"arguments": context}
                result = await client.request("completion/complete", params)
                chosen, total, more = values(result)
                if self._clients.get(server) is not client or client.state != "connected":
                    raise McpError("MCP completion connection changed before delivery")
                return McpCompletion(server, kind, name, argument, value, tuple(context.items()), chosen, total, more)
        except TimeoutError:
            raise McpError("MCP completion timed out; the explicit request was not replayed") from None
        finally:
            remaining = self._completion_waiters[server] - 1
            if remaining:
                self._completion_waiters[server] = remaining
            else:
                self._completion_waiters.pop(server, None)

    async def disconnect(self, name: str) -> None:
        await self._bridge(self._disconnect(name))

    async def _disconnect(self, name: str) -> None:
        client = self._clients.get(name)
        if client is None:
            return
        await client.close()
        with self._lock:
            # A concurrent close/reload may already have installed a new worker.
            # Retiring the old connection must not remove its successor/tools.
            if self._clients.get(name) is client:
                self._clients.pop(name, None)
                self._rejected.pop(name, None)
                self._tools = tuple(tool for tool in self._tools if tool.server != name)

    def allowed(self, alias: str) -> bool:
        with self._lock:
            tool = next((tool for tool in self._tools if tool.alias == alias), None)
        return tool is not None and self.configs[tool.server].allow_tools

    def tools(self) -> tuple[AgentTool, ...]:
        with self._lock:
            specs = self._tools
        def proxy(spec: McpTool) -> AgentTool:
            async def execute(tool_call_id: str, params: dict[str, Any], cancel: Any = None,
                              on_update: Any = None, **kwargs: Any) -> AgentToolResult:
                if cancel is not None and hasattr(cancel, "is_set") and cancel.is_set():
                    raise asyncio.CancelledError
                async def invoke() -> AgentToolResult:
                    await self._bridge(self._schemas.validate(spec.input_validation, params, label="tool arguments"))
                    return await self._bridge(self._call(spec, params))
                task = asyncio.create_task(invoke())
                watcher = asyncio.create_task(cancel.wait()) if cancel is not None and hasattr(cancel, "wait") else None
                try:
                    if watcher is not None:
                        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
                        if watcher in done:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            raise asyncio.CancelledError
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                    if watcher is not None:
                        watcher.cancel()
                    await asyncio.gather(task, *( [watcher] if watcher is not None else []), return_exceptions=True)
            async def validate_arguments(params: dict[str, Any]) -> None:
                await self._bridge(self._schemas.validate(spec.input_validation, params, label="tool arguments"))
            return AgentTool(name=spec.alias, description=spec.description, parameters=copy.deepcopy(spec.input_schema),
                             label=f"MCP {spec.server}/{spec.remote_name}", execute=execute,
                             validate_arguments=validate_arguments)
        return tuple(proxy(spec) for spec in specs)

    async def _call(self, tool: McpTool, arguments: dict[str, Any]) -> AgentToolResult:
        client = self._clients.get(tool.server)
        if client is None:
            raise McpError("MCP owner disconnected; not automatically reconnected/replayed")
        params = {"name": tool.remote_name, "arguments": arguments}
        try:
            if isinstance(client, HttpClient):
                mirrors = {"Mcp-Name": header_value(tool.remote_name), **argument_headers(tool.headers, arguments)}
                result = await client.request("tools/call", params, mirrors=mirrors)
            else:
                result = await client.request("tools/call", params)
        except HttpMcpError as exc:
            if exc.auth_challenge is not None or exc.challenge_invalid:
                config = self.configs[tool.server]
                self._auth_challenges[tool.server] = config.url, config.oauth, exc.auth_challenge
            raise
        if "isError" in result and type(result["isError"]) is not bool:
            raise McpError("Invalid MCP execution error flag")
        if result.get("resultType", "complete") != "complete":
            raise McpError("MCP server requires unsupported interactive input; no data was silently accepted")
        content = result.get("content", [])
        if not isinstance(content, list) or len(content) > 128:
            raise McpError("Invalid/oversized MCP tool content")
        json_value(content, maximum=8 * 1024 * 1024)
        parts: list[TextContent | ImageContent] = []
        block_metadata = []
        for index, raw in enumerate(content):
            block = validate_content(raw)
            payload_keys = {'type', 'text'} if block['type'] == 'text' else {'type', 'data', 'mimeType'}
            if block['type'] in ('text', 'image'):
                extra = {key: value for key, value in block.items() if key not in payload_keys}
                if extra:
                    block_metadata.append({'index': index, 'type': block['type'], 'metadata': extra})
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                parts.append(TextContent(text=block["text"]))
            elif kind == "image" and isinstance(block.get("data"), str) and isinstance(block.get("mimeType"), str):
                parts.append(ImageContent(data=block["data"], mime_type=block["mimeType"]))
            elif kind in {"resource", "resource_link", "audio"}:
                # Content is inert data; embedded/link resources are never fetched automatically.
                parts.append(TextContent(text=json.dumps(block, ensure_ascii=False)))
            else:
                raise McpError("Unsupported MCP content block; content was not silently discarded")
        present = "structuredContent" in result
        structured = result.get("structuredContent")
        encoded = json_value(structured) if present else ""
        if present and not client.modern and not isinstance(structured, dict):
            raise McpError("Legacy MCP structured content must be an object")
        is_error = result.get("isError", False)
        # Execution errors may carry diagnostic data rather than the advertised
        # successful output schema. All content/finite JSON is still validated.
        if tool.output_validation is not None and not is_error:
            if not present:
                raise McpError("MCP result omitted advertised structured output")
            await self._schemas.validate(tool.output_validation, structured, label="structured output")
        if present:
            if not parts:
                parts.append(TextContent(text=encoded))
            elif not any(isinstance(part, TextContent) and part.text.strip() == encoded for part in parts):
                # Canonical structured data must remain visible to models, UIs and
                # persisted complete-source viewers even when a text summary exists.
                parts.append(TextContent(text="[MCP structured output]\n" + encoded))
        encoded_metadata = json_value(block_metadata)
        if block_metadata:
            parts.append(TextContent(text="[MCP content metadata]\n" + encoded_metadata))
        details = self._result_details(tool, result)
        if block_metadata:
            details['mcp_content_metadata'] = block_metadata
        return AgentToolResult(content=tuple(parts), details=details, is_error=is_error)

    def _result_details(self, tool: McpTool, result: dict[str, Any]) -> dict[str, Any]:
        """Protocol metadata only. Client subclasses may attach validated application data."""
        return {"mcp_server": tool.server, "structured_content_present": "structuredContent" in result,
                "structured_content": result.get("structuredContent")}

    async def aclose(self) -> None:
        self.close()
        thread = self._thread
        if thread is None:
            return

        async def settle() -> None:
            # A join deadline/cancelled waiter is not actual resource settlement.
            await asyncio.to_thread(thread.join)
            if self._close_future is not None:
                self._close_future.result()

        await wait_owned(asyncio.create_task(settle()))

    async def _close_clients(self) -> None:
        await self._schemas.close()
        self._auth_leases.clear()
        self._credential_consents.clear()
        for task in tuple(self._auth_preparing.values()):
            task.cancel()
        if self._auth_preparing:
            await asyncio.gather(*tuple(self._auth_preparing.values()), return_exceptions=True)
        await asyncio.gather(*(owner.close() for owner in tuple(self._oauth.values())))
        self._oauth.clear()
        await asyncio.gather(*(client.close() for client in tuple(self._clients.values())))
        with self._lock:
            self._clients.clear()
            self._tools = ()

    def close(self) -> None:
        with self._start_lock:
            if self._closed:
                return
            self._closed = True
            if self._loop is None:
                return
            loop = self._loop
            self._close_future = asyncio.run_coroutine_threadsafe(self._close_clients(), loop)

            def settled(future: concurrent.futures.Future[None]) -> None:
                try:
                    future.exception()  # Retrieved here; async close also reports the actual result.
                finally:
                    loop.call_soon_threadsafe(loop.stop)

            self._close_future.add_done_callback(settled)
