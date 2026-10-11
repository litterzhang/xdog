"""Tools-only after-run review, using the existing shared gateway producer observer."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.active_tools_request import AfterRunToolSelections
from xdog.claw.core.runtime.after_run_name import AfterRunNameConnection, RunName


class RunToolSelection(RunName):
    def __init__(self, connection: AfterRunToolSelectionConnection, group: str, run: str, producer: Any) -> None:
        super().__init__(connection, group, run, producer)
        self.queue: Any = AfterRunToolSelections(self.publish)

    def publish(self, event: dict[str, Any]) -> None:
        connection = self.connection
        async def send() -> None:
            if connection.alive():
                await connection.write({**event, 'group_id': self.group, 'run_id': self.run,
                    'active_tools_after_run_generation': self.generation})
        connection.loop.call_soon_threadsafe(lambda: connection.spawn(send()))

    def bind(self, session: Any) -> Callable[[str, dict[str, Any]], Awaitable[bool]] | None:
        self.session = session
        if not self.connection.enabled:
            return None
        runtime, agent = session._runtime, session.agent
        revision, source = runtime._source_revision, session._source_generation
        model, session_id = agent.state.model, session.meta.session_id
        selected: dict[str, Any] | None = None

        def alive() -> bool:
            connection = self.connection
            return (connection.enabled and connection.alive() and connection.generation == self.generation
                and self in connection.runs and runtime._active_session is session and session.agent is agent
                and agent.state.model == model and runtime.get_new_session_id() == session_id
                and runtime._source_revision == revision and session._source_generation in (source, source + 1)
                and not session.children.supervisor.active
                and (selected is None or agent.tool_selection_revision == selected['revision']
                    and [tool.name for tool in agent.state.tools] == selected['configured']))

        async def request(extension: str, dialog: dict[str, Any]) -> bool:
            nonlocal selected
            if self.task is None or self.task.done() or not alive():
                return False
            selected = snapshot(agent, session_id)
            async def ask(name: str, projected: dict[str, Any]) -> Any:
                return await session.extension_dialogs.ask(name, {**projected,
                    'active_tools_after_run_generation': self.generation}) if alive() else False
            return bool(await self.queue.reviewed(extension, dialog, producer=self, ask=ask, current=selected,
                alive=alive, settled=lambda: self.task is not None and self.task.done() and not agent.is_streaming
                    and (agent._producer_task is None or agent._producer_task.done()),
                successful=lambda: self.success and self.task is not None and not self.task.cancelled()
                    and self.task.exception() is None))
        return request

    async def settled(self) -> None:
        connection, gateway = self.connection, self.connection.gateway
        if self.session is None:
            return
        agent = self.session.agent
        if agent._producer_task is not None and not agent._producer_task.done():
            await asyncio.shield(asyncio.gather(agent._producer_task, return_exceptions=True))
        while gateway._group_runs.get(self.group) == self.run and connection.alive():
            await asyncio.sleep(0)  # Existing gateway reservation release is part of the producer barrier.
        item = self.queue.take()
        if item is None or gateway._orchestrator is None:
            return
        orchestrator = gateway._orchestrator
        async with gateway._group_runs_lock:
            if (self.group in gateway._group_runs or not item.alive()
                    or not orchestrator.try_reserve_group(self.group)):
                self.publish({'type': 'extension_active_tools_after_run', 'request_id': item.id,
                              'session_id': item.session_id, 'status': 'discarded'})
                return
            gateway._group_runs[self.group] = item.id
        connection.renaming[item.id] = asyncio.current_task(), self.group, item.session_id
        result = {'type': 'extension_active_tools_result', 'request_id': item.id, 'group_id': self.group,
                  'session_id': item.session_id, 'success': False}
        try:
            await self.session.select_active_tools(item.names, expected_revision=item.revision, alive=item.alive)
            result.update(success=True, active=[tool.name for tool in agent.active_tools],
                          revision=agent.tool_selection_revision)
        except asyncio.CancelledError:
            result.update(canceled=True, error='Selection canceled; inspect current selection; never replayed')
        except Exception:
            result['error'] = 'Selection failed or source changed; unchanged permissions; never replayed'
        finally:
            connection.renaming.pop(item.id, None)
            orchestrator.release_group_reservation(self.group)
            async with gateway._group_runs_lock:
                if gateway._group_runs.get(self.group) == item.id:
                    gateway._group_runs.pop(self.group, None)
        if connection.alive():
            await connection.write(result)
            self.publish({'type': 'extension_active_tools_after_run', 'request_id': item.id,
                          'session_id': item.session_id, 'status': 'applied' if result['success'] else 'failed'})


class AfterRunToolSelectionConnection(AfterRunNameConnection):
    """Reuse existing connection job cleanup and shared producer observation, not naming grants."""
    def negotiate(self, enabled: bool) -> None:
        self.enabled = enabled
        self.generation += 1
        for run in self.runs:
            run.queue.discard('capability-withdrawn')
            if run.session is not None:
                run.session.extension_dialogs.cancel_where(lambda dialog:
                    dialog.get('operation') == 'select_tools' and dialog.get('after_run') is True)
        for task, _, _ in tuple(self.renaming.values()):
            if task is not None:
                task.cancel()

    def create(self, group: str, run_id: str, producer: Any) -> RunToolSelection | None:
        if self.closed or not self.enabled:
            return None
        for run in self.runs:
            run.queue.discard('new-producer')
        run = RunToolSelection(self, group, run_id, producer)
        self.runs = [run]
        return run
