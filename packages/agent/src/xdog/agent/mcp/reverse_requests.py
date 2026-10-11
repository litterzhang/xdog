"""Bounded legacy reverse requests: reader-independent, owned and never replayed.

A registered host handler owns human review/model permission. Merely configuring a
server never installs one. This is transport infrastructure, not a UI approval gate.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from xdog.agent.mcp.client_capabilities import reverse_response
from xdog.agent.mcp.task_notifications import TaskNotifications
from xdog.agent.mcp.task_registry import TaskRegistry, TaskRegistryError

ReverseHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
TaskHandler = Callable[[dict[str, Any], str, TaskRegistry], Awaitable[dict[str, Any]]]
ReverseWriter = Callable[[dict[str, Any]], Awaitable[Any]]


class ReverseRequests:
    def __init__(self) -> None:
        self.handler: ReverseHandler | None = None
        self.sampling_tools = False
        self.sampling_context = False
        self.elicitation_handler: ReverseHandler | None = None
        self.elicitation_completion: Callable[[dict[str, Any]], None] | None = None
        self.tasks: TaskRegistry | None = None
        self.sampling_task_handler: TaskHandler | None = None
        self.elicitation_task_handler: TaskHandler | None = None
        self.status = TaskNotifications()
        self.active = False
        self._jobs: dict[str | int, asyncio.Task[None]] = {}
        self._used: set[str | int] = set()
        self._methods: dict[str | int, str] = {}

    def advertised(self) -> dict[str, Any]:
        if not self.active:
            return {}
        capabilities: dict[str, Any] = {}
        if self.handler is not None:
            capabilities['sampling'] = {
                **({'tools': {}} if self.sampling_tools else {}),
                **({'context': {}} if self.sampling_context else {})}
        if self.elicitation_handler is not None:
            capabilities['elicitation'] = {'form': {}, 'url': {}}
        if self.tasks is not None:
            controls: dict[str, Any] = {'list': {}, 'cancel': {}}
            families: dict[str, Any] = {}
            if self.handler is not None and self.sampling_task_handler is not None:
                families['sampling'] = {'createMessage': {}}
            if self.elicitation_handler is not None and self.elicitation_task_handler is not None:
                families['elicitation'] = {'create': {}}
            if families:
                controls['requests'] = families
            capabilities['tasks'] = controls
        return capabilities

    async def negotiated(self, version: str) -> None:
        """Only the 2025-11-25 legacy contract has this task vocabulary."""
        if version == '2025-11-25':
            return
        registry = self.tasks
        self.tasks = None
        self.sampling_task_handler = None
        self.elicitation_task_handler = None
        if registry is not None:
            await registry.close()

    async def _task_control(self, message: dict[str, Any]) -> dict[str, Any]:
        registry = self.tasks
        if registry is None:
            raise TaskRegistryError('Task registry withdrawn')
        method, params = message['method'], message.get('params', {})
        allowed = {'_meta', 'cursor'} if method == 'tasks/list' else {'_meta', 'taskId'}
        if (not isinstance(params, dict) or set(params) - allowed
                or '_meta' in params and not isinstance(params['_meta'], dict)):
            raise TaskRegistryError('Invalid task control parameters')
        if method == 'tasks/list':
            if 'cursor' in params:
                raise TaskRegistryError('Task registry has one bounded page; cursor unavailable')
            return registry.list()
        identifier = params.get('taskId')
        if not isinstance(identifier, str) or not 0 < len(identifier.encode()) <= 128:
            raise TaskRegistryError('Invalid task control identity')
        if method == 'tasks/get':
            return registry.state(identifier)
        if method == 'tasks/cancel':
            return await registry.cancel(identifier)
        # Result retrieval waits in its own bounded reverse-control job, never
        # the reader. Cancellation stops this read, not the original task.
        while True:
            status = registry.state(identifier)['status']
            if status in {'completed', 'failed', 'cancelled'}:
                return registry.result(identifier)
            if not self.active or self.tasks is not registry:
                raise TaskRegistryError('Task connection/registry withdrawn')
            await asyncio.sleep(.025)

    def admit(self, message: dict[str, Any], write: ReverseWriter, *, timeout: float) -> bool:
        # Validate before registering a job or reflecting the request identity.
        method = message.get('method')
        task_handler = (self.sampling_task_handler if method == 'sampling/createMessage' else
                        self.elicitation_task_handler if method == 'elicitation/create' else None)
        params = message.get('params')
        creation = (self.tasks is not None and task_handler is not None and isinstance(params, dict)
                    and 'task' in params)
        # A peer-supplied related-task identity is not authority to attach to an
        # existing task. Creation receives a fresh local identity only.
        related = (isinstance(params, dict) and isinstance(params.get('_meta'), dict)
                   and 'io.modelcontextprotocol/related-task' in params['_meta'])
        validation = reverse_response(message, task_creation=creation and not related)
        task_control = method in {'tasks/list', 'tasks/get', 'tasks/result', 'tasks/cancel'}
        registry = self.tasks
        handler = (self._task_control if task_control and registry is not None else
                   self.handler if method == 'sampling/createMessage' else
                   self.elicitation_handler if method == 'elicitation/create' else None)
        if (handler is None or not self.active
                or validation.get('error', {}).get('code') != -32601):
            return False
        identifier = message['id']
        # Result long-polls and human review must not consume the slots needed
        # to cancel/get/list a task. Each lane is independently bounded; the
        # shared connection lifetime identity budget still prevents replay/flood.
        def lane(name: str) -> str:
            return 'result' if name == 'tasks/result' else 'control' if name.startswith('tasks/') else 'review'
        occupied = sum(lane(name) == lane(str(method)) for name in self._methods.values())
        if identifier in self._used or len(self._used) >= 256 or occupied >= 4:
            # Refuse rather than cache/replay a model response. Caller emits the
            # fixed refusal via its ordinary safe reverse responder.
            return False
        try:
            from xdog.agent.mcp.schema import json_value
            snapshot = json.loads(json_value(message, maximum=256 * 1024))
        except (ValueError, RuntimeError):
            return False
        def current() -> bool:
            if task_control:
                return self.active and self.tasks is registry
            return (self.active and handler is (self.handler if method == 'sampling/createMessage'
                                               else self.elicitation_handler)
                    and (not creation or self.tasks is registry and task_handler is
                         (self.sampling_task_handler if method == 'sampling/createMessage' else
                          self.elicitation_task_handler)))
        self._used.add(identifier)
        async def work() -> None:
            response: dict[str, Any]
            created_id = ''
            delivered = False
            try:
                try:
                    async with asyncio.timeout(timeout):
                        if task_control:
                            result = await handler(snapshot)
                        else:
                            validate_result: Callable[[Any, dict[str, Any]], dict[str, Any]]
                            if method == 'sampling/createMessage':
                                from xdog.agent.mcp.sampling import request
                                from xdog.agent.mcp.sampling import result as validate_result
                            else:
                                from xdog.agent.mcp.elicitation import request
                                from xdog.agent.mcp.elicitation import result as validate_result
                            from xdog.agent.supervision_transfer import settled_image_work
                            validated = await settled_image_work(lambda: request(snapshot))
                            # Canonical validation authority is separate from callback edits.
                            if creation:
                                if registry is None or task_handler is None or not current():
                                    raise TaskRegistryError('Task creation registration withdrawn')
                                envelope = validated['params']['task']
                                ttl = envelope.get('ttl', 600000)
                                async def operation(task_id: str) -> dict[str, Any]:
                                    if not current():
                                        raise TaskRegistryError('Task host withdrawn')
                                    assert task_handler is not None
                                    output = await task_handler(json.loads(json.dumps(validated)), task_id, registry)
                                    output = await settled_image_work(lambda: validate_result(output, validated))
                                    if not current():
                                        raise TaskRegistryError('Task host changed before result retention')
                                    metadata = output.setdefault('_meta', {})
                                    if 'io.modelcontextprotocol/related-task' in metadata:
                                        raise TaskRegistryError('Task result identity requires original registry')
                                    metadata['io.modelcontextprotocol/related-task'] = {'taskId': task_id}
                                    return output
                                result = registry.create(str(method), operation, ttl=ttl)
                                created_id = result['task']['taskId']
                            else:
                                result = await handler(json.loads(json.dumps(validated)))
                                result = await settled_image_work(lambda: validate_result(result, validated))
                    if not current():
                        return  # Revoked host handler cannot deliver into its successor.
                    from xdog.agent.mcp.schema import json_value
                    response = {'result': json.loads(json_value(result, maximum=256 * 1024))}
                    if not isinstance(response['result'], dict):
                        raise ValueError('Invalid reverse handler result')
                except asyncio.CancelledError:
                    if not current():
                        return
                    response = {'error': {'code': -32000, 'message': 'Client request canceled; no replay'}}
                except Exception:
                    response = {'error': {'code': -32000, 'message': 'Client request refused/failed; no replay'}}
                if current():
                    try:
                        # Delivery is part of bounded request ownership too. A
                        # stuck writer cannot leave an unknown task running.
                        async with asyncio.timeout(timeout):
                            await write({'jsonrpc': '2.0', 'id': identifier, **response})
                        delivered = 'result' in response
                        if creation and delivered and registry is not None:
                            self.status.start(registry, created_id, str(method), write, current, timeout)
                    except (ValueError, RuntimeError, OSError, TimeoutError):
                        pass  # No response replay after uncertain delivery.
            finally:
                if created_id and not delivered and registry is not None:
                    cleanup = asyncio.create_task(registry.abandon(created_id))
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue  # Actual owned finally retains the reverse slot.
                    cleanup.result()
        task = asyncio.create_task(work())
        self._jobs[identifier] = task
        self._methods[identifier] = str(method)
        def done(completed: asyncio.Task[None]) -> None:
            if self._jobs.get(identifier) is completed:
                self._jobs.pop(identifier, None)
                self._methods.pop(identifier, None)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)
        return True

    def notification(self, message: dict[str, Any]) -> bool:
        if message.get('method') != 'notifications/elicitation/complete' or 'id' in message:
            return False
        if self.active and self.elicitation_handler is not None and self.elicitation_completion is not None:
            self.elicitation_completion(message)
        return True

    def cancel(self, message: dict[str, Any]) -> bool:
        if message.get('method') != 'notifications/cancelled' or 'id' in message:
            return False
        params = message.get('params')
        if not isinstance(params, dict):
            return True
        identifier = params.get('requestId')
        if type(identifier) not in (str, int):
            return True
        task = self._jobs.get(cast(str | int, identifier))
        if task is not None and not task.cancelling():
            task.cancel()
        return True

    async def withdraw(self, method: str) -> None:
        """Revoke one family without canceling other reverse jobs or resetting dedup."""
        if method == 'sampling/createMessage':
            self.handler = None
            self.sampling_task_handler = None
            self.sampling_tools = self.sampling_context = False
        elif method == 'elicitation/create':
            self.elicitation_handler = None
            self.elicitation_task_handler = None
            self.elicitation_completion = None
        else:
            raise ValueError('Unsupported reverse capability withdrawal')
        jobs = tuple(task for identifier, task in self._jobs.items() if self._methods.get(identifier) == method)
        for task in jobs:
            if not task.cancelling():
                task.cancel()
        cleanup = asyncio.create_task(self.tasks.withdraw(method)) if self.tasks is not None else None
        notifications = asyncio.create_task(self.status.close(method))
        settlement = asyncio.gather(*jobs, notifications, *([cleanup] if cleanup is not None else []),
                                    return_exceptions=True)
        cancelled = False
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                cancelled = True
        settlement.result()
        if cancelled:
            raise asyncio.CancelledError

    async def close(self) -> None:
        self.active = False  # Revoke delivery before any awaited callback cleanup.
        jobs = tuple(self._jobs.values())
        for task in jobs:
            if not task.cancelling():
                task.cancel()
        registry = self.tasks
        self.tasks = None
        self.sampling_task_handler = None
        self.elicitation_task_handler = None
        # Signal every task sibling while reverse cancel/result jobs settle. A
        # held cancellation finally must not keep another operation running.
        cleanup = asyncio.create_task(registry.close()) if registry is not None else None
        notifications = asyncio.create_task(self.status.close())
        settlement = asyncio.gather(*jobs, notifications, *([cleanup] if cleanup is not None else []),
                                    return_exceptions=True)
        cancelled = False
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                cancelled = True
        settlement.result()
        self._jobs.clear()
        self._methods.clear()
        self._used.clear()
        if cancelled:
            raise asyncio.CancelledError
