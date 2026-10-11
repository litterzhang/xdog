"""Real worker declarative UI contributions: opt-in, scoped, bounded, no model input."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, packet, session


@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_worker_status_widget_notice_and_clear_settle_before_control_receipt(tmp_path, enabled):
    owner, calls = session(tmp_path, extra=('sample', 'uiclear'))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_contributions': int(enabled)})
        for key, command in (('show', 'ext-uiclear-show'), ('notice', 'ext-sample-hello'),
                             ('hide', 'ext-uiclear-hide')):
            controller.start(packet(owner, key, command), turn_running=False)
            await controller.task
            await asyncio.sleep(0)
        records = [event for event in events if event['type'] == 'extension_contribution']
        if enabled:
            assert records
            assert 'WIDGET-LIVE' in json.dumps(records) and 'EXTENSION-COMMAND-NOTICE' in json.dumps(records)
            shown = [event for event in records if event['request_id'] == 'show']
            hidden = [event for event in records if event['request_id'] == 'hide']
            assert shown[-1]['data']['ui']['uiclear:details']['kind'] == 'widget'
            assert 'uiclear:details' not in hidden[-1]['data']['ui']
            assert all(event['retained'] is False for event in records)
            assert [event['sequence'] for event in records] == list(range(1, len(records) + 1))
            for record in records:
                index = events.index(record)
                receipt = next(i for i, event in enumerate(events) if event['type'] == 'extension_result'
                               and event['request_id'] == record['request_id'])
                assert index < receipt
        else:
            assert not records
        assert not owner.messages and owner.permissions.mode == 'deny' and not calls
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('change', ['withdraw', 'reenable', 'session', 'close'])
async def test_queued_contributions_refuse_late_scope_or_generation_without_clearing_dialog_grants(tmp_path, change):
    owner, _ = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_dialogs': 1, 'extension_forms': 1})
        controller.negotiate({'extension_contributions': 1})
        assert controller.dialogs_enabled and controller.forms_enabled
        async with controller.bind('owned'):
            controller._publish({'type': 'extension_notice', 'text': 'token=PRIVATE',
                                 'snapshot': {'ui': {'safe': {'lines': ['PUBLIC']}}}})
            if change == 'withdraw':
                controller.negotiate({'extension_contributions': 0})
            elif change == 'reenable':
                controller.negotiate({'extension_contributions': 0})
                controller.negotiate({'extension_contributions': 1})
            elif change == 'session':
                owner.session_data.session_id = 'changed'
            else:
                controller.closed = True
            await asyncio.sleep(0)
        assert not any(event['type'] == 'extension_contribution' for event in events)
        assert not any(event['type'] in {'extension_notice', 'extension_ui'} for event in events)
    finally:
        await controller.aclose()
        await close(owner)


async def test_contribution_queue_byte_bounds_redaction_and_exact_negotiation(tmp_path):
    owner, _ = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        for value in (True, False, None, '1', 2):
            with pytest.raises(ValueError):
                controller.negotiate({'extension_contributions': value})
        controller.negotiate({'extension_contributions': 1})
        async with controller.bind('owned'):
            for _ in range(100):
                controller._publish({'type': 'extension_notice', 'text': 'x' * 40000, 'snapshot': {'ui': {}}})
            assert controller._contributions_pending == 32
            await asyncio.sleep(0)
            controller._publish({'type': 'extension_notice', 'text': 'token=PRIVATE',
                                 'snapshot': {'ui': {'secret': 'PRIVATE'}}})
        records = [event for event in events if event['type'] == 'extension_contribution']
        assert len(records) == 33 and any(event.get('limited') for event in records)
        assert 'PRIVATE' not in json.dumps(records)
        assert all(len(json.dumps(event).encode()) < 64 * 1024 for event in records)
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_worker_contributions_are_review_data_not_model_input(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/sample', root / 'sample')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['sample'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_contributions': 1})
        caps = peer.read('extension_contribution_capabilities')
        assert caps['extension_contributions'] == 1 and caps['replay'] is False
        peer.send({'type': 'extension_control', 'request_id': 'command', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-sample-hello', 'args': ''})
        result = peer.read('extension_result')
        assert result['success']
        records = [event for event in peer.events if event['type'] == 'extension_contribution']
        assert records and 'EXTENSION-COMMAND-NOTICE' in json.dumps(records)
        assert all(event['generation'] == caps['generation'] and event['request_id'] == 'command'
                   and event['retained'] is False for event in records)
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny'
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()
