"""Client-rendered custom frame protocol uses the existing painted broker completion gates."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, packet, response, session


async def wait(predicate):
    async with asyncio.timeout(6):
        while not predicate():
            await asyncio.sleep(0.003)


def frame_input(dialog, generation, sequence, *, kind='resize', painted=-1, width=40, height=5, **options):
    return {'type': 'extension_custom_input', 'session_id': dialog['session_id'],
            'request_id': dialog['request_id'], 'scope': dialog['scope'], 'id': dialog['dialog']['id'],
            'custom_generation': generation, 'sequence': sequence,
            'event': {'kind': kind, 'width': width, 'height': height, 'painted': painted, 'generation': 1,
                      **({'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False} if kind == 'key' else {}),
                      **options}}


async def test_actual_worker_custom_frame_review_sequence_and_completion_without_model_or_approval(tmp_path):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        generation = dialog['custom_generation']
        assert dialog['dialog']['kind'] == 'custom'
        with pytest.raises(ValueError):
            controller.answer(response(dialog, {'forged': 'completion'}))
        controller.custom_input(frame_input(dialog, generation, 1))
        await wait(lambda: any(event['type'] == 'extension_custom_frame' for event in events))
        first = next(event for event in events if event['type'] == 'extension_custom_frame')
        assert '\x1b' not in ''.join(first['lines']) and 'unsafe' not in ''.join(first['lines'])
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(dialog, generation, 1))
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(dialog, generation, 2, kind='key', painted=0))
        controller.custom_input(frame_input(dialog, generation, 2, kind='key', painted=first['revision']))
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success']
        assert json.loads(result['output'])['value'] == {'selected': False, 'count': 0, 'mode': None}
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
        assert not owner.extension_dialogs.custom.views
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('change', ['withdraw', 'cancel', 'foreign', 'reserved', 'resize'])
async def test_custom_view_owner_and_control_recovery_never_admit_forged_completion(tmp_path, change):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        generation = dialog['custom_generation']
        controller.custom_input(frame_input(dialog, generation, 1))
        await wait(lambda: controller.custom_frames)
        frame = controller.custom_frames[dialog['dialog']['id']]
        if change == 'withdraw':
            controller.negotiate({'extension_custom_views': 0})
            with pytest.raises(ValueError):
                controller.custom_input(frame_input(dialog, generation, 2, kind='key', painted=frame['revision']))
        elif change == 'cancel':
            controller.answer(response(dialog, None))
        elif change == 'foreign':
            for field in ('scope', 'request_id', 'session_id', 'id', 'custom_generation'):
                with pytest.raises(ValueError):
                    controller.custom_input({**frame_input(dialog, generation, 2), field: 'foreign'})
            controller.answer(response(dialog, None))
        elif change == 'reserved':
            for key, ctrl in (('escape', False), ('c', True), ('z', True)):
                with pytest.raises(ValueError):
                    controller.custom_input(frame_input(dialog, generation, 2, kind='key',
                                                        painted=frame['revision'], key=key, ctrl=ctrl))
            controller.answer(response(dialog, None))
        else:
            controller.custom_input(frame_input(dialog, generation, 2, width=20, height=3))
            await wait(lambda: dialog['dialog']['id'] in controller.custom_frames
                       and controller.custom_frames[dialog['dialog']['id']]['revision'] > frame['revision'])
            with pytest.raises(ValueError):
                controller.custom_input(frame_input(dialog, generation, 3, kind='key', painted=frame['revision']))
            controller.answer(response(dialog, None))
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert json.loads(result['output'])['value'] is None
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_custom_frames_complete_only_after_explicit_current_painted_input(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom', root / 'custom')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1})
        capabilities = peer.read('extension_custom_capabilities')
        assert capabilities['extension_custom_views'] == 1 and capabilities['client_rendered'] is True
        peer.send({'type': 'extension_control', 'request_id': 'custom', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-custom-view', 'args': ''})
        dialog = peer.read('extension_dialog')
        generation = dialog['custom_generation']
        peer.send(frame_input(dialog, generation, 1))
        frame = peer.read('extension_custom_frame')
        assert frame['width'] == 40 and frame['height'] == 5 and frame['custom_generation'] == generation
        assert '\x1b' not in ''.join(frame['lines'])
        peer.send(frame_input(dialog, generation, 2, kind='key', painted=0))
        assert peer.read('error')['action'] == 'extension_custom_input'
        peer.send(frame_input(dialog, generation, 3, kind='key', painted=frame['revision']))
        result = peer.read('extension_result')
        assert result['success']
        assert json.loads(result['output'])['value'] == {'selected': False, 'count': 0, 'mode': None}
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('value', [True, False, None, '1', 2])
async def test_custom_capability_requires_exact_integer_and_preserves_dialog_flags(tmp_path, value):
    owner, _ = session(tmp_path)
    controller = RpcExtensions(owner, lambda _: None)
    try:
        controller.negotiate({'extension_dialogs': 1})
        with pytest.raises(ValueError):
            controller.negotiate({'extension_custom_views': value})
        assert not controller.custom_enabled and controller.dialogs_enabled
        controller.negotiate({'extension_custom_views': 1})
        assert controller.custom_enabled and controller.dialogs_enabled
    finally:
        await controller.aclose()
        await close(owner)


async def test_renderer_withdrawal_during_actual_worker_call_has_no_late_frame(tmp_path):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        command = {**packet(owner, 'held', 'ext-custom-view'), 'args': 'hang'}
        controller.start(command, turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        controller.custom_input(frame_input(dialog, dialog['custom_generation'], 1))
        await wait(lambda: bool(owner.extension_dialogs.custom.views))
        controller.negotiate({'extension_custom_views': 0})
        async with asyncio.timeout(6):
            await controller.task
        assert not any(event['type'] == 'extension_custom_frame' for event in events)
        assert not owner.extension_dialogs.custom.views and not calls
        assert owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('nested_kind', ['question', 'custom'])
async def test_actual_worker_nested_dialog_focus_restores_only_fresh_parent_frame(tmp_path, nested_kind):
    owner, calls = session(tmp_path, extra=('nested',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1, 'extension_dialogs': 1})
        command = {**packet(owner, 'nested', 'ext-nested-view'), 'args': 'custom' if nested_kind == 'custom' else ''}
        controller.start(command, turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        parent = next(event for event in events if event['type'] == 'extension_dialog')
        generation = parent['custom_generation']
        controller.custom_input(frame_input(parent, generation, 1))
        await wait(lambda: parent['dialog']['id'] in controller.custom_frames)
        first = controller.custom_frames[parent['dialog']['id']]
        controller.custom_input(frame_input(parent, generation, 2, kind='key', key='n', painted=first['revision']))
        await wait(lambda: len([event for event in events if event['type'] == 'extension_dialog']) == 2)
        child = [event for event in events if event['type'] == 'extension_dialog'][-1]
        assert child['dialog']['parent_id'] == parent['dialog']['id']
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(parent, generation, 3, kind='key', painted=first['revision']))
        if nested_kind == 'question':
            controller.answer(response(child, 'NESTED-ANSWER'))
            await wait(lambda: len([event for event in events if event['type'] == 'extension_dialog']) == 3)
            confirm = [event for event in events if event['type'] == 'extension_dialog'][-1]
            assert confirm['dialog']['kind'] == 'confirm'
            controller.answer(response(confirm, True))
        else:
            controller.custom_input(frame_input(child, generation, 1))
            await wait(lambda: child['dialog']['id'] in controller.custom_frames)
            child_frame = controller.custom_frames[child['dialog']['id']]
            controller.custom_input(frame_input(child, generation, 2, kind='key', painted=child_frame['revision']))
        await wait(lambda: parent['dialog']['id'] in controller.custom_frames
                   and controller.custom_frames[parent['dialog']['id']]['revision'] > first['revision'])
        restored = controller.custom_frames[parent['dialog']['id']]
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(parent, generation, 4, kind='key', painted=first['revision']))
        controller.custom_input(frame_input(parent, generation, 5, kind='key', painted=restored['revision']))
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success']
        values = json.loads(result['output'])['value']['values']
        assert values == (['NESTED-ANSWER', True] if nested_kind == 'question' else [{'child': False}])
        assert not owner.extension_dialogs.has_pending and not owner.extension_dialogs.custom.views
        assert not owner.messages and not calls and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


async def test_actual_nested_question_withdrawal_cascades_and_does_not_replay_late_answer(tmp_path):
    owner, calls = session(tmp_path, extra=('nested',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1, 'extension_dialogs': 1})
        controller.start(packet(owner, 'nested', 'ext-nested-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        parent = next(event for event in events if event['type'] == 'extension_dialog')
        controller.custom_input(frame_input(parent, parent['custom_generation'], 1))
        await wait(lambda: controller.custom_frames)
        first = controller.custom_frames[parent['dialog']['id']]
        controller.custom_input(frame_input(parent, parent['custom_generation'], 2, kind='key', key='n',
                                            painted=first['revision']))
        await wait(lambda: len([event for event in events if event['type'] == 'extension_dialog']) == 2)
        child = [event for event in events if event['type'] == 'extension_dialog'][-1]
        controller.negotiate({'extension_custom_views': 0})
        async with asyncio.timeout(6):
            await controller.task
        with pytest.raises(ValueError):
            controller.answer(response(child, 'LATE'))
        assert not owner.extension_dialogs.has_pending and not owner.extension_dialogs.custom.views
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_nested_question_suspends_parent_and_restores_fresh_frame(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/nested', root / 'nested')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['nested'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1, 'extension_dialogs': 1})
        peer.read('extension_custom_capabilities')
        peer.read('rpc_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'nested', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-nested-view', 'args': ''})
        parent = peer.read('extension_dialog')
        generation = parent['custom_generation']
        peer.send(frame_input(parent, generation, 1))
        first = peer.read('extension_custom_frame')
        peer.send(frame_input(parent, generation, 2, kind='key', key='n', painted=first['revision']))
        child = peer.read('extension_dialog')
        assert child['dialog']['parent_id'] == parent['dialog']['id']
        peer.send(frame_input(parent, generation, 3, kind='key', painted=first['revision']))
        assert peer.read('error')['action'] == 'extension_custom_input'
        peer.send(response(child, 'CLI-NESTED-ANSWER'))
        confirm = peer.read('extension_dialog')
        assert confirm['dialog']['kind'] == 'confirm'
        peer.send(response(confirm, True))
        restored = peer.read('extension_custom_frame')
        assert restored['revision'] > first['revision'] and restored['id'] == parent['dialog']['id']
        peer.send(frame_input(parent, generation, 4, kind='key', painted=first['revision']))
        assert peer.read('error')['action'] == 'extension_custom_input'
        peer.send(frame_input(parent, generation, 5, kind='key', painted=restored['revision']))
        result = peer.read('extension_result')
        assert result['success'] and json.loads(result['output'])['value']['values'] == ['CLI-NESTED-ANSWER', True]
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


async def test_nested_broker_admission_protects_parent_before_transport_publication(tmp_path, monkeypatch):
    owner, _ = session(tmp_path, extra=('nested',))
    events, deferred = [], []
    controller = RpcExtensions(owner, events.append)
    real_publish = controller._publish
    def held(event):
        if event.get('type') == 'extension_dialog' and event['dialog'].get('parent_id'):
            deferred.append(event)
        else:
            real_publish(event)
    monkeypatch.setattr(controller, '_publish', held)
    try:
        controller.negotiate({'extension_custom_views': 1, 'extension_dialogs': 1})
        controller.start(packet(owner, 'nested', 'ext-nested-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        parent = next(event for event in events if event['type'] == 'extension_dialog')
        generation = parent['custom_generation']
        controller.custom_input(frame_input(parent, generation, 1))
        await wait(lambda: controller.custom_frames)
        first = controller.custom_frames[parent['dialog']['id']]
        controller.custom_input(frame_input(parent, generation, 2, kind='key', key='n', painted=first['revision']))
        await wait(lambda: deferred)
        assert len(controller.dialogs) == 1  # The frontend has not received the nested dialog yet.
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(parent, generation, 3, kind='key', painted=first['revision']))
        controller.answer(response(parent, None))  # Host cancel may cascade without borrowing focused input.
        async with asyncio.timeout(6):
            await controller.task
        assert not owner.extension_dialogs.has_pending and not owner.extension_dialogs.custom.views
    finally:
        await controller.aclose()
        await close(owner)


async def test_admitted_resize_revokes_old_frame_before_held_worker_render_returns(tmp_path, monkeypatch):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    import threading
    entered, release = threading.Event(), threading.Event()
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        generation = dialog['custom_generation']
        controller.custom_input(frame_input(dialog, generation, 1))
        await wait(lambda: controller.custom_frames)
        first = controller.custom_frames[dialog['dialog']['id']]
        view = owner.extension_dialogs.custom.views[dialog['dialog']['id']]
        render = view.render
        async def held(event):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.003)
            return await render(event)
        monkeypatch.setattr(view, 'render', held)
        controller.custom_input(frame_input(dialog, generation, 2, width=20, height=3))
        assert dialog['dialog']['id'] not in controller.custom_frames
        await wait(entered.is_set)
        with pytest.raises(ValueError, match='current delivered frame'):
            controller.custom_input(frame_input(dialog, generation, 3, kind='key', painted=first['revision']))
        assert controller.custom_sequences[dialog['dialog']['id']] == 2
        release.set()
        await wait(lambda: dialog['dialog']['id'] in controller.custom_frames)
        fresh = controller.custom_frames[dialog['dialog']['id']]
        assert (fresh['width'], fresh['height']) == (20, 3)
        controller.custom_input(frame_input(dialog, generation, 4, kind='key', width=20, height=3,
                                            painted=fresh['revision']))
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert json.loads(result['output'])['inputs'] == ['enter']
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        release.set()
        await controller.aclose()
        await close(owner)


async def test_superseded_render_cannot_restore_input_while_new_geometry_is_still_held(tmp_path, monkeypatch):
    import threading

    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    first_started, second_started = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        generation = dialog['custom_generation']
        view_id = dialog['dialog']['id']
        view = owner.extension_dialogs.custom.views[view_id]
        render = view.render
        async def held(event):
            started = first_started if event['generation'] == 1 else second_started
            release = release_first if event['generation'] == 1 else release_second
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.003)
            return await render(event)
        monkeypatch.setattr(view, 'render', held)
        controller.custom_input(frame_input(dialog, generation, 1))
        await wait(first_started.is_set)
        newer = frame_input(dialog, generation, 2, width=20, height=3)
        newer['event']['generation'] = 2
        controller.custom_input(newer)
        release_first.set()
        await wait(second_started.is_set)
        await asyncio.sleep(0.02)  # Let the queued obsolete frame reach the actual RPC publication filter.
        assert view_id not in controller.custom_frames
        assert not any(event['type'] == 'extension_custom_frame' for event in events)
        with pytest.raises(ValueError, match='current delivered frame'):
            controller.custom_input(frame_input(dialog, generation, 3, kind='key', painted=1))
        release_second.set()
        await wait(lambda: view_id in controller.custom_frames)
        fresh = controller.custom_frames[view_id]
        assert fresh['generation'] == 2 and (fresh['width'], fresh['height']) == (20, 3)
        controller.answer(response(dialog, None))
        await controller.task
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        release_first.set()
        release_second.set()
        await controller.aclose()
        await close(owner)
