"""Connection-owned reviewed naming; execute only after the original run task exits."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.extensions.name_request import AfterRunNames


class RunName:
    def __init__(self, connection: AfterRunNameConnection, group: str, run: str, producer: Any) -> None:
        self.connection, self.group, self.run = connection, group, run
        self.generation = connection.generation
        self.producer = producer
        self.session: Any = None
        self.queue = AfterRunNames(self.publish)

    @property
    def task(self) -> asyncio.Task[Any] | None:
        task = self.producer.task  # Shared gateway producer observer, not a compaction grant.
        return task if isinstance(task, asyncio.Task) else None

    @property
    def success(self) -> bool:
        return self.producer.success is True

    def publish(self, event: dict[str, Any]) -> None:
        connection = self.connection
        async def send() -> None:
            if connection.alive():
                await connection.write({**event, 'group_id': self.group, 'run_id': self.run,
                    'name_after_run_generation': self.generation})
        connection.loop.call_soon_threadsafe(lambda: connection.spawn(send()))

    def bind(self, session: Any) -> Callable[[str, dict[str, Any]], Awaitable[bool]] | None:
        self.session = session
        if not self.connection.enabled:
            return None  # Observation is independent; compaction authority remains default-off.
        runtime, agent = session._runtime, session.agent
        revision, source = runtime._source_revision, session._source_generation
        model, session_id = agent.state.model, session.meta.session_id
        tool_revision: int | None = None

        def alive() -> bool:
            connection = self.connection
            return (connection.enabled and connection.alive() and connection.generation == self.generation
                and runtime._active_session is session and session.agent is agent and agent.state.model == model
                and runtime.get_new_session_id() == session_id and runtime._source_revision == revision
                and session._source_generation in (source, source + 1)
                and (tool_revision is None or agent.tool_selection_revision == tool_revision)
                and not session.children.supervisor.active)

        async def request(extension: str, dialog: dict[str, Any]) -> bool:
            nonlocal tool_revision
            tool_revision = agent.tool_selection_revision if tool_revision is None else tool_revision
            if self.task is None or self.task.done():
                return False
            async def ask(name: str, projected: dict[str, Any]) -> Any:
                return await session.extension_dialogs.ask(name, {**projected,
                    'name_after_run_generation': self.generation}) if alive() else False
            return await self.queue.reviewed(extension, dialog, producer=self, ask=ask, alive=alive,
                settled=lambda: self.task is not None and self.task.done() and not agent.is_streaming
                    and (agent._producer_task is None or agent._producer_task.done()),
                successful=lambda: self.success and self.task is not None and not self.task.cancelled()
                    and self.task.exception() is None)
        return request

    async def settled(self) -> None:
        connection, gateway = self.connection, self.connection.gateway
        if self.session is None:
            return
        agent = self.session.agent
        if agent._producer_task is not None and not agent._producer_task.done():
            await asyncio.shield(asyncio.gather(agent._producer_task, return_exceptions=True))
        # The ordinary gateway run callback owns release of its reservation/map.
        while gateway._group_runs.get(self.group) == self.run and connection.alive():
            await asyncio.sleep(0)
        item = self.queue.take()
        if item is None or gateway._orchestrator is None:
            return
        orchestrator = gateway._orchestrator
        async with gateway._group_runs_lock:
            if (self.group in gateway._group_runs or not item.alive()
                    or not orchestrator.try_reserve_group(self.group)):
                self.publish({'type': 'extension_name_after_run', 'request_id': item.id,
                              'session_id': item.session_id, 'status': 'discarded'})
                return
            gateway._group_runs[self.group] = item.id
        connection.renaming[item.id] = asyncio.current_task(), self.group, item.session_id
        result = {'type': 'extension_name_result', 'request_id': item.id, 'group_id': self.group,
                  'session_id': item.session_id, 'success': False}
        try:
            await connection.write({'type': 'extension_name_after_run', 'status': 'started',
                'request_id': item.id, 'group_id': self.group, 'session_id': item.session_id,
                'run_id': self.run, 'name_after_run_generation': self.generation})
            selected = await orchestrator.rename_session(self.group, item.session_id, item.name,
                expected_name=item.expected_name, alive=item.alive)
            result.update(success=True, name=selected)
        except asyncio.CancelledError:
            result.update(canceled=True, error='Reviewed naming canceled; inspect context, no replay')
        except Exception:
            result['error'] = 'Reviewed naming failed/source changed; no replay'
        finally:
            connection.renaming.pop(item.id, None)
            orchestrator.release_group_reservation(self.group)
            async with gateway._group_runs_lock:
                if gateway._group_runs.get(self.group) == item.id:
                    gateway._group_runs.pop(self.group, None)
        if connection.alive():
            await connection.write(result)
            self.publish({'type': 'extension_name_after_run', 'request_id': item.id, 'session_id': item.session_id,
                          'status': 'applied' if result['success'] else 'failed'})


class AfterRunNameConnection:
    def __init__(self, gateway: Any, write: Callable[[dict[str, Any]], Awaitable[None]],
                 alive: Callable[[], bool]) -> None:
        self.gateway, self.write, self.alive = gateway, write, alive
        self.loop = asyncio.get_running_loop()
        self.enabled, self.generation = False, 0
        self.closed = False
        self.closed = False
        self.runs: list[RunName] = []
        self.jobs: set[asyncio.Task[None]] = set()
        self.renaming: dict[str, tuple[asyncio.Task[Any] | None, str, str]] = {}

    def spawn(self, coroutine: Any) -> None:
        if self.closed:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        def done(work: asyncio.Task[None]) -> None:
            self.jobs.discard(work)
            if not work.cancelled():
                work.exception()
        task.add_done_callback(done)

    def negotiate(self, enabled: bool) -> None:
        self.enabled = enabled
        self.generation += 1
        for run in self.runs:
            run.queue.discard('capability-withdrawn')
            if run.session is not None:
                run.session.extension_dialogs.cancel_where(lambda dialog: dialog.get('name_after_run') is True)
        for task, _, _ in tuple(self.renaming.values()):
            if task is not None:
                task.cancel()

    def create(self, group: str, run_id: str, producer: Any) -> RunName | None:
        if self.closed:
            return None
        for run in self.runs:
            run.queue.discard('new-producer')
        run = RunName(self, group, run_id, producer)
        self.runs = [run]
        return run

    def attach(self, run: RunName | None, task: asyncio.Task[Any]) -> None:
        if run is not None:
            task.add_done_callback(lambda _: self.spawn(run.settled()))

    def cancel(self, packet: dict[str, Any]) -> bool:
        identifier = packet.get('request_id')
        if not isinstance(identifier, str):
            return False
        item = self.renaming.get(identifier)
        if item is None or packet.get('group_id') != item[1] or packet.get('session_id') != item[2]:
            return False
        if item[0] is not None:
            item[0].cancel()
        return True

    async def close(self) -> None:
        self.closed = True
        self.negotiate(False)
        for task in tuple(self.jobs):
            task.cancel()
        await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
