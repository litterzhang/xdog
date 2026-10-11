"""Original-loop elicitation request and exact reply review; never browser/model work."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from xdog.agent.mcp.client_capabilities import task_augmented
from xdog.agent.mcp.elicitation import LIMIT, request, result
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError
from xdog.agent.mcp.task_registry import TaskRegistry
from xdog.agent.supervision_transfer import settled_image_work


class ElicitationReplyInvalid(McpError):
    """Recoverable validation failure; a new exact request review was published."""


@dataclass(slots=True)
class _Review:
    id: str
    original: dict[str, Any] = field(repr=False)
    modern: bool = False
    task_id: str = ''
    registry: TaskRegistry | None = field(default=None, repr=False)
    phase: str = 'request'
    receipt: str = ''
    digest: str = ''
    decision: asyncio.Future[dict[str, Any]] | None = field(default=None, repr=False)
    task: asyncio.Task[Any] | None = field(default=None, repr=False)
    submitting: bool = False


class ElicitationOwner:
    """Human request/response review owner; native hosts supply painted consent."""
    def __init__(self, lease: str, publish: Callable[[dict[str, Any]], None], alive: Callable[[], bool],
                 *, timeout: float = 120) -> None:
        if not isinstance(lease, str) or not 0 < len(lease) <= 128 or not 0.1 <= timeout <= 600:
            raise ValueError('Invalid elicitation host lease/deadline')
        self.lease, self.publish, self.alive, self.timeout = lease, publish, alive, timeout
        self.task_enabled = False  # Explicit manager registration opt-in; never inferred from peer.
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: dict[str, _Review] = {}
        self._admitting = 0
        self._validation_tasks: set[asyncio.Task[Any]] = set()
        self._urls: dict[str, float] = {}
        self._url_ids: set[str] = set()

    def _check(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise McpError('Elicitation controls require the original loop')

    def _live(self) -> bool:
        return not self._closed and self.alive()

    async def _review(self, job: _Review, body: dict[str, Any]) -> dict[str, Any]:
        if not self._live():
            raise McpError('Elicitation host revoked; no replay')
        encoded = json_value(body, maximum=LIMIT)
        job.digest, job.receipt = hashlib.sha256(encoded.encode()).hexdigest(), secrets.token_hex(16)
        job.decision = asyncio.get_running_loop().create_future()
        if job.registry is not None:
            job.registry.input_required(job.task_id, True)
        self.publish({'type': 'elicitation_review', 'id': job.id, 'phase': job.phase,
            'receipt': job.receipt, 'sha256': job.digest, 'body': json.loads(encoded),
            **({'protocol': '2026-07-28'} if job.modern else {}),
            'notice': ('Inspect requested fields/URL; no browser, login, model or tool grant.'
                       if job.phase == 'request' else
                       'Inspect exact response before sharing it with this MCP peer.')})
        while not job.decision.done():
            if not self._live():
                raise McpError('Elicitation view revoked; response withheld')
            await asyncio.wait({job.decision}, timeout=.025)
        if job.registry is not None:
            job.registry.input_required(job.task_id, False)
        return job.decision.result()

    async def handle(self, value: dict[str, Any]) -> dict[str, Any]:
        return await self._handle(value)

    async def handle_input(self, value: dict[str, Any]) -> dict[str, Any]:
        return await self._handle(value, modern=True)

    async def handle_task(self, value: dict[str, Any], identifier: str,
                          registry: TaskRegistry) -> dict[str, Any]:
        registry.authorize(identifier, 'elicitation/create')
        if not isinstance(value.get('params'), dict) or 'task' not in value['params']:
            raise McpError('Explicit task creation envelope required')
        return await self._handle(value, task_id=identifier, registry=registry)

    async def _handle(self, value: dict[str, Any], *, task_id: str = '', modern: bool = False,
                      registry: TaskRegistry | None = None) -> dict[str, Any]:
        self._check()
        if task_augmented(value) and registry is None:
            raise McpError('Client task capability not supported', code=-32602)
        if not self._live() or len(self._jobs) + self._admitting >= 4:
            raise McpError('Elicitation unavailable/busy')
        self._admitting += 1
        validation_task = asyncio.current_task()
        assert validation_task is not None
        self._validation_tasks.add(validation_task)
        try:
            original = await settled_image_work(lambda: request(value, modern=True) if modern else request(value))
        finally:
            self._admitting -= 1
            self._validation_tasks.discard(validation_task)
        if not self._live():
            raise McpError('Elicitation host revoked during validation')
        job = _Review(secrets.token_hex(16), original, task=asyncio.current_task(), modern=modern,
                      task_id=task_id, registry=registry)
        self._jobs[job.id] = job
        try:
            async with asyncio.timeout(self.timeout):
                proposed = await self._review(job, original)
                if proposed['action'] in ('decline', 'cancel'):
                    return proposed  # No private data and no external action to authorize.
                job.phase = 'response'
                decision = await self._review(job, {'request': original, 'response': proposed})
                if not self._live():
                    raise McpError('Elicitation host revoked before release')
                if decision['action'] == 'accept' and original['params'].get('mode') == 'url' and not modern:
                    identifier = original['params']['elicitationId']
                    now = time.monotonic()
                    self._urls = {key: expiry for key, expiry in self._urls.items() if expiry > now}
                    if identifier in self._url_ids or len(self._url_ids) >= 256 or len(self._urls) >= 16:
                        raise McpError('URL elicitation identity/capacity exhausted; no replay')
                    self._url_ids.add(identifier)
                    self._urls[identifier] = time.monotonic() + self.timeout
                    self.publish({'type': 'elicitation_url_pending', 'id': job.id,
                                  'elicitation_id': identifier,
                                  'notice': 'URL request acknowledged; external completion is not yet confirmed.'})
                return proposed if decision['action'] == 'accept' else decision
        finally:
            job.receipt = ''
            if job.decision is not None and not job.decision.done():
                job.decision.cancel()
            self._jobs.pop(job.id, None)
            try:
                self.publish({'type': 'elicitation_closed', 'id': job.id})
            except Exception:
                pass

    async def answer(self, identifier: str, *, lease: str, phase: str, receipt: str, sha256: str,
                     response: Any) -> None:
        self._check()
        job = self._jobs.get(identifier)
        if (not self._live() or lease != self.lease or job is None or phase != job.phase
                or phase not in ('request', 'response') or not receipt or receipt != job.receipt
                or sha256 != job.digest or job.decision is None or job.decision.done() or job.submitting):
            raise McpError('Elicitation owner/phase/receipt changed; no replay')
        job.submitting = True
        validation_task = asyncio.current_task()
        assert validation_task is not None
        self._validation_tasks.add(validation_task)
        try:
            # Validate reply privately before consuming its phase receipt. Invalid
            # form edits can be corrected, but concurrent submission cannot race.
            snapshot = json.loads(json_value(response, maximum=LIMIT))
            if phase == 'request':
                try:
                    checked = await settled_image_work(
                        lambda: result(snapshot, job.original, modern=True)
                        if job.modern else result(snapshot, job.original))
                except McpError:
                    if (not self._live() or self._jobs.get(identifier) is not job or job.receipt != receipt
                            or job.decision.done()):
                        raise McpError('Elicitation owner revoked during invalid reply validation')
                    # Keep the original waiting future but rotate its public
                    # authority. A stale submission cannot race the corrected edit.
                    job.receipt = secrets.token_hex(16)
                    draft = snapshot.get('content') if isinstance(snapshot, dict) else None
                    self.publish({'type': 'elicitation_review', 'id': job.id, 'phase': job.phase,
                        'receipt': job.receipt, 'sha256': job.digest,
                        'body': json.loads(json_value(job.original, maximum=LIMIT)),
                        **({'protocol': '2026-07-28'} if job.modern else {}),
                        **({'form_draft': draft} if isinstance(draft, dict) else {}),
                        'notice': 'Reply did not satisfy requested schema. Review request again and correct '
                                  'retained draft; no data shared, old receipt retired.'})
                    raise ElicitationReplyInvalid(
                        'Elicitation reply invalid; draft retained under a new review') from None
            else:
                if not isinstance(snapshot, dict) or set(snapshot) != {'action'}:
                    raise McpError('Response review can only accept/decline/cancel exact proposed bytes')
                checked = await settled_image_work(lambda: result(snapshot, {
                    'params': {'mode': 'url'}}))
            if (not self._live() or self._jobs.get(identifier) is not job or job.receipt != receipt
                    or job.phase != phase or job.decision.done()):
                raise McpError('Elicitation owner revoked during reply validation')
            job.receipt = ''
            job.decision.set_result(checked)
        finally:
            job.submitting = False
            self._validation_tasks.discard(validation_task)

    def completion(self, message: dict[str, Any]) -> None:
        """Legacy peer notice only for this owner-approved URL, never credential admission."""
        self._check()
        if (not self._live() or message.get('jsonrpc') != '2.0'
                or message.get('method') != 'notifications/elicitation/complete'
                or 'id' in message or set(message) - {'jsonrpc', 'method', 'params'}):
            return
        params = message.get('params')
        if (not isinstance(params, dict) or set(params) - {'elicitationId', '_meta'}
                or not isinstance(params.get('elicitationId'), str)
                or '_meta' in params and not isinstance(params['_meta'], dict)):
            return
        identifier = params['elicitationId']
        deadline = self._urls.pop(identifier, None)
        if deadline is None or time.monotonic() > deadline:
            return
        self.publish({'type': 'elicitation_url_complete', 'elicitation_id': identifier,
                      'notice': 'Peer reports external interaction complete; no credential refresh or request replay.'})

    async def close(self) -> None:
        self._check()
        self._closed = True
        self._urls.clear()
        current = asyncio.current_task()
        tasks = tuple(({job.task for job in self._jobs.values() if job.task is not None}
                       | self._validation_tasks) - {current})
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
