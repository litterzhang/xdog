"""Owned, optional task-directory observation; ACP is the only reminder path.

Import is inert. Definitions contain data only and never select code to load.
Stopping this registration does not cancel or withdraw accepted destination work.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xdog.agent.acp.client import enqueue, inbox_status, validate_target
from xdog.agent.session_input import SessionInput, input_data


def _write_config(path: Path, definition: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(definition, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class _EnqueueHandler:
    """Public Eyes handler port bound to the original host-provided ACP scope."""
    def __init__(self, target: dict[str, str], instruction: str,
                 on_change: Callable[[Any], Awaitable[None]] | None = None) -> None:
        self.target, self.instruction, self.on_change = target, instruction, on_change
        self.closed = False

    async def start(self) -> None:
        await inbox_status(self.target)

    def _message(self, delivery: Any) -> SessionInput:
        return SessionInput(
            id=delivery.id, origin='automatic', source='Eyes', instruction=self.instruction,
            evidence=tuple(asdict(event) for event in delivery.events),
            provenance={'registration_id': delivery.registration_id, 'route_id': delivery.route_id,
                        'delivery_id': delivery.id, 'event_ids': [event.id for event in delivery.events]})

    def validate(self, delivery: Any) -> None:
        input_data(self._message(delivery))

    async def deliver(self, delivery: Any) -> Any:
        from xdog.eyes.events import DeliveryResult
        if self.closed:
            return DeliveryResult('cancelled', 'Owned task observation is closed')
        if self.on_change is not None:
            await self.on_change(delivery)
            return DeliveryResult('queued', receipt_status='coalesced')
        result = await enqueue(self.target, self._message(delivery))
        if result['status'] in {'queued', 'duplicate'}:
            return DeliveryResult('queued', receipt_status=result['status'], message_state=result['message_state'])
        return DeliveryResult('outcome-unknown' if result['status'] == 'outcome-unknown' else 'paused',
                              result.get('error', 'ACP unavailable'), retry_on_resume=True)

    def snapshot(self) -> Any:
        from xdog.eyes.handlers.contracts import HandlerSnapshot
        return HandlerSnapshot('closed' if self.closed else 'ready', {'session': self.target['session_id'],
                                                                    'delivery': 'enqueue'})

    async def check(self) -> None:
        if self.closed:
            raise ConnectionError('Owned task observation is closed')
        await inbox_status(self.target)

    async def resume(self) -> bool:
        if self.closed:
            return False
        try:
            await self.check()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        self.closed = True  # No destination cancel/withdraw, even for pending ACKs.


class TaskObserver:
    """One owned registration, no daemon and no shared-service shutdown authority.

    Construct a fresh instance after aclose. start is for an explicitly active
    Goal only; caller owns Goal policy and must close on pause/clear/retirement.
    """
    def __init__(self) -> None:
        self._service: Any = None
        self._start_task: asyncio.Task[dict[str, Any]] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False
        self._registration_id: str | None = None

    async def start(self, target: dict[str, Any], task_dir: Path, config_path: Path, goal_id: str,
                    on_change: Callable[[Any], Awaitable[None]] | None = None) -> dict[str, Any]:
        if self._closed:
            return {'status': 'unavailable', 'reason': 'Task observer is closed'}
        if self._start_task is not None:
            raise RuntimeError('Task observer already started')
        # Detach caller-owned mutable routing before the first suspension.
        try:
            binding = validate_target(target)
        except (ValueError, TypeError, AttributeError) as exc:
            return {'status': 'unavailable', 'reason': str(exc)}
        self._start_task = asyncio.create_task(self._start(binding, task_dir, config_path, goal_id, on_change))
        try:
            return await asyncio.shield(self._start_task)
        except asyncio.CancelledError:
            await self.aclose()
            raise

    async def _start(self, target: dict[str, str], task_dir: Path, config_path: Path,
                     goal_id: str, on_change: Callable[[Any], Awaitable[None]] | None) -> dict[str, Any]:
        try:
            if not isinstance(goal_id, str) or not goal_id.strip() or '\x00' in goal_id:
                raise ValueError('Goal ID must be nonempty text')
            workspace, directory = Path(target['cwd']), task_dir.absolute()
            config = config_path.absolute()
            if (workspace.resolve() != workspace or directory.resolve() != directory
                    or config.parent.resolve() != config.parent or not directory.is_dir()
                    or not directory.is_relative_to(workspace) or not config.is_relative_to(workspace)):
                raise ValueError('Task/config paths require the original canonical workspace')
            instruction = ('Goal ' + goal_id + ': task JSON files changed. Read the authoritative task files in '
                           + str(directory) + ' and decide the next action using the Goal skill. '
                           'This is an automatic reminder, not user authorization. Existing tool permissions apply.')
            excluded = ['*/**']
            if config.is_relative_to(directory):
                excluded.append(config.relative_to(directory).as_posix())
            definition = {
                'version': 2, 'name': 'goal-tasks-' + goal_id,
                'sources': [{'id': 'tasks', 'type': 'file', 'root': str(directory),
                             'include': ['*.json'], 'exclude': excluded}],
                'detectors': [{'id': 'changed', 'type': 'file.change', 'source': 'tasks',
                               'debounce_ms': 500, 'max_batch_delay_ms': 2000}],
                'handlers': [{'id': 'owner', 'type': 'acp',
                              'connection': {'transport': 'unix', 'path': target['endpoint']},
                              'session': target['session_id'], 'delivery': 'enqueue', 'instruction': instruction}],
                'routes': [{'id': 'task-changes', 'detector': 'changed', 'handler': 'owner',
                            'event_types': ['file.created', 'file.modified', 'file.deleted']}]}
            # The generated file is a definition, not a runnable module or plugin
            # descriptor. Even without Eyes, callers can inspect the intended IO.
            write = asyncio.create_task(asyncio.to_thread(_write_config, config, definition))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                while not write.done():
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        pass
                write.result()
                raise
            from xdog.eyes.service import ObserverService
            # A private service plus injected public handler port fences every
            # delivery to the original inbox/branch, including after reconnect.
            # Eyes' stock handler only pins the scope discovered on connection.
            self._service = ObserverService(
                cwd=workspace, handler_factory=lambda _: _EnqueueHandler(target, instruction, on_change))
            result = await self._service.start(definition)
            self._registration_id = str(result['registration_id'])
            # Service.start installs the task before its first file scan settles.
            # Do not report readiness or let a fast worker finish into the initial
            # baseline (which intentionally emits no change event).
            async with asyncio.timeout(10):
                while True:
                    snapshot = await self._service.status(self._registration_id)
                    rows = snapshot.get('registrations', [])
                    if len(rows) != 1:
                        raise ValueError('Owned task registration disappeared during baseline')
                    state = rows[0]['state']
                    if state == 'watching':
                        break
                    if state != 'baselining':
                        raise ValueError('Task observation did not become ready: ' + str(state))
                    await asyncio.sleep(.01)
            return {'status': 'watching', 'registration_id': self._registration_id, 'config_path': str(config)}
        except asyncio.CancelledError:
            if self._service is not None:
                await self._service.aclose()
            raise
        except Exception as exc:
            if self._service is not None:
                await self._service.aclose()
            return {'status': 'unavailable', 'reason': str(exc), 'config_path': str(config_path)}

    async def _close(self) -> None:
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        if self._service is not None:
            await self._service.aclose()

    async def aclose(self) -> None:
        """Join owned startup, file IO, polling and delivery; never cancel inputs."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
