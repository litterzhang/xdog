"""ACP must expose the application's actual journal, not a second adapter receipt."""
from __future__ import annotations

import asyncio

from xdog.agent.acp.contracts import Peer
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.types import UserInput

from packages.claw.tests.test_acp_backend import ScriptedModel, enqueue_params, make_backend


async def test_acp_namespace_is_the_actual_application_scope_namespace(tmp_path):
    orch, backend, model = await make_backend(tmp_path)
    try:
        service = await orch.input_service()
        scope = InputScope('main', tmp_path)
        status = await backend.extension(Peer('reader', None), 'xdog/inbox_status',
            {'session_id': 'group:main', 'cwd': str(tmp_path)})
        assert status['inbox_id'] == await service.store.namespace(scope)
        assert model.inputs == [] and orch._runtimes['main']._active_session is None
    finally:
        await backend.close()
        await orch.stop()


async def test_acp_status_can_inspect_a_native_input_without_creating_another_turn(tmp_path):
    orch, backend, model = await make_backend(tmp_path)
    try:
        await orch.route_message(UserInput(group_id='main', content='native input'), message_id='native-input')
        service = await orch.input_service()
        receipt = await service.store.get(InputScope('main', tmp_path), 'native-input')
        status = await backend.extension(Peer('reader', None), 'xdog/inbox_status',
            {'session_id': 'group:main', 'cwd': str(tmp_path), 'message_id': 'native-input'})
        assert len(status['messages']) == 1
        assert status['messages'][0]['status'] == receipt.status == 'succeeded'
        assert status['messages'][0]['attempt_id'] == receipt.attempt_id
        assert status['messages'][0]['session_id'] == receipt.session_id
        assert len(model.inputs) == 1
    finally:
        await backend.close()
        await orch.stop()


async def test_reopened_endpoint_does_not_mislabel_continued_application_execution(tmp_path):
    from xdog.claw.acp.backend import ClawBackend
    model = ScriptedModel()
    model.release.clear()
    orch, backend, _ = await make_backend(tmp_path, model)
    try:
        await backend.extension(Peer('sender', None), 'xdog/enqueue', enqueue_params(tmp_path))
        await asyncio.wait_for(model.entered.wait(), 3)
        service = await orch.input_service()
        scope = InputScope('main', tmp_path)
        await backend.close()
        backend = await ClawBackend.open(
                            orch,
                        )
        status = await backend.extension(Peer('reader', None), 'xdog/inbox_status',
            {'session_id': 'group:main', 'cwd': str(tmp_path), 'message_id': 'event-1'})
        actual = await service.store.get(scope, 'event-1')
        assert status['messages'][0]['status'] == actual.status == 'running'
        assert status['messages'][0]['attempt_id'] == actual.attempt_id
        model.release.set()
        result = await asyncio.wait_for(service.wait(scope, 'event-1'), 3)
        assert result.status == 'succeeded' and len(model.inputs) == 1
    finally:
        model.release.set()
        await backend.close()
        await orch.stop()
