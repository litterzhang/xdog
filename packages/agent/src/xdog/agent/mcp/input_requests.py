"""Bounded 2026-07-28 input-required continuations, never unsolicited RPC.

Only an owned tools/call, prompts/get or resources/read may disclose explicitly
configured Roots metadata. No callback/model/tool capability can be registered.
Each continuation preserves original arguments and echoes opaque state; uncertain
transport outcomes never retry.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.mcp.client_capabilities import RootCapability

INPUT_METHODS = frozenset({'tools/call', 'prompts/get', 'resources/read'})
LIMIT = 256 * 1024


class InputRequests:
    def __init__(self, roots: RootCapability) -> None:
        self.roots = roots
        self.active = False
        self._failed = False
        self.max_rounds = 10
        self.timeout = 600.0
        self._jobs: set[asyncio.Task[Any]] = set()

    def advertised(self) -> dict[str, Any]:
        if not self.active:
            return {}
        capabilities: dict[str, Any] = {}
        if self.roots.active and self.roots.ceiling:
            # No roots/list_changed notification in this revision. A subsequent
            # client request sees the current reviewed selection, including [].
            capabilities['roots'] = {}
        return capabilities  # Task/experimental/extension vocabulary is not borrowed.

    async def run(self, method: str, params: dict[str, Any],
                  send: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        from xdog.agent.mcp.schema import json_value
        from xdog.agent.mcp.stdio import McpError
        from xdog.agent.supervision_transfer import settled_image_work

        if not self.active or method not in INPUT_METHODS:
            raise McpError('Input continuation requires an owned modern request')
        if {'inputResponses', 'requestState'} & params.keys():
            raise McpError('Input continuation data requires original request ownership')
        task = asyncio.current_task()
        assert task is not None
        if task in self._jobs or len(self._jobs) >= 4:
            raise McpError('Modern input request capacity exceeded')
        # Disclosure authority is captured before the first wire leg. Later
        # configuration changes cannot add undeclared Roots to this operation.
        declared = self.advertised()
        self._jobs.add(task)
        root_revision: int | None = None
        def current() -> None:
            if task.cancelling():
                raise asyncio.CancelledError
            if (not self.active or root_revision is not None and
                    (not self.roots.active or self.roots.revision != root_revision)):
                raise McpError('Input continuation owner withdrawn; no replay')
        try:
            original = await settled_image_work(lambda: json.loads(json_value(params, maximum=8 * 1024 * 1024)))
            payload = original
            async with asyncio.timeout(self.timeout):
                for round_index in range(self.max_rounds + 1):
                    current()
                    response = await send(payload)
                    current()
                    kind = response.get('resultType', 'complete')
                    if kind == 'complete':
                        return response
                    if kind != 'input_required':
                        raise McpError('Unsupported MCP result type; not treated as completion')
                    if round_index == self.max_rounds:
                        raise McpError('Modern input continuation round ceiling reached; no replay')
                    frozen = await settled_image_work(lambda: self._validate(response, declared))
                    requests = frozen.get('inputRequests', {})
                    responses: dict[str, Any] = {}
                    # Validate every sibling before disclosing even configured
                    # Roots; unsupported input never receives partial responses.
                    for key, embedded in requests.items():
                        current()
                        root_revision = self.roots.revision
                        envelope = {'jsonrpc': '2.0', 'id': 'input-' + secrets.token_hex(16),
                                    'method': 'roots/list', 'params': embedded.get('params', {})}
                        output = self.roots.response(envelope)
                        if 'result' not in output:
                            raise McpError('Configured root disclosure unavailable')
                        responses[key] = output['result']
                    current()
                    # Exactly this round's responses/state, never earlier private
                    # answers or an interpretation of the opaque server state.
                    payload = {**original}
                    if requests:
                        payload['inputResponses'] = responses
                    else:
                        await asyncio.sleep(.25)  # Bounded requestState-only load shedding.
                    if 'requestState' in frozen:
                        payload['requestState'] = frozen['requestState']
                    await settled_image_work(lambda: json_value(payload, maximum=8 * 1024 * 1024))
                    current()
        except asyncio.CancelledError:
            if self._failed:
                raise McpError('MCP input connection lost; outcome uncertain, not replayed') from None
            raise
        finally:
            self._jobs.discard(task)
        raise AssertionError('Unreachable input continuation')

    def _validate(self, value: Any, declared: dict[str, Any]) -> dict[str, Any]:
        from xdog.agent.mcp.schema import json_value
        from xdog.agent.mcp.stdio import McpError

        row: Any = json.loads(json_value(value, maximum=LIMIT))
        if not isinstance(row, dict):
            raise McpError('Invalid modern input-required object')
        requests = row.get('inputRequests', {})
        if (set(row) - {'resultType', 'inputRequests', 'requestState', '_meta'}
                or '_meta' in row and not isinstance(row['_meta'], dict)
                or not isinstance(requests, dict) or len(requests) > 4
                or not requests and 'requestState' not in row):
            raise McpError('Invalid/bounded modern input-required result')
        if 'requestState' in row and (not isinstance(row['requestState'], str)
                or len(row['requestState'].encode()) > 65536):
            raise McpError('Invalid/bounded opaque request state')
        for key, embedded in requests.items():
            if (not key or len(key.encode()) > 128 or not isinstance(embedded, dict)
                    or set(embedded) - {'method', 'params'}):
                raise McpError('Invalid embedded input identity/shape')
            family, params = embedded.get('method'), embedded.get('params', {})
            if not isinstance(params, dict) or 'task' in params or (isinstance(params.get('_meta'), dict)
                    and 'io.modelcontextprotocol/related-task' in params['_meta']):
                raise McpError('Modern embedded task augmentation is unavailable')
            if family == 'roots/list' and 'roots' in declared:
                if set(params) - {'_meta'} or '_meta' in params and not isinstance(params['_meta'], dict):
                    raise McpError('Invalid embedded roots parameters')
            else:
                raise McpError('Embedded input capability not declared on original request')
        return dict(row)

    async def close(self, *, failed: bool = False) -> None:
        self.active = False
        self._failed = self._failed or failed
        await self._settle(tuple(self._jobs))

    async def _settle(self, tasks: tuple[asyncio.Task[Any], ...]) -> None:
        current = asyncio.current_task()
        tasks = tuple(task for task in tasks if task is not current)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        settlement = asyncio.gather(*tasks, return_exceptions=True)
        canceled = False
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                canceled = True
        settlement.result()
        if canceled:
            raise asyncio.CancelledError
