"""Reviewed original-parent tool selections reuse the native producer settlement proof."""
from __future__ import annotations

from typing import Any

from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.active_tools_request import AfterRunToolSelections
from xdog.agent.extensions.invocation import current_invocation


class NativeAfterRunTools:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.queue = AfterRunToolSelections(lambda event: ui._event_queue.put(
            {**event, 'extension_session': event['session_id']}))

    async def request(self, extension: str, dialog: dict[str, Any]) -> bool:
        ui = self.ui
        producer = ui._after_run_compaction.current  # Observation only; no compaction grant is borrowed.
        lease = current_invocation()
        if (producer is None or lease is None or lease.child_id or not ui._after_run_compaction._running()
                or lease.session_id != producer.session.session_id):
            return False
        session, agent = producer.session, producer.agent
        current = snapshot(agent, session.session_id)
        applying = [False]
        def alive() -> bool:
            return (ui._after_run_compaction.current is producer and ui._session is session and session.agent is agent
                and session.session_data.active_branch == producer.branch and session.model == producer.model
                and ui._worker_generation == producer.generation and not ui._exit_requested
                and ui._cancel_epoch == producer.cancel_epoch + int(applying[0])
                and not (applying[0] and (ui._command is None or ui._command.cancel_requested))
                and agent.tool_selection_revision == current['revision']
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)
        async def ask(name: str, projected: dict[str, Any]) -> Any:
            return await session.extension_dialogs.ask(name, projected) if alive() else False
        # The wrapper owns its own applying bit: another after-run action cannot
        # make this selection survive that action's source/cancel-epoch change.
        return await self.queue.reviewed(extension, dialog, producer=(producer, applying), ask=ask, current=current,
            alive=alive, settled=lambda: producer.settled,
            successful=lambda: producer.completed and producer.worker_success)

    def poll(self) -> None:
        ui = self.ui
        if ui._worker_active or ui._is_busy or ui._command is not None:
            return
        item = self.queue.take()
        if item is None:
            return
        if not isinstance(item.producer, tuple) or len(item.producer) != 2:
            return
        _producer, applying = item.producer
        applying[0] = True
        ui._handle_slash_command('active-tools', '', tools_target=(item.names, item.revision), tools_alive=item.alive)
