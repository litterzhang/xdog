"""Two-phase sampling consent: input execution AND output release need host review.

The host publishes/paints reviews and supplies its independently permission-checked
executor. This owner never chooses a model, borrows history/tools, or starts a provider.
All methods run on its owning asyncio loop; native hosts bridge control calls there.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from xdog.agent.mcp.client_capabilities import task_augmented
from xdog.agent.mcp.sampling import MAX_SAMPLING_BYTES, request, result
from xdog.agent.mcp.sampling_context import bind_context
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError
from xdog.agent.mcp.task_registry import TaskRegistry
from xdog.agent.supervision_transfer import settled_image_work

Executor = Callable[[dict[str, Any], dict[str, Any], asyncio.Event], Awaitable[dict[str, Any]]]
Publisher = Callable[[dict[str, Any]], None]
ContextProvider = Callable[[str], Awaitable[list[dict[str, Any]]]]


@dataclass(slots=True)
class _Review:
    id: str
    request: dict[str, Any] = field(repr=False)
    modern: bool = False
    task_id: str = ''
    registry: TaskRegistry | None = field(default=None, repr=False)
    cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    phase: str = 'input'
    receipt: str = ''
    digest: str = ''
    decision: asyncio.Future[dict[str, Any]] | None = field(default=None, repr=False)
    output: dict[str, Any] | None = field(default=None, repr=False)
    task: asyncio.Task[Any] | None = field(default=None, repr=False)


class SamplingOwner:
    """One explicit host lease, four jobs, single-use phase/digest-bound receipts."""
    def __init__(self, lease: str, publish: Publisher, execute: Executor, alive: Callable[[], bool],
                 *, timeout: float = 120, context: ContextProvider | None = None) -> None:
        if not isinstance(lease, str) or not 0 < len(lease) <= 128 or not 0.1 <= timeout <= 600:
            raise ValueError('Invalid sampling host lease/deadline')
        self.lease, self.publish, self.execute, self.alive, self.timeout = lease, publish, execute, alive, timeout
        self.context = context
        self.task_enabled = False  # Explicit manager registration opt-in; never inferred from peer.
        self._closed = False
        self._jobs: dict[str, _Review] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise McpError('Sampling controls require the original owner loop')

    def _live(self) -> bool:
        return not self._closed and self.alive()

    async def _review(self, job: _Review, body: dict[str, Any]) -> dict[str, Any]:
        if not self._live() or job.cancel.is_set():
            raise McpError('Sampling host owner revoked')
        encoded = json_value(body, maximum=MAX_SAMPLING_BYTES)
        job.digest = hashlib.sha256(encoded.encode()).hexdigest()
        job.receipt = secrets.token_hex(16)
        job.decision = asyncio.get_running_loop().create_future()
        if job.registry is not None:
            job.registry.input_required(job.task_id, True)
        self.publish({'type': 'sampling_review', 'id': job.id, 'phase': job.phase,
                      **({'protocol': '2026-07-28'} if job.modern else {}),
                      'receipt': job.receipt, 'sha256': job.digest, 'body': json.loads(encoded),
                      'notice': ('Approve exact request and explicit model/context/tools selection before execution.'
                                 if job.phase == 'input' else
                                 'Review exact additional context before sharing with the selected model.'
                                 if job.phase == 'context' else
                                 'Review exact generated output before releasing it to this MCP peer.')})
        while not job.decision.done():
            if not self._live() or job.cancel.is_set():
                raise McpError('Sampling host review revoked; no replay')
            await asyncio.wait({job.decision}, timeout=.025)
        if job.registry is not None:
            job.registry.input_required(job.task_id, False)
        return job.decision.result()

    async def _execute(self, job: _Review, selection: dict[str, Any],
                       execution_request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Watch host revocation during provider work, not just after it finishes."""
        frozen_request = json.loads(json.dumps(execution_request if execution_request is not None else job.request))
        if job.registry is not None:
            job.registry.authorize(job.task_id, 'sampling/createMessage')
            # Only after exact task request consent: executor consumes the model
            # operation, not the transport task envelope. Original review stays intact.
            frozen_request['params'].pop('task', None)
        execution = asyncio.ensure_future(self.execute(frozen_request, json.loads(json.dumps(selection)), job.cancel))
        try:
            while not execution.done():
                if not self._live() or job.cancel.is_set():
                    raise McpError('Sampling host revoked during execution; output withheld')
                await asyncio.wait({execution}, timeout=.025)
            if not self._live() or job.cancel.is_set():
                raise McpError('Sampling host revoked after execution; output withheld')
            return execution.result()
        finally:
            if not execution.done():
                job.cancel.set()
                execution.cancel()
            # Repeated caller cancellation must not release the review slot
            # while provider/tool finally still owns its work.
            while not execution.done():
                try:
                    await asyncio.shield(execution)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not execution.cancelled():
                execution.exception()

    async def _resolve_context(self, job: _Review, scope: str) -> list[dict[str, Any]]:
        provider = self.context
        if provider is None:
            raise McpError('Sampling context disclosure provider unavailable; no history fallback')
        resolving = asyncio.ensure_future(provider(scope))
        try:
            while not resolving.done():
                if not self._live() or job.cancel.is_set():
                    raise McpError('Sampling host revoked during context capture')
                await asyncio.wait({resolving}, timeout=.025)
            if not self._live() or job.cancel.is_set():
                raise McpError('Sampling host revoked after context capture')
            additional = resolving.result()
            if not isinstance(additional, list):
                raise McpError('Invalid sampling context snapshot')
            # Copy synchronously on the provider's owner loop before giving any
            # serialization worker or UI a reference into mutable host data.
            return cast(list[dict[str, Any]], json.loads(json_value(additional, maximum=MAX_SAMPLING_BYTES)))
        finally:
            if not resolving.done():
                job.cancel.set()
                resolving.cancel()
            while not resolving.done():
                try:
                    await asyncio.shield(resolving)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not resolving.cancelled():
                resolving.exception()

    async def handle(self, value: dict[str, Any]) -> dict[str, Any]:
        return await self._handle(value)

    async def handle_input(self, value: dict[str, Any]) -> dict[str, Any]:
        return await self._handle(value, modern=True)

    async def handle_task(self, value: dict[str, Any], identifier: str,
                          registry: TaskRegistry) -> dict[str, Any]:
        registry.authorize(identifier, 'sampling/createMessage')
        if not isinstance(value.get('params'), dict) or 'task' not in value['params']:
            raise McpError('Explicit task creation envelope required')
        return await self._handle(value, task_id=identifier, registry=registry)

    async def _handle(self, value: dict[str, Any], *, task_id: str = '', modern: bool = False,
                      registry: TaskRegistry | None = None) -> dict[str, Any]:
        self._check_loop()
        if task_augmented(value) and registry is None:
            raise McpError('Client task capability not supported', code=-32602)
        if not self._live() or len(self._jobs) >= 4:
            raise McpError('Sampling host review unavailable/busy')
        validated = await settled_image_work(lambda: request(value, modern=True) if modern else request(value))
        if not self._live() or len(self._jobs) >= 4:
            raise McpError('Sampling host changed during validation')
        job = _Review(secrets.token_hex(16), validated, task=asyncio.current_task(), modern=modern,
                      task_id=task_id, registry=registry)
        self._jobs[job.id] = job
        try:
            async with asyncio.timeout(self.timeout):
                selection = await self._review(job, validated)
                if not self._live() or job.cancel.is_set():
                    raise McpError('Sampling host changed before execution')
                if (validated['params'].get('toolChoice', {}).get('mode', 'auto') == 'required'
                        and not selection['tools']):
                    raise McpError('Required proposed tools were not approved; no executor call')
                execution_request = validated
                execution_selection = selection
                if selection['context'] != 'none':
                    additional = await self._resolve_context(job, selection['context'])
                    additional = await settled_image_work(lambda: bind_context(additional, validated))
                    # Freeze/validate exactly what will be reviewed AND executed.
                    # No second context resolver read is allowed after approval.
                    execution_request = json.loads(json_value(validated, maximum=MAX_SAMPLING_BYTES))
                    execution_request['params']['messages'] = [*additional, *validated['params']['messages']]
                    execution_request = await settled_image_work(lambda: request(execution_request))
                    context_body = {'selection': selection, 'messages': execution_request['params']['messages'],
                                    'systemPrompt': execution_request['params'].get('systemPrompt')}
                    job.phase = 'context'
                    await self._review(job, context_body)
                    execution_selection = {**selection, 'context': 'none'}
                job.phase = 'executing'
                # Isolate callback data from the canonical request/selection used
                # to validate subsequent output. Host owns any actual tool effects.
                output = await self._execute(job, execution_selection, execution_request)
                if not self._live() or job.cancel.is_set():
                    raise McpError('Sampling host changed after execution; output withheld')
                effective = json.loads(json.dumps(validated))
                if 'tools' in effective['params']:
                    effective['params']['tools'] = [tool for tool in effective['params']['tools']
                                                     if tool['name'] in selection['tools']]
                job.output = await settled_image_work(
                    lambda: result(output, effective, modern=True) if modern else result(output, effective))
                if job.output['model'] != selection['model']:
                    raise McpError('Sampling executor returned a different model; output withheld')
                job.phase = 'output'
                await self._review(job, job.output)
                if not self._live() or job.cancel.is_set():
                    raise McpError('Sampling host changed before release; output withheld')
                return cast(dict[str, Any], json.loads(json_value(job.output, maximum=MAX_SAMPLING_BYTES)))
        finally:
            job.cancel.set()
            job.receipt = ''
            if job.decision is not None and not job.decision.done():
                job.decision.cancel()
            self._jobs.pop(job.id, None)
            # Terminal event contains identities/status only, never private output.
            try:
                self.publish({'type': 'sampling_closed', 'id': job.id})
            except Exception:
                pass

    def answer(self, identifier: str, *, lease: str, phase: str, receipt: str, sha256: str,
               approve: bool, selection: Any = None) -> None:
        self._check_loop()
        job = self._jobs.get(identifier)
        if (not self._live() or lease != self.lease or job is None or job.phase != phase
                or phase not in {'input', 'context', 'output'} or not job.receipt or receipt != job.receipt
                or sha256 != job.digest or job.decision is None or job.decision.done() or type(approve) is not bool):
            raise McpError('Sampling review owner/phase/receipt changed; no replay')
        if not approve:
            job.receipt = ''
            job.cancel.set()
            job.decision.set_exception(McpError('Sampling review denied; no replay'))
            return
        if phase == 'input':
            if (not isinstance(selection, dict) or set(selection) != {'model', 'max_tokens', 'context', 'tools'}
                    or not isinstance(selection['model'], str) or not 0 < len(selection['model'].encode()) <= 256
                    or type(selection['max_tokens']) is not int
                    or not 1 <= selection['max_tokens'] <= job.request['params']['maxTokens']
                    or selection['context'] not in ('none', 'thisServer', 'allServers')
                    or not isinstance(selection['tools'], list)
                    or any(not isinstance(name, str) for name in selection['tools'])
                    or len(set(selection['tools'])) != len(selection['tools'])):
                raise McpError('Explicit sampling model/token/context/tool selection required')
            context = job.request['params'].get('includeContext', 'none')
            ranks = {'none': 0, 'thisServer': 1, 'allServers': 2}
            allowed = {tool['name'] for tool in job.request['params'].get('tools', [])}
            if ranks[selection['context']] > ranks[context] or not set(selection['tools']) <= allowed:
                raise McpError('Sampling selection exceeds reviewed request ceiling')
            decision = json.loads(json_value(selection, maximum=4096))
        else:
            if selection is not None:
                raise McpError('Context/output approval cannot change the executed selection')
            decision = {}
        job.receipt = ''  # Exact single-use phase admission.
        job.decision.set_result(decision)

    async def close(self) -> None:
        self._check_loop()
        self._closed = True
        current = asyncio.current_task()
        jobs = tuple(self._jobs.values())
        for job in jobs:
            job.cancel.set()
            if job.task is not None and job.task is not current and not job.task.cancelling():
                job.task.cancel()
        await asyncio.gather(*(job.task for job in jobs if job.task is not None and job.task is not current),
                             return_exceptions=True)
