"""Native parent-run settlement barrier for reviewed extension compaction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xdog.agent.extensions.compaction_request import AfterRunCompactions
from xdog.agent.extensions.invocation import current_invocation


@dataclass
class _Producer:
    generation: int
    cancel_epoch: int
    session: Any
    agent: Any
    branch: str
    model: str
    thread: Any
    completed: bool = False
    worker_success: bool = False
    applying: bool = False
    settled: bool = False


class NativeAfterRunCompaction:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.current: _Producer | None = None
        self.queue = AfterRunCompactions(lambda event: ui._event_queue.put(
            {**event, 'extension_session': event['session_id']}))

    def begin(self) -> _Producer:
        self.queue.discard('new-producer')
        ui, session = self.ui, self.ui._session
        self.current = item = _Producer(ui._worker_generation, ui._cancel_epoch, session, session.agent,
            session.session_data.active_branch, session.model, ui._worker_thread)
        return item

    def finish(self, producer: _Producer, success: bool) -> None:
        producer.completed = success

    def worker_finished(self, generation: int, success: bool) -> None:
        if self.current is not None and self.current.generation == generation:
            self.current.worker_success = success
            self.current.settled = True

    def _running(self) -> bool:
        runtime = getattr(self.ui._session, "runtime", None)
        return bool(self.ui._worker_active or runtime is not None and runtime.controller.current_request is not None)

    async def request(self, extension: str, dialog: dict[str, Any]) -> bool:
        producer = self.current
        lease = current_invocation()
        ui = self.ui
        if (producer is None or lease is None or lease.child_id or lease.session_id != producer.session.session_id
                or not self._running() or producer.session._compacting):
            return False

        tool_revision = producer.session.agent.tool_selection_revision
        permission_mode = producer.session.permissions.mode
        def alive() -> bool:
            session = producer.session
            return (self.current is producer and ui._session is session and session.agent is producer.agent
                and session.session_data.active_branch == producer.branch and session.model == producer.model
                and ui._worker_generation == producer.generation and not ui._exit_requested
                and ui._cancel_epoch == producer.cancel_epoch + int(producer.applying)
                and session.agent.tool_selection_revision == tool_revision
                and session.permissions.mode == permission_mode
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)

        async def ask(name: str, projected: dict[str, Any]) -> Any:
            return await producer.session.extension_dialogs.ask(name, projected) if alive() else False

        return await self.queue.reviewed(extension, dialog, producer=producer, ask=ask, alive=alive,
            settled=lambda: producer.settled,
            successful=lambda: producer.completed and producer.worker_success)

    def poll(self) -> None:
        ui = self.ui
        # turn_end is emitted before asyncio.run has shut down its loop. It is not
        # the barrier: wait for the original thread to actually exit as well.
        if ui._worker_active or ui._is_busy or ui._command is not None:
            return
        item = self.queue.take()
        if item is None:
            return
        producer = item.producer
        if not isinstance(producer, _Producer):
            return
        producer.applying = True
        ui._handle_slash_command('compact', '', compact_target=item.instructions, compact_alive=item.alive)
