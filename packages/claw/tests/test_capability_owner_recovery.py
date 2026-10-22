"""Restore installed group owners without warming a model or adding feature host policy."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.skills.goal.host import OperationContext


@pytest.mark.parametrize('endpoint_first', [False, True])
@pytest.mark.parametrize('state', ['active', 'paused', 'tasks', 'user', 'completed'])
async def test_restored_goal_owner_receives_generic_availability_without_parent_session(
    tmp_path, endpoint_first, state,
):
    stream = Mock(side_effect=AssertionError('Owner restoration must not execute a model'))
    config = ClawConfig(data_dir=str(tmp_path / 'data'))
    group = Group(id='main', workspace=str(tmp_path / 'workspace'))

    def application():
        app = Orchestrator(config, model='test/dummy', stream_fn=stream, defer_inputs=True)
        app.register_group(group)
        return app

    app = application()
    try:
        await app.start()
        binding = app._runtimes['main'].capabilities.get('goal')
        goal = await binding.service.create('Restored owner', 'Inspect persisted tasks', context=OperationContext(True))
        await binding.service.task({'id': 'inspect', 'title': 'Inspect output', 'expected_version': 0})
        if state == 'paused':
            await binding.service.change('pause', context=OperationContext(True), reason='User pause')
        elif state in {'tasks', 'user'}:
            await binding.service.change('wait', until=state, reason='Explicit wait',
                **({'question': 'Which result?', 'impact': 'Need a choice', 'resume_when': 'User decides'}
                   if state == 'user' else {}))
        elif state == 'completed':
            await binding.service.change('complete', summary='Already verified')
        original = await binding.service.current()
        namespace = binding.host.namespace
        assert app._runtimes['main']._active_session is None
    finally:
        await app.stop()

    app = application()
    server = None
    with tempfile.TemporaryDirectory(prefix='claw-owner-') as private:
        try:
            if endpoint_first:
                server = await ClawAcpServer.start(app, Path(private) / 'agent.sock')
            await app.start()
            if not endpoint_first:
                server = await ClawAcpServer.start(app, Path(private) / 'agent.sock')
            runtime = app._runtimes['main']
            binding = runtime.capabilities.get('goal')
            await binding.flush()
            assert binding.host.namespace == namespace
            assert await binding.service.current() == original
            assert runtime._active_session is None
            stream.assert_not_called()
            records = await (await app.input_service()).store.records(InputScope('main', runtime.workspace_dir))
            if state in {'active', 'tasks'}:
                assert binding.automation['status'] == 'watching'
                assert binding._observer is not None
                assert len(records) == 1  # Persisted task evidence, not a fabricated user turn.
                record, = records
                assert record.status == 'pending' and record.envelope.reply is None
                assert record.envelope.message.origin == 'acp'
                assert record.envelope.message.provenance['goal_id'] == goal.id
                # Repeated owner/endpoint hints cannot create a second reminder or owner.
                inputs = await app.input_service()
                inputs.publish_available(record.envelope.scope)
                await binding.flush()
                assert await inputs.store.records(record.envelope.scope) == records
            else:
                assert binding._observer is None and not records
            await server.close()
            server = None
            await binding.flush()
            assert binding._observer is None
            stream.assert_not_called()
        finally:
            if server is not None:
                await server.close()
            await app.stop()
