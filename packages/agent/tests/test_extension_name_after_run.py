from __future__ import annotations

import pytest
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.name_request import AfterRunNames, available


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
