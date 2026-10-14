"""Selection proposals reuse the actual producer barrier; no midbatch mutation or child borrowing."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.active_tools_request import AfterRunToolSelections, available
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.tool_inventory import capture
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


def dialog(**changes):
    return {'id': 'proposal', 'kind': 'session', 'operation': 'select_tools', 'title': 'Review exact tools',
            'after_run': True, 'names': ['safe'], 'revision': 0, **changes}


def current():
    return {'session_id': 'original', 'revision': 0, 'configured': ['safe', 'other'], 'active': ['safe', 'other']}


def setup(tmp_path, **settings):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/aftertools', root / 'aftertools')
    return ExtensionRuntime(['aftertools'], root, tmp_path, settings={'aftertools': settings},
                            context=lambda: {'session_id': 'original'})


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('method,allowed', [('tools/call', True), ('xdog/pre', True), ('xdog/post', True),
    ('xdog/input', True), ('xdog/event', True), ('xdog/context_finish', True), ('xdog/command', False),
    ('xdog/provider_start', False), ('xdog/provider_auth', False), ('xdog/terminal_input', False),
    ('xdog/custom_frame', False)])
def test_named_selection_callbacks_do_not_gain_other_surfaces(method, allowed):
    assert available(method, {}) is allowed
    assert not available('xdog/context_finish', {'operation': 'compaction'})


def test_after_run_schema_rejects_forged_types_and_scope_fields() -> None:

    def _check_case(changes):
        with pytest.raises(ValueError):
            validate_dialog(dialog(**changes))

    for changes in [
        {"after_run": False},
        {"after_run": 1},
        {"names": ["safe", "safe"]},
        {"revision": True},
        {"target": {}},
        {"session_id": "foreign"},
        {"tool_selection_run": {"id": "x", "session_id": "original", "method": []}},
        {"active_tools_after_run_generation": True},
    ]:
        _check_case(changes)


@pytest.mark.parametrize('ending', ['success', 'denied', 'callback-failed', 'producer-failed', 'revoked',
                                    'child', 'changed-revision', 'dropped-name', 'forged-current'])
async def test_review_callback_and_real_producer_settlement_are_separate_once_only_barriers(ending):
    queue, reviews = AfterRunToolSelections(), []
    state = {'alive': True, 'settled': False, 'success': True}
    lease = InvocationLease('aftertools', 'original', 'child' if ending == 'child' else '', 'tools/call',
                            lambda: state['alive'], object())
    token = _current.set(lease)
    proposal = dialog()
    if ending == 'changed-revision':
        proposal['revision'] = 1
    if ending == 'dropped-name':
        proposal['names'] = ['removed']
    if ending == 'forged-current':
        proposal['current_tools'] = current()
    async def ask(extension, projected):
        assert validate_dialog(projected) == projected
        assert projected['current_tools'] == current()
        reviews.append(projected)
        return ending != 'denied'
    try:
        if ending == 'forged-current':
            with pytest.raises(ValueError):
                await queue.reviewed('aftertools', proposal, producer=state, ask=ask, current=current(),
                    alive=lambda: state['alive'], settled=lambda: state['settled'], successful=lambda: state['success'])
            return
        result = await queue.reviewed('aftertools', proposal, producer=state, ask=ask, current=current(),
            alive=lambda: state['alive'], settled=lambda: state['settled'], successful=lambda: state['success'])
        assert result is (ending not in {'denied', 'child', 'changed-revision', 'dropped-name'})
        assert queue.take() is None
        await lease.complete(ending != 'callback-failed')
        assert queue.take() is None  # Successful callback is NOT an idle producer.
        state.update(settled=True, alive=ending != 'revoked', success=ending != 'producer-failed')
        value = queue.take()
        assert (value is not None) is (ending == 'success')
        if value is not None:
            assert value.names == ('safe',) and value.revision == 0
        assert queue.take() is None
    finally:
        _current.reset(token)


async def test_get_active_tools_reuses_captured_original_names_without_a_new_host_dialog():
    api = ExtensionAPI({})
    assert await api.get_active_tools() is None
    safe = AgentTool(name='safe', description='safe', parameters={})
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()),
                  config=AgentConfig(model='fixture'), tools=[safe])
    token = api._tool_inventory.set(capture(agent, 'original', 'child-owner'))
    try:
        value = await api.get_active_tools()
        assert value == snapshot(agent, 'original')
        value['active'].clear()
        assert (await api.get_active_tools())['active'] == ['safe']
        assert not await api.set_active_tools([], value['revision'])
    finally:
        api._tool_inventory.reset(token)
