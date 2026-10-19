"""Bounded synthetic model + manual Eyes clocks on the real Claw ACP owner."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, GroupConfig, QueueMode
from xdog.eyes.managed_scheduler import SchedulerClocks

NOW = datetime.fromisoformat('2026-10-12T09:20:00+08:00').timestamp()


def scheduler(**schedule):
    return {'type': 'scheduler', 'schedule': schedule}


def at(seconds=240):
    return scheduler(at=datetime.fromtimestamp(NOW + seconds, UTC).isoformat())


class ManualClock:
    """One shared wall/monotonic clock; no polling deadline advances itself."""
    def __init__(self):
        self.now = NOW
        self.mono = 100.0
        self._tick = asyncio.Event()

    def wall(self):
        return self.now

    def monotonic(self):
        return self.mono

    async def sleep(self, seconds):
        await self._tick.wait()

    def pulse(self):
        event, self._tick = self._tick, asyncio.Event()
        event.set()

    def advance(self, seconds):
        self.now += seconds
        self.mono += seconds
        self.pulse()

    def clocks(self):
        return SchedulerClocks(wall=self.wall, monotonic=self.monotonic, sleep=self.sleep)


class ScriptedModel:
    def __init__(self):
        self.calls = []
        self.contexts = []
        self.gates = {}
        self.replies = {}
        self.active = 0
        self.peak = 0

    def __call__(self, model, context, options=None):
        self.contexts.append(context)
        content = context.messages[-1].content
        prompt = content if isinstance(content, str) else ''.join(
            part.text for part in content if isinstance(part, TextContent))
        if '\nHandling instruction:\n' in prompt:
            prompt = prompt.split('\nHandling instruction:\n', 1)[1].split('\nUntrusted evidence (', 1)[0]
        self.calls.append(prompt)
        message = self.replies.get(prompt, AssistantMessage(content=(TextContent(text='Scheduled report'),)))

        async def events():
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if prompt in self.gates:
                    entered, release = self.gates[prompt]
                    entered.set()
                    waits = [asyncio.create_task(release.wait())]
                    if options is not None and options.cancel is not None:
                        waits.append(asyncio.create_task(options.cancel.wait()))
                    try:
                        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in waits:
                            task.cancel()
                        await asyncio.gather(*waits, return_exceptions=True)
                yield StartEvent(partial=message)
                yield DoneEvent(message=message)
            finally:
                self.active -= 1

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


@dataclass
class Harness:
    orch: Orchestrator
    clock: ManualClock
    model: ScriptedModel
    channel: TuiChannel
    server: ClawAcpServer
    manager: ManagedObservers
    endpoint: Path
    inbox_path: Path
    serial: int = 0
    users: list = field(default_factory=list)

    async def until(self, predicate):
        """Bounded condition synchronization, never stress/retry whole scenarios."""
        async with asyncio.timeout(4):
            while True:
                result = await predicate()
                if result:
                    return result
                self.clock.pulse()
                await asyncio.sleep(.001)

    async def flush(self):
        # Give descriptor/SQLite workers and transport callbacks a bounded chance
        # to observe the *unchanged* test clock. Positive assertions use until.
        for _ in range(5):
            self.clock.pulse()
            await asyncio.sleep(.002)

    async def create(self, instruction='report', group='g1', source=None, name=None):
        self.serial += 1
        return await self.manager.create(group, name or f'observer-{self.serial}', source or at(), instruction)

    async def tool(self, action, group='g1', **arguments):
        from xdog.claw.core.tools.tool_eyes import create_eyes_tool
        ctx = self.orch._runtimes[group].get_or_create_session().agent.tool_context
        assert ctx['_eyes'] is self.manager
        result = await create_eyes_tool().execute('test-call', {'action': action, **arguments}, ctx=ctx)
        return result.content[0].text

    async def record(self, identifier, group='g1'):
        return await self.manager.store.get(group, self.orch.get_group_workspace(group), identifier)

    async def completed(self, identifier, count=1, group='g1'):
        async def check():
            status = await self.manager.status(group, identifier)
            history = status['occurrences']
            if len(history) != count or any(
                row['execution_status'] not in {'succeeded', 'failed', 'interrupted', 'cancelled'}
                or row['delivery_status'] not in {'delivered', 'failed', 'unknown', 'not-required'} for row in history
            ):
                return None
            record = await self.record(identifier, group)
            if record.state.get('pending') is not None:
                return None
            if 'at' in status['source'].get('schedule', {}) and (
                status['enabled'] or status['state'] == 'running'
            ):
                return None
            return status
        return await self.until(check)

    def attach(self):
        self.orch.set_observers(self.manager)

    async def replace_manager(self):
        await self.manager.close()
        self.manager = ManagedObservers(self.orch, self.endpoint, clocks=self.clock.clocks())
        self.attach()
        return self.manager

    async def restart(self, *, application=False):
        await self.manager.close()
        await self.server.close()
        if application:
            previous = self.orch
            groups = [runtime.group for runtime in previous._runtimes.values()]
            await previous.stop()
            self.orch = Orchestrator(previous._config, stream_fn=self.model, data_dir=previous._data_dir)
            for group in groups:
                self.orch.register_group(group)
            self.orch.add_channel(self.channel)
        self.server = await ClawAcpServer.start(
                                self.orch,
                                self.endpoint,
                            )
        self.manager = ManagedObservers(self.orch, self.endpoint, clocks=self.clock.clocks())
        self.attach()
        await self.manager.start()

    async def close(self):
        for _, release in self.model.gates.values():
            release.set()
        self.clock.pulse()
        await self.manager.close()
        await self.server.close()
        if self.users:
            await asyncio.gather(*self.users, return_exceptions=True)
        await self.orch.stop()


@pytest.fixture
async def managed(tmp_path, monkeypatch):
    clock, model = ManualClock(), ScriptedModel()

    def create(group, data_dir, **kwargs):
        return GroupRuntime(group=group, data_dir=data_dir, model='test/dummy', stream_fn=model,
                            observers=kwargs['observers'], send_fn=kwargs['send_fn'])

    monkeypatch.setattr(GroupRuntime, 'create', create)
    config = ClawConfig(data_dir=str(tmp_path / 'data'), eyes_enabled=True,
                        enabled_tools=('eyes',), max_concurrent_agents=2)
    orch = Orchestrator(config, stream_fn=model)
    for gid in ('g1', 'g2', 'g3'):
        orch.register_group(Group(id=gid, workspace=str(tmp_path / gid),
                                  config=GroupConfig(debounce_ms=0, queue_mode=QueueMode.STEER)))
    channel = TuiChannel()
    channel.send_message = AsyncMock()
    orch.add_channel(channel)
    # pytest's long node names must not leak into the Unix socket's 107-byte limit.
    with tempfile.TemporaryDirectory(prefix='xe-', dir='/tmp') as private:
        endpoint = Path(private) / 'a.sock'
        inbox = tmp_path / 'private' / 'inbox.sqlite3'
        server = await ClawAcpServer.start(
                           orch,
                           endpoint,
                       )
        manager = ManagedObservers(orch, endpoint, clocks=clock.clocks())
        harness = Harness(orch, clock, model, channel, server, manager, endpoint, inbox)
        harness.attach()
        try:
            await manager.start()
            yield harness
        finally:
            async with asyncio.timeout(5):
                await harness.close()

