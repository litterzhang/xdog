"""Bounded 2026-07-28 input-required continuations, never unsolicited RPC.

Only an owned tools/call, prompts/get or resources/read may collect input from
already registered review handlers. Each explicit protocol continuation preserves
original arguments and echoes opaque state; uncertain transport outcomes never retry.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.mcp.client_capabilities import RootCapability
from xdog.agent.mcp.reverse_requests import ReverseHandler, ReverseRequests

INPUT_METHODS = frozenset({'tools/call', 'prompts/get', 'resources/read'})
LIMIT = 256 * 1024


class InputRequests:
    def __init__(self, roots: RootCapability, reverse: ReverseRequests) -> None:
        self.roots, self.reverse = roots, reverse
        self.active = False
        self._failed = False
        self.sampling_handler: ReverseHandler | None = None
        self.elicitation_handler: ReverseHandler | None = None
        self.sampling_alive: Callable[[], bool] = lambda: True
        self.elicitation_alive: Callable[[], bool] = lambda: True
        self.max_rounds = 10
        self.timeout = 600.0
        self._jobs: dict[asyncio.Task[Any], set[str]] = {}

    def advertised(self) -> dict[str, Any]:
        if not self.active:
            return {}
        capabilities: dict[str, Any] = {}
        if self.roots.active and self.roots.ceiling:
            # No roots/list_changed notification in this revision. A subsequent
            # client request sees the current reviewed selection, including [].
            capabilities['roots'] = {}
        if self.sampling_handler is not None and self.sampling_alive():
            capabilities['sampling'] = {
                **({'tools': {}} if self.reverse.sampling_tools else {}),
                **({'context': {}} if self.reverse.sampling_context else {})}
        if self.elicitation_handler is not None and self.elicitation_alive():
            capabilities['elicitation'] = {'form': {}, 'url': {}}
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
        # Capture authority before the first wire leg. New/replaced registration
        # cannot fulfil a request whose per-request declaration predates it.
        sampling, elicitation = self.sampling_handler, self.elicitation_handler
        declared = self.advertised()
        families: set[str] = set()
        self._jobs[task] = families
        root_revision: int | None = None
        def current() -> None:
            if task.cancelling():
                raise asyncio.CancelledError
            if (not self.active or ('sampling/createMessage' in families
                    and (self.sampling_handler != sampling or not self.sampling_alive()))
                    or ('elicitation/create' in families
                    and (self.elicitation_handler != elicitation or not self.elicitation_alive()))
                    or ('roots/list' in families and (not self.roots.active
                        or root_revision is not None and self.roots.revision != root_revision))):
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
                    # Serial handling avoids forcing multiple exclusive native
                    # reviews onto the same view. All siblings validate first.
                    for key, embedded in requests.items():
                        family = embedded['method']
                        families.add(family)
                        current()
                        envelope = {'jsonrpc': '2.0', 'id': 'input-' + secrets.token_hex(16),
                                    'method': family, 'params': embedded.get('params', {})}
                        if family == 'roots/list':
                            root_revision = self.roots.revision
                            output = self.roots.response(envelope)
                            if 'result' not in output:
                                raise McpError('Configured root disclosure unavailable')
                            responses[key] = output['result']
                        else:
                            handler = sampling if family == 'sampling/createMessage' else elicitation
                            if handler is None:
                                raise McpError('Input review handler unavailable')
                            output = await self._handle(handler, json.loads(json.dumps(envelope)))
                            current()
                            if family == 'sampling/createMessage':
                                from xdog.agent.mcp.sampling import result as sampling_result
                                responses[key] = await settled_image_work(
                                    lambda: sampling_result(output, envelope, modern=True))
                            else:
                                from xdog.agent.mcp.elicitation import result as elicitation_result
                                responses[key] = await settled_image_work(
                                    lambda: elicitation_result(output, envelope, modern=True))
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
            self._jobs.pop(task, None)
        raise AssertionError('Unreachable input continuation')

    def _validate(self, value: Any, declared: dict[str, Any]) -> dict[str, Any]:
        from xdog.agent.mcp.elicitation import request as elicitation_request
        from xdog.agent.mcp.sampling import request as sampling_request
        from xdog.agent.mcp.schema import json_value
        from xdog.agent.mcp.stdio import McpError

        row: dict[str, Any] = json.loads(json_value(value, maximum=LIMIT))
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
            envelope = {'jsonrpc': '2.0', 'id': 'input-validation', 'method': family, 'params': params}
            if family == 'roots/list' and 'roots' in declared:
                if set(params) - {'_meta'} or '_meta' in params and not isinstance(params['_meta'], dict):
                    raise McpError('Invalid embedded roots parameters')
            elif family == 'sampling/createMessage' and 'sampling' in declared:
                if (('tools' in params or 'toolChoice' in params) and 'tools' not in declared['sampling']
                        or params.get('includeContext', 'none') != 'none' and 'context' not in declared['sampling']):
                    raise McpError('Embedded sampling exceeds declared subcapabilities')
                sampling_request(envelope, modern=True)
            elif family == 'elicitation/create' and 'elicitation' in declared:
                elicitation_request(envelope, modern=True)
            else:
                raise McpError('Embedded input capability not declared on original request')
        return row

    async def _handle(self, handler: ReverseHandler, value: dict[str, Any]) -> dict[str, Any]:
        # The caller owns the whole flow, but repeated cancellation must not
        # interrupt a handler's actual finally or release the flow slot early.
        async def invoke() -> dict[str, Any]:
            return await handler(value)
        work = asyncio.create_task(invoke())
        try:
            return await asyncio.shield(work)
        finally:
            if not work.done() and not work.cancelling():
                work.cancel()
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not work.cancelled():
                work.exception()

    async def withdraw(self, method: str) -> None:
        if method == 'elicitation/create':
            self.elicitation_handler = None
        elif method == 'sampling/createMessage':
            self.sampling_handler = None
        await self._settle(tuple(task for task, families in self._jobs.items() if method in families))

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
