"""Owned, optional task-directory observation; ACP is the only reminder path.

Import is inert. Definitions contain data only and never select code to load.
Stopping this registration does not cancel or withdraw accepted destination work.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.agent.acp.client import validate_target

from . import eyes_cli


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


@dataclass(frozen=True)
class TaskChange:
    """Detached CLI event data; no Eyes runtime classes cross into Goal."""
    id: str
    registration_id: str
    route_id: str
    events: tuple[dict[str, Any], ...]


class TaskObserver:
    """An owner-scoped CLI subscription, never an Eyes SDK or embedded engine.

    Events commit to Goal's coalescer before acknowledgement. Host retirement
    detaches; explicit Goal suspension pauses only this registration.
    """
    def __init__(self) -> None:
        self._owner = ''
        self._subscription: asyncio.Task[None] | None = None
        self._state: dict[str, Any] = {}
        self._suspend = True
        self._start_task: asyncio.Task[dict[str, Any]] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False
        self._registration_id: str | None = None
        self._registration_attempted = False

    async def start(self, target: dict[str, Any], task_dir: Path, config_path: Path, goal_id: str,
                    on_change: Callable[[Any], Awaitable[bool]] | None = None) -> dict[str, Any]:
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
                     goal_id: str, on_change: Callable[[Any], Awaitable[bool]] | None) -> dict[str, Any]:
        try:
            if on_change is None:
                raise ValueError('Task observation requires the Goal runtime event consumer')
            if not isinstance(goal_id, str) or not goal_id.strip() or '\x00' in goal_id:
                raise ValueError('Goal ID must be nonempty text')
            workspace, directory = Path(target['cwd']), task_dir.absolute()
            config = config_path.absolute()
            if (workspace.resolve() != workspace or directory.resolve() != directory
                    or config.parent.resolve() != config.parent or not directory.is_dir()
                    or not directory.is_relative_to(workspace) or not config.is_relative_to(workspace)):
                raise ValueError('Task/config paths require the original canonical workspace')
            excluded = ['*/**']
            if config.is_relative_to(directory):
                excluded.append(config.relative_to(directory).as_posix())
            definition = {
                'version': 2, 'name': 'goal-tasks-' + goal_id,
                'sources': [{'id': 'tasks', 'type': 'file', 'root': str(directory),
                             'include': ['*.json'], 'exclude': excluded}],
                'detectors': [{'id': 'changed', 'type': 'file.change', 'source': 'tasks',
                               'debounce_ms': 500, 'max_batch_delay_ms': 2000}],
                'handlers': [{'id': 'owner', 'type': 'events'}],
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
            # Pin the original host namespace/session/branch, not mutable event
            # provenance. The namespace is not itself an authorization token.
            identity = {key: value for key, value in target.items() if key != 'endpoint'}
            # An endpoint is a replaceable transport address, not owner identity.
            # Reopening the same inbox/session/branch reuses its registration even
            # when the host republishes its ACP socket at another private path.
            owner = 'goal:' + hashlib.sha256(json.dumps(
                [identity, goal_id], sort_keys=True, allow_nan=False).encode()).hexdigest()
            self._owner = owner
            await eyes_cli.invoke(owner, 'daemon', 'start')
            self._registration_attempted = True
            registration = asyncio.create_task(eyes_cli.invoke(owner,
                'start', '-', '--delivery', 'events', '--key', 'tasks', '--no-wait', payload=definition))
            try:
                result = await asyncio.shield(registration)
            except asyncio.CancelledError:
                # Intake may already have durably accepted this registration.
                # Join its original result before close decides what to suspend.
                while not registration.done():
                    try:
                        await asyncio.shield(registration)
                    except asyncio.CancelledError:
                        pass
                self._registration_id = str(registration.result()['registration_id'])
                raise
            identifier = str(result['registration_id'])
            self._registration_id = identifier
            if result['state'] == 'paused':
                await eyes_cli.invoke(owner, 'resume', identifier)
            await eyes_cli.invoke(owner, 'status', identifier, '--wait-ready', '--timeout', '10')
            self._state.update(status='watching', registration_id=self._registration_id, config_path=str(config))

            async def consume() -> None:
                stream = eyes_cli.events(owner)
                try:
                    async for event in stream:
                        if self._closed:
                            return
                        if event['registration_id'] != self._registration_id or event['key'] != 'tasks':
                            raise ValueError('Task event does not belong to the original observation')
                        value = event['delivery']
                        if (not isinstance(value, dict) or value.get('registration_id') != identifier
                                or value.get('route_id') != 'task-changes'
                                or not isinstance(value.get('id'), str) or not value['id']
                                or len(value['id'].encode()) > 1024
                                or not isinstance(value.get('events'), list)
                                or not all(isinstance(item, dict) for item in value['events'])
                                or type(event.get('sequence')) is not int or event['sequence'] < 1):
                            raise ValueError('Invalid original task event from Eyes CLI')
                        delivery = TaskChange(value['id'], identifier, value['route_id'], tuple(value['events']))
                        if await on_change(delivery) is not True:
                            raise RuntimeError('Goal did not durably accept the task event; retained without ACK')
                        # The callback durably records the delivery ID before
                        # this cumulative ACK. ACK loss can repeat the callback,
                        # never its business effects (Goal signal is idempotent).
                        await eyes_cli.invoke(owner, 'ack', str(event['sequence']))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Keep the unacknowledged event for original-owner recovery.
                    # Never fall back to direct ACP input or a new model loop.
                    self._state.update(status='unavailable', reason=str(exc))
                finally:
                    await stream.aclose()

            self._subscription = asyncio.create_task(consume(), name='goal-eyes-events')
            return self._state
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {'status': 'unavailable', 'reason': str(exc), 'config_path': str(config_path)}

    async def _close(self) -> None:
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        if self._subscription is not None:
            self._subscription.cancel()
            await asyncio.gather(self._subscription, return_exceptions=True)
        if self._owner and self._suspend and self._registration_attempted:
            # An ACK-loss outcome may not expose its ID. The original scoped
            # idempotency key still identifies only our registration.
            try:
                await eyes_cli.invoke(self._owner, 'pause', self._registration_id or 'tasks')
            except eyes_cli.EyesCLIError as exc:
                if self._registration_id is not None or str(exc) != (
                        'Unknown Eyes registration in this owner'):
                    raise

    async def aclose(self, *, suspend: bool = True) -> None:
        """Join local IO; optionally pause the owned source, never the daemon."""
        if self._close_task is None:
            self._suspend = suspend
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
