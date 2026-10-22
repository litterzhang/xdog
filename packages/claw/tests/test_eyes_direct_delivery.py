"""Daemon → ordinary ACP, with no host Eyes runtime or captured reply route."""
from __future__ import annotations

import asyncio
import json

from xdog.agent.capabilities.runtime import ApplicationPort
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.eyes._daemon_control import DaemonControl

from test_support.eyes_definition import definition


async def test_tool_can_target_another_group_workspace_and_delivery_does_not_wait_for_execution(
    tmp_path, isolated_eyes_daemon,
):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    def model(*args):
        calls.append(args)
        message = AssistantMessage(content=(TextContent(text='normal automatic response'),))
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)

        async def events():
            entered.set()
            await release.wait()
            yield DoneEvent(message=message)
        return EventStream.from_async_generator(events(), result_future=future)

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data'), enabled_tools=('eyes',)),
                       model='test/dummy', stream_fn=model, defer_inputs=True)
    for key in ('sender', 'receiver'):
        app.register_group(Group(id=key, workspace=str(tmp_path / key)))
    server = await ClawAcpServer.start(app, isolated_eyes_daemon.parent / 'input.sock')
    control = None
    try:
        await app.application_capabilities.start(ApplicationPort())
        inputs = await app.input_service()
        inputs.resume()
        tool = next(item for item in app.application_capabilities.tools if item.name == 'eyes')

        async def invoke(action, **fields):
            result = await tool.execute('call', {'action': action, 'owner': 'chosen-by-caller',
                **fields}, ctx={'group_id': 'sender'})
            assert not result.is_error
            envelope = json.loads(''.join(item.text for item in result.content))
            assert envelope['exit_code'] == 0, envelope
            return json.loads(envelope['stdout'])

        workspace = app.get_group_workspace('sender')
        target = app.get_group_workspace('receiver')
        data = definition(server.path, 'group:receiver', 'cross-group', {
            'type': 'file', 'root': '.', 'include': ['*.txt'], 'poll_interval_ms': 50}, 'Inspect changed files')
        data['detectors'][0].update(debounce_ms=50, max_batch_delay_ms=100)
        path = workspace / 'eyes.json'
        path.write_text(json.dumps(data))
        created = await invoke('create', definition_path=str(path))
        control = DaemonControl(owner='chosen-by-caller')
        await control.wait_ready(created['registration_id'], timeout=5)
        await app.application_capabilities.aclose()  # Remove the entire registered tool binding.
        (workspace / 'first.txt').write_text('one')
        await asyncio.wait_for(entered.wait(), 8)
        (workspace / 'second.txt').write_text('two')
        async with asyncio.timeout(8):
            while len(await inputs.store.records(InputScope('receiver', target))) < 2:
                await asyncio.sleep(.025)
        rows = await inputs.store.records(InputScope('receiver', target))
        assert len(calls) == 1 and not release.is_set()  # First execution is still held.
        assert len(rows) == 2  # Yet the daemon has already delivered the next event.
        assert all(row.envelope.reply is None and row.envelope.message.origin == 'acp' for row in rows)
        assert not await inputs.store.records(InputScope('sender', workspace))
        assert await (await app.output_service()).store.records() == []
        assert not (tmp_path / 'data/observers').exists()
        assert not hasattr(app, 'register_output_source')
        await control.cancel(created['registration_id'])
    finally:
        release.set()
        if control is not None:
            await control.aclose()
        await server.close()
        await app.stop()
