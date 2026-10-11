"""A completed approval still invalidates an earlier terminal editing operation."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.terminal import RpcTerminal

from packages.coding.tests.test_rpc_child_terminal import started_terminal, terminal_packet
from packages.coding.tests.test_rpc_children import close as close_child
from packages.coding.tests.test_rpc_children import setup, wait
from packages.coding.tests.test_rpc_extensions import close as close_parent
from packages.coding.tests.test_rpc_extensions import session
from packages.coding.tests.test_rpc_parent_terminal import request


@pytest.mark.parametrize('child', [False, True], ids=['parent-hook', 'child-hook'])
@pytest.mark.parametrize('approver', ['parent', 'child'])
async def test_approval_opened_and_closed_during_actual_hook_invalidates_old_edit(tmp_path, child, approver):
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, extension='terminal')
    else:
        owner, calls = session(tmp_path, extra=('terminal',))
        events = []
        controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    entered, release = asyncio.Event(), asyncio.Event()
    approving = None
    try:
        if child:
            identifier = await started_terminal(controller, events)
            controller.negotiate({'child_terminal_input': 1})
            runtime = owner.children.extensions.owners[identifier]
        else:
            await owner.initialize_extensions()
            controller.negotiate({'terminal_input': 1})
            runtime = owner.extensions
        if approver == 'child':
            from xdog.coding.core.permissions import PermissionManager
            if not child:
                owner.children.permissions['test-owner'] = PermissionManager('ask')
            manager = next(iter(owner.children.permissions.values()))
        else:
            manager = owner.permissions
        manager.set_mode('ask')
        manager.set_request_handler(lambda approval: None)
        original = runtime.terminal_input
        async def held(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        runtime.terminal_input = held
        if child:
            controller.terminal_input(terminal_packet(controller, identifier))
            task = controller.jobs['terminal'][0]
        else:
            controller.start(request(controller))
            task = controller.task
        async with asyncio.timeout(5):
            await entered.wait()
        revision = manager.admission_revision
        call = ToolCall(id='approval', name='filesystem', arguments={'action': 'write', 'path': 'target'})
        approving = asyncio.create_task(manager.before_tool_call(BeforeToolCallContext(
            assistant_message=AssistantMessage(), tool_call=call, args=call.arguments, context=AgentContext())))
        await wait(lambda: manager.has_pending_requests)
        assert manager.admission_revision == revision + 1
        manager.deny_all()
        assert await approving is not None
        assert not manager.has_pending_requests  # Current pending-state check alone would miss this ownership change.
        release.set()
        await task
        kind = 'child_terminal_result' if child else 'terminal_result'
        result = next(event for event in events if event['type'] == kind)
        assert not result['success'] and 'data' not in result and 'consume' not in result
        assert await runtime.command('ext-terminal-count', '') == '1'
        assert not owner.messages and not (tmp_path / 'target').exists()
        if not child:
            assert not calls
    finally:
        release.set()
        owner.permissions.deny_all()
        for manager in owner.children.permissions.values():
            manager.deny_all()
        if approving is not None:
            await approving
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)
