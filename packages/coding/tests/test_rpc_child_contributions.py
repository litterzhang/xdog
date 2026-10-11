"""Child declarative worker contributions never borrow parent contribution authority."""
from __future__ import annotations

import asyncio
import json

import pytest

from packages.coding.tests.test_rpc_children import close, control, setup, wait


@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_child_worker_bootstrap_and_turn_notice_are_independently_scoped(tmp_path, enabled):
    session, _, nav, sources, children, events = setup(tmp_path, extension='sample')
    try:
        children.negotiate({'child_contributions': int(enabled)})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: bool(session.children.supervisor.capture_source(child).messages))
        if enabled:
            await wait(lambda: any(event['type'] == 'child_contribution' for event in events))
            records = [event for event in events if event['type'] == 'child_contribution']
            assert 'EXTENSION-TURN-START' in json.dumps(records)
            assert 'EXTENSION-WIDGET' in json.dumps(records)
            assert all(event['child_id'] == child and event['child_owner'] == children.lease
                       and event['generation'] == children.contribution_generation
                       and event['retained'] is False for event in records)
        else:
            assert not any(event['type'] == 'child_contribution' for event in events)
        assert not session.messages and session.permissions.mode == 'deny'
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('change', ['withdraw', 'reenable', 'revision', 'controls', 'child-close'])
async def test_queued_child_contributions_refuse_changed_generation_owner_and_closed_child(tmp_path, change):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        children.negotiate({'child_contributions': 1})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        generation, owner, binding = children.contribution_generation, children.bound, children.binding
        children._contribution_pending += 1
        if change == 'withdraw':
            children.negotiate({'child_contributions': 0})
        elif change == 'reenable':
            children.negotiate({'child_contributions': 0})
            children.negotiate({'child_contributions': 1})
        elif change == 'revision':
            nav.refresh_revision()
        elif change == 'controls':
            children.negotiate({'child_controls': 0})
        else:
            await control(children, 'cancel', 'cancel', child_id=child)
            await control(children, 'close', 'close', child_id=child)
        children._deliver_contribution(owner, binding, generation, child, {'text': 'OLD'})
        assert not any(event['type'] == 'child_contribution' for event in events)
    finally:
        await close(session, nav, sources, children)


async def test_child_contribution_publish_queue_byte_and_secret_bounds(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        children.negotiate({'child_contributions': 1})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        publish = session.children.supervisor.publish
        for _ in range(100):
            publish({'type': 'extension_notice', 'child_id': child, 'text': 'x' * 40000})
        assert children._contribution_pending == 32
        await asyncio.sleep(0)
        publish({'type': 'extension_ui', 'child_id': child, 'text': 'token=PRIVATE',
                 'snapshot': {'ui': {'password': 'PRIVATE', 'public': ['SAFE']}}})
        await asyncio.sleep(0)
        records = [event for event in events if event['type'] == 'child_contribution']
        assert len(records) == 33 and any(event.get('limited') for event in records)
        assert 'PRIVATE' not in json.dumps(records) and 'SAFE' in json.dumps(records)
        assert all(len(json.dumps(event).encode()) < 64 * 1024 for event in records)
    finally:
        await close(session, nav, sources, children)


def test_actual_cli_child_worker_contributions_do_not_enter_parent_history(tmp_path):
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
        peer.send({'type': 'rpc_hello', 'child_contributions': 1})
        caps = peer.read('child_capabilities')
        while not caps['child_contributions']:
            caps = peer.read('child_capabilities')
        peer.caps = caps
        peer.send(peer.packet('start', action='start', task='hold'))
        started = peer.read('child_result')
        assert started['success']
        event = peer.read('child_contribution')
        assert event['child_id'] == started['child_id'] and event['child_owner'] == caps['child_owner']
        assert event['generation'] == caps['contribution_generation'] and event['retained'] is False
        assert 'sample' in json.dumps(event)
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()
