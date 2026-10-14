from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_rpc_extensions import close, session


def review(panel):
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))  # Unpainted confirmation must not apply.
    for _ in range(200):
        panel.render(90)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('kind', ['rpc', 'claw'])
async def test_compaction_capability_revoke_does_not_cancel_other_after_run_reviews(tmp_path, kind):
    from types import SimpleNamespace

    from xdog.agent.extensions.dialogs import DialogBroker
    from xdog.claw.core.runtime.after_run_compaction import AfterRunCompactionConnection
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    pending = [asyncio.create_task(broker.ask('local', {
        'id': 'unused', 'kind': 'session', 'operation': 'rename', 'title': 'Rename',
        'name': 'new name', 'expected_name': '', 'after_run': True})),
        asyncio.create_task(broker.ask('local', {
        'id': 'unused', 'kind': 'session', 'operation': 'select_tools', 'title': 'Select tools',
        'names': [], 'revision': 0, 'after_run': True}))]
    async def send(event):
        return None
    controller = None
    owner = None
    try:
        await asyncio.sleep(0)
        assert len(broker._pending) == 2
        if kind == 'rpc':
            owner, _ = session(tmp_path)
            control = RpcExtensions(owner, lambda event: None)
            owner.extension_dialogs = broker
            control.after_run_compaction.negotiate(False)
            controller = control
        else:
            controller = AfterRunCompactionConnection(SimpleNamespace(), send, lambda: True)
            marker = controller.create('group', 'run')
            assert marker is not None  # Read-only observation does not require a compact grant.
            marker.session = SimpleNamespace(extension_dialogs=broker)
            controller.negotiate(False)
        await asyncio.sleep(0)
        assert all(not task.done() for task in pending) and len(broker._pending) == 2
    finally:
        broker.cancel_all()
        await asyncio.gather(*pending, return_exceptions=True)
        if controller is not None:
            await (controller.aclose() if kind == 'rpc' else controller.close())
        if owner is not None:
            await close(owner)
