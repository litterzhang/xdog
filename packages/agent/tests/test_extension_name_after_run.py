from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.name_request import AfterRunNames, available
from xdog.agent.extensions.runtime import ExtensionRuntime


@pytest.mark.parametrize('method,allowed', [('tools/call', True), ('xdog/pre', True), ('xdog/post', True),
    ('xdog/input', True), ('xdog/event', True), ('xdog/context_finish', True), ('xdog/command', False),
    ('xdog/provider_start', False), ('xdog/provider_auth', False), ('xdog/custom_frame', False)])
def test_after_run_name_is_named_and_not_an_ambient_host_capability(method, allowed):
    assert available(method, {}) is allowed
    assert not available('xdog/context_finish', {'operation': 'compaction'})
    assert not available('xdog/event', {'event': 'session_compact'})


@pytest.mark.parametrize('ending', ['success', 'callback-error', 'producer-error', 'withdraw', 'child'])
async def test_callback_success_is_not_producer_settlement_and_no_child_can_borrow_parent(ending):
    state = {'alive': True, 'settled': False, 'success': True}
    queue = AfterRunNames()
    lease = InvocationLease('local', 'session', 'child' if ending == 'child' else '', 'tools/call',
                            lambda: state['alive'], object())
    token = _current.set(lease)
    reviews = []
    async def ask(name, dialog):
        reviews.append(dialog)
        return True
    try:
        result = await queue.reviewed('local', {'kind': 'session', 'operation': 'rename', 'name_after_run': True,
            'name': 'AFTER-RUN-NAME', 'expected_name': ''}, producer=object(), ask=ask, alive=lambda: state['alive'],
            settled=lambda: state['settled'], successful=lambda: state['success'])
        assert result is (ending != 'child')
        assert queue.take() is None
        await lease.complete(ending != 'callback-error')
        assert queue.take() is None
        state.update(settled=True, success=ending != 'producer-error', alive=ending != 'withdraw')
        item = queue.take()
        assert (item is not None) is (ending == 'success')
        if item is not None:
            assert item.name == 'AFTER-RUN-NAME' and item.session_id == 'session'
        assert queue.take() is None
        assert bool(reviews) is (ending != 'child')
    finally:
        _current.reset(token)


@pytest.mark.parametrize('is_error', [False, True])
async def test_real_worker_tool_result_error_discards_original_name_proposal(tmp_path, is_error, monkeypatch):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/aftername', root / 'aftername')
    runtime = ExtensionRuntime(['aftername'], root, tmp_path, context=lambda: {'session_id': 'original'})
    queue, state = AfterRunNames(), {'settled': False}
    reviews = []
    async def ask(name, dialog):
        reviews.append(dialog)
        return True
    async def handler(name, dialog):
        return await queue.reviewed(name, dialog, producer=state, ask=ask, alive=lambda: True,
                                    settled=lambda: state['settled'], successful=lambda: True)
    runtime.name_request_handler = handler
    runtime.session_name = lambda: {'session_id': 'original', 'owner_id': 'original', 'name': ''}
    # Initialization deliberately contains extension failures. Preserve their actual
    # cause before disconnect removes the client, rather than failing at tools()[0].
    bootstrap_errors, disconnected = [], []
    original_load, original_disconnect = runtime._load, runtime._manager._disconnect

    async def traced_load(name):
        import traceback
        try:
            await original_load(name)
        except Exception:
            bootstrap_errors.append(traceback.format_exc())
            raise

    async def traced_disconnect(name):
        client = runtime._manager._clients.get(name)
        if client is not None:
            disconnected.append({'state': client.state, 'error': client.error,
                'stderr': b''.join(client.stderr_tail).decode('utf-8', errors='replace'),
                'returncode': client.process.returncode if client.process is not None else None})
        await original_disconnect(name)

    monkeypatch.setattr(runtime, '_load', traced_load)
    monkeypatch.setattr(runtime._manager, '_disconnect', traced_disconnect)
    try:
        await runtime.initialize()
        tools = runtime.tools()
        diagnostic = {'snapshot': runtime.snapshot(), 'clients': runtime._manager.status(),
                      'bootstrap_errors': bootstrap_errors, 'disconnected': disconnected}
        assert runtime.snapshot()['extensions'] == [{'name': 'aftername', 'state': 'active'}], diagnostic
        assert len(tools) == 1 and not bootstrap_errors and not disconnected, diagnostic
        client = runtime._manager._clients['aftername']
        assert client.state == 'connected' and client.error == '', diagnostic
        result = await tools[0].execute('call', {'error': is_error})
        assert result.is_error is is_error and len(reviews) == 1
        assert queue.take() is None
        state['settled'] = True
        assert (queue.take() is None) is is_error
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('method', ['pre', 'post', 'input', 'context', 'event'])
async def test_actual_general_callback_methods_stage_but_cannot_rename_before_settlement(tmp_path, method):
    from xdog.ai.types import UserMessage
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/aftername', root / 'aftername')
    runtime = ExtensionRuntime(['aftername'], root, tmp_path, settings={'aftername': {'method': method}},
                               context=lambda: {'session_id': 'original'})
    queue, state = AfterRunNames(), {'settled': False}
    reviews = []
    async def ask(name, dialog):
        reviews.append(dialog)
        return True
    async def handler(name, dialog):
        return await queue.reviewed(name, dialog, producer=state, ask=ask, alive=lambda: True,
                                    settled=lambda: state['settled'], successful=lambda: True)
    runtime.name_request_handler = handler
    runtime.session_name = lambda: {'session_id': 'original', 'owner_id': 'original', 'name': ''}
    try:
        await runtime.initialize()
        if method == 'pre':
            await runtime.pre('other', {})
        elif method == 'post':
            await runtime.post('other', {}, 'result')
        elif method == 'input':
            await runtime.input(UserMessage(content='hello'))
        elif method == 'context':
            await runtime.transform([UserMessage(content='hello')])
        else:
            await runtime.event('turn_end')
        assert len(reviews) == 1 and queue.take() is None
        state['settled'] = True
        assert queue.take().name == 'AFTER-RUN-NAME'
    finally:
        await runtime.aclose()
