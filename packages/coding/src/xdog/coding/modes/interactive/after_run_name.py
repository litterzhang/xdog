"""Name CAS uses the same original native producer record as after-run compaction."""
from __future__ import annotations

from typing import Any

from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.extensions.name_request import AfterRunNames


class NativeAfterRunName:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.applying: object | None = None
        self.queue = AfterRunNames(lambda event: ui._event_queue.put(event))

    async def request(self, extension: str, dialog: dict[str, Any]) -> bool:
        ui, lease = self.ui, current_invocation()
        producer = ui._after_run_compaction.current
        if producer is None or lease is None or lease.child_id or not ui._after_run_compaction._running():
            return False
        session = producer.session
        def alive() -> bool:
            return (ui._after_run_compaction.current is producer and ui._session is session
                and session.agent is producer.agent and session.session_id == lease.session_id
                and session.session_data.active_branch == producer.branch and session.model == producer.model
                and ui._worker_generation == producer.generation and not ui._exit_requested
                and not producer.applying and ui._cancel_epoch == producer.cancel_epoch + int(self.applying is producer)
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)
        async def ask(name: str, value: dict[str, Any]) -> Any:
            return await session.extension_dialogs.ask(name, value) if alive() else False
        return await self.queue.reviewed(extension, dialog, producer=producer, ask=ask, alive=alive,
            settled=lambda: producer.settled,
            successful=lambda: producer.completed and producer.worker_success)

    def poll(self) -> None:
        ui = self.ui
        if ui._worker_active or ui._is_busy or ui._command is not None:
            return
        item = self.queue.take()
        if item is not None:
            self.applying = item.producer
            ui._handle_slash_command('name', '', rename_target=(item.name, item.expected_name), rename_alive=item.alive)
