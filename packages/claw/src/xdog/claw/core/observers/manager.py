"""Gateway-owned Eyes integration: policy/receipts here, time computation in Eyes.

All ACP destinations are constructed from the current registered group and the
host's private endpoint. Only host-associated outbox records can cause reports.
No external provenance, saved definition or tool argument grants routing rights.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from xdog.agent.async_work import wait_owned
from xdog.agent.session_input import SessionInput, decode_input
from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.observers.journal import ObserverJournal
from xdog.claw.core.observers.models import ObserverRecord, ObserverStopped
from xdog.eyes.deliveries import Delivery
from xdog.eyes.events import DeliveryResult
from xdog.eyes.handlers.acp import AcpHandler
from xdog.eyes.handlers.acp_payload import enqueue_parameters
from xdog.eyes.handlers.contracts import HandlerSnapshot
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import JsonValue
from xdog.eyes.managed_scheduler import SchedulerClocks, run_managed_scheduler
from xdog.eyes.models import AcpHandlerDef, AtSchedule, HandlerDef, ObserverDef, SchedulerSourceDef, SourceDef
from xdog.eyes.service import ObserverService

if TYPE_CHECKING:
    from xdog.claw.core.runtime.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def canonical_message(delivery: Delivery, definition: ObserverDef, cwd: Path) -> SessionInput:
    handler = definition.handlers[0]
    if not isinstance(handler, AcpHandlerDef):
        raise ValueError('Managed observers require the host ACP route')
    params = enqueue_parameters(delivery, session_id=handler.session, cwd=str(cwd), instruction=handler.instruction)
    return decode_input({'id': params['message_id'], 'origin': 'acp', 'source': params['source'],
                         'instruction': params['instruction'], 'evidence': params['evidence'],
                         'provenance': params['provenance']})


class _FileHandler:
    """Compose the generic ACP handler; this adapter belongs to Claw, never Eyes."""
    def __init__(self, owner: ManagedObservers, record: ObserverRecord, definition: HandlerDef) -> None:
        if not isinstance(definition, AcpHandlerDef):
            raise ValueError('Only the current group ACP route is allowed')
        self.owner, self.record = owner, record
        self.inner = AcpHandler(definition, cwd=record.cwd)

    async def start(self) -> None:
        await self.inner.start()
        await self.owner._namespace(self.record, self.inner)
        self.owner._handlers[self.record.id] = self.inner

    def validate(self, value: Delivery) -> None:
        self.inner.validate(value)

    async def deliver(self, value: Delivery) -> DeliveryResult:
        from dataclasses import asdict
        definition = self.owner._definition(self.record)
        await self.owner.store.save(self.record, {'pending': json.loads(json.dumps(asdict(value)))},
                                    (canonical_message(value, definition, self.record.cwd),))
        return await self.owner._deliver(self.record, definition, value)

    async def check(self) -> None:
        await self.inner.check()

    async def resume(self) -> bool:
        return await self.inner.resume()

    def snapshot(self) -> HandlerSnapshot:
        return self.inner.snapshot()

    async def close(self) -> None:
        await self.inner.close()
        if self.owner._handlers.get(self.record.id) is self.inner:
            self.owner._handlers.pop(self.record.id, None)


class ManagedObservers:
    def __init__(self, application: Orchestrator, endpoint: Path, *,
                 clocks: SchedulerClocks = SchedulerClocks()) -> None:
        self.orchestrator = application
        self.endpoint, self.clocks = endpoint.absolute(), clocks
        self._store: ObserverJournal | None = None
        self._inputs: InputService | None = None
        self._opening: asyncio.Task[None] | None = None
        self._report_signal = asyncio.Event()
        self._report_task: asyncio.Task[None] | None = None
        self._unsubscribe: Any = None
        self._unproject: Any = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._handlers: dict[str, AcpHandler] = {}
        self._files: dict[str, ObserverService] = {}
        self._faults: dict[str, str] = {}
        self._operations: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def store(self) -> ObserverJournal:
        if self._store is None:
            raise RuntimeError('Managed observers must initialize their journal before use')
        return self._store

    @property
    def inputs(self) -> InputService:
        if self._inputs is None:
            raise RuntimeError('Managed observer input port is not ready')
        return self._inputs

    async def _ensure_store(self) -> None:
        if self._opening is None:
            self._opening = asyncio.create_task(self._open_store())
        await wait_owned(self._opening)

    async def _open_store(self) -> None:
        self._inputs = await self.orchestrator.input_service()
        self._store = await ObserverJournal.open(self.orchestrator.data_dir / 'observers' / 'journal.sqlite3',
                                                 self._inputs.store)
        from xdog.claw.core.planning.notification_adapter import project_notification
        async def project(receipt: InputRecord, value: Any) -> Any:
            return await project_notification(self.orchestrator, self.store, receipt, value)
        self._unproject = self.orchestrator.project_inputs(project)
        self._unsubscribe = self.orchestrator.subscribe_inputs(lambda _scope: self._report_signal.set())
        self._report_task = asyncio.create_task(self._reports(), name='claw-observer-reports')
        self._report_signal.set()

    async def _reports(self) -> None:
        try:
            while not self._closing:
                self._report_signal.clear()
                for receipt in await self.store.pending_reports():
                    await self._report(receipt)
                await self._report_signal.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Observer report reconciliation stopped; inspect original outbox')

    async def _report(self, receipt: InputRecord) -> None:
        if not await self.store.begin_delivery(receipt):
            return
        try:
            if receipt.status == 'succeeded' and not receipt.response.strip():
                raise ValueError('Agent produced no report')
            report = receipt.response if receipt.status == 'succeeded' else '观察任务执行失败: ' + receipt.error
            outcomes = await self.orchestrator.deliver_report(receipt.envelope.scope.group_id, report)
            errors = [item for item in outcomes if item['status'] != 'delivered']
            unknown = any(item['status'] == 'unknown' for item in errors)
            status = 'unknown' if unknown else 'failed' if errors else 'delivered'
            await self.store.finish_delivery(receipt, status=status,
                error=json.dumps(outcomes, ensure_ascii=False) if errors else '')
        except asyncio.CancelledError:
            await self.store.finish_delivery(receipt, status='unknown',
                                             error='Report send interrupted; inspect effects')
            raise
        except Exception as exc:
            await self.store.finish_delivery(receipt, status='failed', error=str(exc))

    def _cwd(self, group: str) -> Path:
        value = self.orchestrator.get_group_workspace(group)
        if value is None:
            raise ValueError('Unknown current group; Eyes cannot create or redirect groups')
        return value

    def _definition(self, record: ObserverRecord) -> ObserverDef:
        if self._cwd(record.group_id) != record.cwd:
            raise ValueError('Observer workspace changed; inspect before resuming')
        definition = parse(record.definition, base_dir=record.cwd)
        if len(definition.handlers) != 1 or not isinstance(definition.handlers[0], AcpHandlerDef):
            raise ValueError('Expected exactly one host-owned ACP handler')
        handler = definition.handlers[0]
        if (handler.session != 'group:' + record.group_id or handler.connection.transport != 'unix'
                or handler.connection.path != self.endpoint or handler.delivery != 'enqueue'):
            raise ValueError('Observer target does not match the current group private endpoint')
        return definition

    def definition(self, group: str, name: str, source: dict[str, Any], instruction: str) -> ObserverDef:
        if not isinstance(name, str) or not name.strip() or len(name.encode()) > 256:
            raise ValueError('Expected a bounded observer name')
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction.encode()) > 256 * 1024:
            raise ValueError('Expected a bounded processing instruction')
        if not isinstance(source, dict) or source.get('type') not in {'file', 'scheduler'}:
            raise ValueError('Expected an existing Eyes file or scheduler source definition')
        source = {'id': 'source', **source}
        event = 'scheduler.due' if source['type'] == 'scheduler' else 'file.change'
        events = ['scheduler.due'] if event == 'scheduler.due' else ['file.created', 'file.modified', 'file.deleted']
        definition = parse({'version': 2, 'name': name, 'sources': [source], 'detectors': [
            {'id': 'detector', 'type': event, 'source': source['id']}], 'handlers': [
            {'id': 'handler', 'type': 'acp', 'connection': {'transport': 'unix', 'path': str(self.endpoint)},
             'session': 'group:' + group, 'delivery': 'enqueue', 'instruction': instruction}], 'routes': [
            {'id': 'route', 'detector': 'detector', 'handler': 'handler', 'event_types': events}]},
            base_dir=self._cwd(group))
        source_def = definition.sources[0]
        if isinstance(source_def, SourceDef) and not source_def.root.is_relative_to(self._cwd(group)):
            raise ValueError('Managed file observation must stay in the current group workspace')
        return definition

    async def _namespace(self, record: ObserverRecord, handler: AcpHandler) -> None:
        if not record.inbox_id and await self.store.get(record.group_id, record.cwd, record.id) is not None:
            raise ValueError('Saved observer lacks a trusted inbox namespace; inspect before resuming')
        expected = record.inbox_id or await self.inputs.store.namespace(InputScope(record.group_id, record.cwd))
        if handler.client.inbox_id != expected:
            raise ValueError('ACP inbox namespace changed; no saved work was resent')

    async def _handler(self, record: ObserverRecord, definition: ObserverDef) -> AcpHandler:
        handler = self._handlers.get(record.id)
        if handler is None:
            target = definition.handlers[0]
            assert isinstance(target, AcpHandlerDef)
            handler = AcpHandler(target, cwd=record.cwd)
            try:
                await handler.start()
                await self._namespace(record, handler)
            except BaseException:
                await handler.close()
                raise
            self._handlers[record.id] = handler
        return handler

    async def create(self, group: str, name: str, source: dict[str, Any], instruction: str, *,
                     owner: str = "", notify_only: bool = False) -> dict[str, Any]:
        if self._closing:
            raise RuntimeError('Managed Eyes is closing')
        await self._ensure_store()
        task = asyncio.create_task(self._create(group, name, source, instruction,
                                               owner=owner, notify_only=notify_only))
        self._operations.add(task)
        try:
            return await wait_owned(task)
        finally:
            self._operations.discard(task)

    async def _create(self, group: str, name: str, source: dict[str, Any], instruction: str, *,
                      owner: str = "", notify_only: bool = False) -> dict[str, Any]:
        from xdog.eyes.managed_scheduler import initial_managed_state
        if (not isinstance(owner, str) or len(owner.encode()) > 256
                or owner and not owner.strip() or type(notify_only) is not bool):
            raise ValueError('Expected bounded host owner and notify_only boolean')
        async with self._lock:
            if self._closing:
                raise RuntimeError('Managed Eyes is closing')
            definition = self.definition(group, name, source, instruction)
            data = definition_data(definition)
            previous = next((item for item in await self.store.list(group, self._cwd(group))
                             if item.name == name), None)
            if previous is not None:
                if previous.definition != data or previous.owner != owner or previous.notify_only != notify_only:
                    raise ValueError('Observer name already has different content; use a new name')
                if previous.enabled:
                    await self._activate(previous)  # Explicit file start, never implicit restart.
                return await self.status(group, previous.id)
            identifier = 'eyes-' + uuid.uuid4().hex
            record = ObserverRecord(identifier, group, self._cwd(group), name, data, 1, True,
                'managed' if isinstance(definition.sources[0], SchedulerSourceDef) else 'ephemeral', {}, '',
                owner=owner, notify_only=notify_only)
            # Validate fresh deadlines before opening connections, then seed the
            # interval after the actual handler connects. Both operations are model-free.
            accepted = (initial_managed_state(definition, identifier, self.clocks)
                        if record.recovery == 'managed' else {})
            try:
                if record.recovery == 'managed':
                    await self._handler(record, definition)
                source_def = definition.sources[0]
                # An at accepted before connection setup retains its deadline;
                # repeating phases begin only after the handler is ready.
                state = (accepted if isinstance(source_def, SchedulerSourceDef)
                         and isinstance(source_def.schedule, AtSchedule) else
                         initial_managed_state(definition, identifier, self.clocks)
                         if record.recovery == 'managed' else {})
                record = await self.store.create(group, record.cwd, name, data, identifier=identifier,
                                                 recovery=record.recovery, state=cast(dict[str, Any], state),
                                                 owner=owner, notify_only=notify_only)
            except BaseException:
                handler = self._handlers.pop(identifier, None)
                if handler is not None:
                    await handler.close()
                raise
            await self._activate(record)
            return await self.status(group, record.id)

    async def _save(self, record: ObserverRecord, definition: ObserverDef, state: dict[str, JsonValue]) -> None:
        from xdog.eyes.managed_scheduler import restore_pending_delivery
        delivery = restore_pending_delivery(definition, record.id, state)
        messages = () if delivery is None else (canonical_message(delivery, definition, record.cwd),)
        await self.store.save(record, cast(dict[str, Any], state), messages)

    async def _deliver(self, record: ObserverRecord, definition: ObserverDef, delivery: Delivery) -> DeliveryResult:
        current = await self.store.message(record, delivery.id)
        if current != canonical_message(delivery, definition, record.cwd):
            raise ValueError('Delivery was not committed with its source cursor')
        scope = InputScope(record.group_id, record.cwd)
        while not self._closing:
            current_record = await self.store.get(record.group_id, record.cwd, record.id)
            receipt = await self.inputs.store.get(scope, delivery.id)
            if receipt is not None:
                await self.store.sync(record)
                if receipt.status in {'interrupted', 'cancelled'}:
                    await self.store.fail(record, receipt.error or receipt.status, review=True)
                    raise ObserverStopped('Interrupted/cancelled input is not automatically replayed')
                if receipt.status in {'succeeded', 'failed'}:
                    self._report_signal.set()
                    occurrence = await self.store.occurrence(receipt)
                    if occurrence is None:
                        raise ValueError('Original observer outbox disappeared')
                    if occurrence['delivery_status'] in {'delivered', 'failed', 'not-required'}:
                        return DeliveryResult('queued', receipt_status='duplicate', message_state=receipt.status)
                    if occurrence['delivery_status'] == 'unknown':
                        await self.store.fail(record, 'Unknown output; inspect before new work', review=True)
                        raise ObserverStopped('Unknown channel delivery is not replayed')
            else:
                if (current_record is None or not current_record.enabled
                        or current_record.revision != record.revision):
                    raise ObserverStopped('Observer stopped before new transport admission')
                try:
                    handler = await self._handler(record, definition)
                    await self._namespace(record, handler)
                    if not await self.store.transport_started(record, delivery.id):
                        raise ObserverStopped('Observer stopped before transport admission')
                    result = await handler.deliver(delivery)
                    await self.store.transport_finished(record, delivery.id,
                        status='queued' if result.status in {'queued', 'succeeded'} else
                               'not-sent' if result.status == 'not-sent' else 'unknown')
                    if result.status not in {'queued', 'succeeded'}:
                        raise ConnectionError(result.error or result.status)
                except asyncio.CancelledError:
                    raise
                except ValueError as exc:
                    await self.store.fail(record, str(exc), review=True)
                    raise ObserverStopped('Target binding changed; original work not resent') from exc
                except ObserverStopped:
                    raise
                except Exception as exc:
                    await self.store.transport_finished(record, delivery.id, status='unknown')
                    await self.store.fail(record, 'Waiting for original-ID intake: ' + str(exc))
                    rejected = self._handlers.pop(record.id, None)
                    if rejected is not None:
                        await rejected.close()
            await self.clocks.sleep(.1)
        raise asyncio.CancelledError

    async def _run(self, record: ObserverRecord, cancel: asyncio.Event) -> None:
        try:
            definition = self._definition(record)
            await run_managed_scheduler(definition, record.id, record.state or None,
                lambda state: self._save(record, definition, state),
                lambda delivery: self._deliver(record, definition, delivery), cancel, self.clocks)
            if not cancel.is_set() and not self._closing:
                current = await self.store.get(record.group_id, record.cwd, record.id)
                if (current and current.state.get('cursor', {}).get('completed')
                        and current.state.get('pending') is None):
                    await self.store.complete(record)
        except ObserverStopped:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._faults[record.id] = str(exc)
            logger.exception('Managed observer %s stopped', record.id)
            try:
                await self.store.fail(record, str(exc), review=True)
            except Exception:
                logger.exception('Observer failure could not be persisted; runtime remains halted')
        finally:
            handler = self._handlers.pop(record.id, None)
            if handler is not None:
                await handler.close()

    def _finished(self, identifier: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(identifier, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._faults[identifier] = str(error)
            logger.error('Observer cleanup failed: %s', error)

    async def _activate(self, record: ObserverRecord) -> None:
        if self._closing or not record.enabled or record.id in self._tasks or record.id in self._files:
            return
        definition = self._definition(record)
        self._faults.pop(record.id, None)
        if isinstance(definition.sources[0], SchedulerSourceDef):
            cancel = self._cancel.setdefault(record.id, asyncio.Event())
            task = asyncio.create_task(self._run(record, cancel), name='claw-eyes:' + record.id)
            self._tasks[record.id] = task
            task.add_done_callback(lambda done: self._finished(record.id, done))
        elif record.owner and record.state.get('pending') is not None:
            cancel = self._cancel.setdefault(record.id, asyncio.Event())
            task = asyncio.create_task(self._restore_owned_file(record, cancel), name='claw-file-outbox:' + record.id)
            self._tasks[record.id] = task
            task.add_done_callback(lambda done: self._finished(record.id, done))
        else:
            await self._start_file(record, definition)

    async def _start_file(self, record: ObserverRecord, definition: ObserverDef) -> None:
        service = ObserverService(cwd=record.cwd,
            handler_factory=lambda target: _FileHandler(self, record, target))
        try:
            await service.start(definition)
        except BaseException:
            await service.aclose()
            raise
        self._files[record.id] = service

    async def _restore_owned_file(self, record: ObserverRecord, cancel: asyncio.Event) -> None:
        """Reconcile an original-ID outbox before admitting a new file baseline.

        Only explicit host-owned recovery uses this path. A fresh baseline is
        never used as evidence that a pre-crash event was already delivered.
        """
        try:
            pending = record.state['pending']
            if (not isinstance(pending, dict) or set(pending) != {'id', 'registration_id', 'route_id', 'events'}
                    or not isinstance(pending.get('registration_id'), str)
                    or not isinstance(pending.get('id'), str)):
                raise ValueError('Invalid owned file pending identity')
            identifier = pending['id']
            message = await self.store.message(record, identifier)
            if (message is None or message.id != identifier
                    or message.provenance.get('registration_id') != pending['registration_id']
                    or message.provenance.get('route_id') != pending.get('route_id')
                    or json.dumps(message.evidence, sort_keys=True, allow_nan=False)
                    != json.dumps(pending['events'], sort_keys=True, allow_nan=False)):
                raise ValueError('Owned file pending event lacks its matching immutable outbox')
            namespace = await self.inputs.store.namespace(InputScope(record.group_id, record.cwd))
            if namespace != record.inbox_id:
                raise ValueError('Inbox namespace changed; no saved work was resent')
            receipt = await self.inputs.store.get(InputScope(record.group_id, record.cwd), identifier)
            if receipt is None:
                # Host-owned destination/namespace was checked above; this is
                # exactly the existing canonical leased ACP intake path.
                if not await self.store.transport_started(record, identifier):
                    raise ObserverStopped('Owned source stopped before transport')
                handler = await self._handler(record, self._definition(record))
                await handler.client.enqueue({'session_id': 'group:' + record.group_id, 'cwd': str(record.cwd),
                    'message_id': message.id, 'instruction': message.instruction, 'source': message.source,
                    'evidence': list(message.evidence), 'provenance': message.provenance})
                await self.store.transport_finished(record, identifier, status='queued')
            while not cancel.is_set() and not self._closing:
                current = await self.store.get(record.group_id, record.cwd, record.id)
                if current is None or not current.enabled or current.revision != record.revision:
                    return
                receipt = await self.inputs.store.get(InputScope(record.group_id, record.cwd), identifier)
                if receipt is not None:
                    if receipt.status in {'interrupted', 'cancelled'}:
                        raise ValueError('Interrupted file-event execution is not automatically replayed')
                    if receipt.status in {'succeeded', 'failed'}:
                        await self.store.sync(record)
                        self._report_signal.set()
                        occurrence = await self.store.occurrence(receipt)
                        if occurrence is None or occurrence['delivery_status'] == 'unknown':
                            raise ValueError('Owned file event has unknown delivery; inspect before restarting')
                        if occurrence['delivery_status'] in {'delivered', 'failed', 'not-required'}:
                            await self.store.save(record, {'pending': None})
                            await self._start_file(record, self._definition(record))
                            return
                sleep = asyncio.ensure_future(self.clocks.sleep(.1))
                stopped = asyncio.create_task(cancel.wait())
                try:
                    await asyncio.wait((sleep, stopped), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    sleep.cancel()
                    stopped.cancel()
                    await asyncio.gather(sleep, stopped, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._faults[record.id] = str(exc)
            await self.store.fail(record, str(exc), review=True)

    async def start(self) -> None:
        await self._ensure_store()
        for record in await self.store.list():
            if record.enabled and record.recovery == 'managed':
                try:
                    await self._activate(record)
                except Exception as exc:
                    await self.store.fail(record, str(exc), review=True)
        self.inputs.resume()

    async def resume_owned(self, group: str, identifier: str, *, owner: str) -> dict[str, Any]:
        """Host-only explicit restoration of a Goal-owned file observer.

        This never starts ordinary file observers, changes routing, or re-enables
        cancelled/review work. The Goal host decides which owned IDs to restore.
        """
        if self._closing:
            raise RuntimeError('Managed Eyes is closing')
        await self._ensure_store()
        task = asyncio.create_task(self._resume_owned(group, identifier, owner))
        self._operations.add(task)
        try:
            return await wait_owned(task)
        finally:
            self._operations.discard(task)

    async def _resume_owned(self, group: str, identifier: str, owner: str) -> dict[str, Any]:
        async with self._lock:
            if self._closing:
                raise RuntimeError('Managed Eyes is closing')
            record = await self.store.get(group, self._cwd(group), identifier)
            if record is None or not record.owner or record.owner != owner:
                raise ValueError('Unknown owned observer in the current group/workspace')
            definition = self._definition(record)
            if not isinstance(definition.sources[0], SourceDef):
                raise ValueError('Only explicitly owned file observers can be resumed')
            # Cancelled/review records stay terminal; no implicit model replay.
            if record.enabled and record.recovery == 'ephemeral':
                await self._activate(record)
            return await self.status(group, identifier)

    async def status(self, group: str, identifier: str) -> dict[str, Any]:
        await self._ensure_store()
        record = await self.store.get(group, self._cwd(group), identifier)
        if record is None:
            raise ValueError('Unknown observer in the current group/workspace')
        await self.store.sync(record)
        updated = await self.store.get(group, self._cwd(group), identifier)
        if updated is None:
            raise ValueError('Observer disappeared during status reconciliation')
        record = updated
        source = record.definition.get('sources', [{}])[0]
        state = (record.recovery if record.recovery in {'needs_review', 'cancelled'} else
                 'failed' if identifier in self._faults else record.recovery)
        runtime: dict[str, JsonValue] | None = None
        if record.enabled:
            if identifier in self._tasks:
                state = 'running'
            elif identifier in self._files:
                runtime = await self._files[identifier].status()
                registrations = runtime['registrations']
                if isinstance(registrations, list) and registrations and isinstance(registrations[0], dict):
                    state = str(registrations[0]['state'])
            elif record.recovery == 'ephemeral':
                state = 'needs-start'
        cursor = record.state.get('cursor', {})
        next_due = cursor.get('next_utc') if record.enabled and not cursor.get('completed') else None
        handlers = record.definition.get('handlers', [{}])
        instruction = handlers[0].get('instruction', '') if handlers else ''
        return {'id': record.id, 'name': record.name, 'enabled': record.enabled, 'revision': record.revision,
                'state': state, 'source': source, 'instruction': instruction, 'next_due': next_due,
                'error': record.error or self._faults.get(identifier, ''), 'runtime': runtime,
                'owner': record.owner, 'notify_only': record.notify_only, 'reports_enabled': record.reports_enabled,
                'history_window': 64, 'occurrences': await self.store.history(group, record.cwd, identifier)}

    async def list(self, group: str) -> tuple[dict[str, Any], ...]:
        await self._ensure_store()
        # Keep inventory bounded: full source/instruction/history is available
        # through status rather than multiplying one-MiB definitions by 128.
        results = []
        for item in await self.store.list(group, self._cwd(group)):
            status = await self.status(group, item.id)
            summary = {key: status[key] for key in ('id', 'name', 'enabled', 'revision', 'state', 'next_due',
                                                                      'owner', 'notify_only')}
            summary['source_type'] = status['source'].get('type', 'invalid')
            for key in ('instruction', 'error'):
                value = status[key]
                summary[key] = value[:256] + '… [full value: status]' if len(value) > 256 else value
            summary['history_window'] = 1
            summary['occurrences'] = status['occurrences'][:1]
            # Display-only previews are marked; stored evidence is never truncated.
            if summary['occurrences']:
                summary['occurrences'] = [{key: value for key, value in summary['occurrences'][0].items()
                                          if key not in {'execution_error', 'delivery_error'}}]
            results.append(summary)
        return tuple(results)

    async def cancel(self, group: str, identifier: str) -> dict[str, Any]:
        await self._ensure_store()
        result = await self.store.cancel(group, self._cwd(group), identifier)
        if identifier in self._cancel:
            self._cancel[identifier].set()
        task = self._tasks.get(identifier)
        if task is not None:
            await wait_owned(task)
        service = self._files.pop(identifier, None)
        if service is not None:
            await service.aclose()
        return {**result, 'id': identifier,
                'notice': 'Stopped future collection; accepted inputs and reports were not withdrawn'}

    async def cancel_notifications(self, group: str, identifier: str) -> dict[str, Any]:
        await self._ensure_store()
        await self.store.cancel_notifications(group, self._cwd(group), identifier)
        return {'id': identifier, 'notifications_cancelled': True, 'execution_cancelled': False}

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        for cancel in self._cancel.values():
            cancel.set()
        for task in tuple(self._operations):
            task.cancel()
        await asyncio.gather(*tuple(self._operations), *tuple(self._tasks.values()), return_exceptions=True)
        await asyncio.gather(*(service.aclose() for service in self._files.values()), return_exceptions=False)
        self._files.clear()
        await asyncio.gather(*(handler.close() for handler in self._handlers.values()), return_exceptions=True)
        self._handlers.clear()
        if self._unsubscribe is not None:
            self._unsubscribe()
        if self._unproject is not None:
            self._unproject()
        if self._report_task is not None:
            self._report_task.cancel()
            await asyncio.gather(self._report_task, return_exceptions=True)
        if self._opening is not None:
            await asyncio.gather(self._opening, return_exceptions=True)
        if self._store is not None:
            await self._store.close()
