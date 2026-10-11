"""Private socket parent catalog/binary read and client full-review admission; no service/model."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.keys import KeyEvent
from xdog.tui.parent_draft_receiver import ParentDraftReceiver, composer_receipt

from packages.claw.tests.test_mcp_sampling_gateway import receive, setup
from packages.tui.tests.test_parent_draft_receiver import PNG, paint


@pytest.mark.parametrize('images', [False, True])
async def test_socket_parent_catalog_read_complete_receipt_and_client_review(tmp_path, images):
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    root = Path(config.data_dir) / 'saved-parent-drafts'
    root.parent.chmod(0o700)
    draft = ChildDraft('PRIVATE SOCKET RECOVERY', images=(attachment(PNG, 'private.png'),) if images else ())
    store = ChildDraftStore(root)
    saved = store.save(encode_draft(draft, label='parent', scope=session.meta.session_id, action='parent', slot=''))
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    receiver = ParentDraftReceiver()
    try:
        pong = await send({'type': 'ping', 'group_id': 'main'}, 'pong')
        assert pong['parent_saved_drafts'] == 0 and pong['parent_saved_images'] == 0
        refused = await send({'type': 'parent_draft_control', 'request_id': 'old', 'action': 'catalog',
                              'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32},
                             'parent_draft_result')
        assert not refused['success']
        caps = await send({'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1,
                           'parent_saved_images': int(images)}, 'pong')
        assert caps['parent_saved_drafts'] == 1 and caps['parent_saved_save'] == 0
        scope = {'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32,
                 'parent_draft_owner': caps['parent_draft_owner'],
                 'parent_draft_generation': caps['parent_draft_generation']}
        catalog = await send({'type': 'parent_draft_control', **scope, 'request_id': 'catalog', 'action': 'catalog'},
                             'parent_draft_result')
        assert catalog['success'] and catalog['catalog'][0]['id'] == saved.id
        assert 'PRIVATE SOCKET RECOVERY' not in str(catalog)
        receipt = composer_receipt(ChildDraft(), 4)
        expected = {'request_id': 'read', **scope, 'session_revision': catalog['session_revision']}
        receiver.expect(expected, receipt, saved.id, saved.sha256)
        writer.write((json.dumps({'type': 'parent_draft_control', **scope, 'request_id': 'read', 'action': 'read',
                      'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256, 'composer': receipt}) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                frame = json.loads(await reader.readline())
                if frame['type'] == 'parent_draft_result':
                    final = frame
                    break
                receiver.feed(frame)
        recovery = await asyncio.to_thread(receiver.finish, 'read', final)
        adopted = []
        panel = recovery.panel(lambda: expected, lambda: receipt, lambda *args: adopted.append(args), lambda: None)
        panel.focused = True
        panel.handle_input(KeyEvent('enter'))
        assert not adopted
        paint(panel)
        panel.handle_input(KeyEvent('enter'))
        assert adopted[0][0].text == draft.text
        if images:
            assert adopted[0][0].images[0].data == PNG
        assert not adapter.calls and not session.agent.messages
        assert not server._orchestrator.is_group_running('main')
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        receiver.close()
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_foreign_socket_owner_and_withdrawn_generation_cannot_read_parent(tmp_path):
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(reader, writer, packet, kind):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        ping = {'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1}
        caps = await send(reader, writer, ping, 'pong')
        await send(other_reader, other_writer, ping, 'pong')
        packet = {'type': 'parent_draft_control', 'request_id': 'foreign', 'group_id': 'main',
            'session_id': session.meta.session_id, 'view': 'a' * 32, 'action': 'catalog',
            'parent_draft_owner': caps['parent_draft_owner'],
            'parent_draft_generation': caps['parent_draft_generation']}
        assert not (await send(other_reader, other_writer, packet, 'parent_draft_result'))['success']
        await send(reader, writer, {**ping, 'parent_saved_drafts': 0}, 'pong')
        await send(reader, writer, ping, 'pong')
        assert not (await send(reader, writer, packet, 'parent_draft_result'))['success']
        assert not adapter.calls
    finally:
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


async def test_socket_withdrawal_keeps_reader_live_but_waits_actual_parent_catalog_worker(tmp_path, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.catalog
    def held(self):
        entered.set()
        assert release.wait(3)
        return original(self)
    monkeypatch.setattr(ChildDraftStore, 'catalog', held)
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        caps = await send({'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1}, 'pong')
        writer.write((json.dumps({'type': 'parent_draft_control', 'request_id': 'held', 'action': 'catalog',
            'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32,
            'parent_draft_owner': caps['parent_draft_owner'],
            'parent_draft_generation': caps['parent_draft_generation']}) + '\n').encode())
        await writer.drain()
        assert await asyncio.to_thread(entered.wait, 2)
        pong = await send({'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 0}, 'pong')
        assert pong['parent_saved_drafts'] == 0
        assert not server._orchestrator.try_reserve_group('main')  # Held disk work still owns reservation.
        release.set()
        result = await receive(reader, 'parent_draft_result')
        assert not result['success'] and result['canceled'] and not adapter.calls
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('images', [False, True])
async def test_socket_parent_save_and_discard_two_phase_original_view_and_no_replay(tmp_path, images):
    import base64
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind='parent_draft_result'):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        caps = await send({'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1,
            'parent_saved_save': 1, 'parent_saved_discard': 1, 'parent_saved_images': int(images)}, 'pong')
        assert caps['parent_saved_save'] == caps['parent_saved_discard'] == 1
        scope = {'type': 'parent_draft_control', 'group_id': 'main', 'session_id': session.meta.session_id,
            'view': 'a' * 32, 'parent_draft_owner': caps['parent_draft_owner'],
            'parent_draft_generation': caps['parent_draft_generation']}
        draft = ChildDraft('PRIVATE SOCKET SAVE', images=(attachment(PNG, 'image.png'),) if images else ())
        receipt = composer_receipt(draft, 1)
        prepared = await send({**scope, 'request_id': 'prepare', 'action': 'save_prepare',
            'text': draft.text, 'composer': receipt, 'images': [{'name': image.name, 'mime_type': image.mime_type,
                'data': base64.b64encode(image.data).decode()} for image in draft.images]})
        assert prepared['success'], prepared
        root = Path(config.data_dir) / 'saved-parent-drafts'
        assert not root.exists()  # Preparation is not disk mutation.
        confirm = {**scope, 'request_id': 'confirm', 'action': 'save_confirm', 'composer': receipt,
                   'review_id': prepared['review_id'], 'review': prepared['review'], 'confirmed': True}
        assert not (await send({**confirm, 'request_id': 'foreign', 'view': 'b' * 32}))['success']
        changed = {**receipt, 'revision': 2}
        assert not (await send({**confirm, 'request_id': 'changed', 'composer': changed}))['success']
        saved = await send(confirm)
        assert saved['success'], saved
        assert not (await send({**confirm, 'request_id': 'replay'}))['success']
        catalog = await send({**scope, 'request_id': 'catalog', 'action': 'catalog'})
        assert catalog['success'] and len(catalog['catalog']) == 1
        discard = await send({**scope, 'request_id': 'discard-prepare', 'action': 'discard_prepare',
            'composer': receipt, 'snapshot_id': saved['snapshot_id'], 'snapshot_sha256': saved['snapshot_sha256']})
        assert discard['success'] and root.exists()
        assert discard['review']['content'] == draft.text
        done = await send({**scope, 'request_id': 'discard-confirm', 'action': 'discard_confirm',
            'composer': receipt, 'review_id': discard['review_id'], 'review': discard['review'], 'confirmed': True})
        assert done['success'] and not list(root.glob('*.json'))
        assert not adapter.calls and not session.agent.messages and draft.text == 'PRIVATE SOCKET SAVE'
        assert not server._orchestrator.is_group_running('main')
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_socket_parent_save_flag_withdrawal_revokes_review_and_exact_json_types(tmp_path):
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind='parent_draft_result'):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        ping = {'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1, 'parent_saved_save': 1}
        caps = await send(ping, 'pong')
        scope = {'type': 'parent_draft_control', 'group_id': 'main', 'session_id': session.meta.session_id,
            'view': 'a' * 32, 'parent_draft_owner': caps['parent_draft_owner'],
            'parent_draft_generation': caps['parent_draft_generation']}
        composer = composer_receipt(ChildDraft('PRIVATE'), 1)
        prepared = await send({**scope, 'request_id': 'prepare', 'action': 'save_prepare',
                              'text': 'PRIVATE', 'composer': composer})
        assert prepared['success']
        altered = json.loads(json.dumps(prepared['review']))
        altered['composer']['empty'] = 0
        confirm = {**scope, 'request_id': 'bad-type', 'action': 'save_confirm', 'composer': composer,
                   'review_id': prepared['review_id'], 'review': altered, 'confirmed': True}
        assert not (await send(confirm))['success']
        assert not (Path(config.data_dir) / 'saved-parent-drafts').exists()
        await send({**ping, 'parent_saved_save': 0}, 'pong')
        fresh = await send(ping, 'pong')
        confirm.update(request_id='old-review', parent_draft_generation=fresh['parent_draft_generation'],
                       review=prepared['review'])
        assert not (await send(confirm))['success']
        assert not adapter.calls and not (Path(config.data_dir) / 'saved-parent-drafts').exists()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('end', ['recover', 'changed', 'close'])
@pytest.mark.parametrize('images', [False, True])
async def test_actual_chatapp_remote_parent_catalog_full_review_composer_and_revocation(tmp_path, end, images):
    from xdog.claw.channels.tui.tui_app import ChatApp
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    draft = ChildDraft('REMOTE UNSENT PARENT', images=(attachment(PNG, 'remote.png'),) if images else ())
    saved = store.save(encode_draft(draft, label='remote', scope=session.meta.session_id, action='parent', slot=''))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_drafts') == 1)
        ui._parent_draft_command('remote')
        await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
        panel = ui._remote_parent_panel
        assert panel.rows[0]['id'] == saved.id and not ui._editor.get_text() and not adapter.calls
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.review is not None)
        review = panel.review
        review.handle_input(KeyEvent('enter'))
        assert not ui._editor.get_text() and not ui._composer.items
        paint(review)
        if end == 'changed':
            ui._editor.set_text('NEW USER DRAFT')
            ui._editor.set_text('')  # ABA: content back to empty still has changed revision.
            with pytest.raises(ValueError, match='changed'):
                review.handle_input(KeyEvent('enter'))
            assert not ui._editor.get_text() and not ui._composer.items
        elif end == 'close':
            ui._close_remote_parent()
            review.handle_input(KeyEvent('enter'))
            assert not ui._editor.get_text() and not ui._composer.items
        else:
            review.handle_input(KeyEvent('enter'))
            assert ui._editor.get_text() == draft.text
            assert len(ui._composer.items) == int(images)
            if images:
                assert next(iter(ui._composer.items.values())).data == PNG
            assert ui._remote_parent_panel is None
        assert not adapter.calls and not session.agent.messages
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('operation', ['save', 'discard'])
@pytest.mark.parametrize('end', ['confirm', 'change', 'close'])
async def test_actual_chatapp_remote_disk_review_no_early_mutation_and_no_composer_change(tmp_path, operation, end):
    from xdog.claw.channels.tui.tui_app import ChatApp
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    if operation == 'discard':
        store.save(encode_draft(ChildDraft('DISK PRIVATE'), label='delete target',
                                         scope=session.meta.session_id, action='parent', slot=''))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_save') == 1)
        ui._editor.set_text('UNSENT PARENT')
        if operation == 'save':
            ui._parent_draft_command('remote-save')
        else:
            ui._parent_draft_command('remote')
            await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
            ui._remote_parent_panel.render(80)
            ui._remote_parent_panel.handle_input(KeyEvent('x'))
        await wait(lambda: ui._remote_parent_panel is not None and ui._remote_parent_panel.review is not None)
        panel = ui._remote_parent_panel
        review = panel.review
        review.handle_input(KeyEvent('enter'))
        assert len(store.catalog()) == int(operation == 'discard') and ui._editor.get_text() == 'UNSENT PARENT'
        paint(review)
        if end == 'change':
            ui._editor.set_text('CHANGED PARENT')
            with pytest.raises(ValueError, match='changed'):
                review.handle_input(KeyEvent('enter'))
        elif end == 'close':
            ui._close_remote_parent()
            review.handle_input(KeyEvent('enter'))
        else:
            review.handle_input(KeyEvent('enter'))
            await wait(lambda: ui._remote_parent_panel is None)
            rows = store.catalog()
            assert len(rows) == int(operation == 'save')
            if operation == 'save':
                assert store.load(rows[0]['id']).draft.text == 'UNSENT PARENT'
        assert ui._editor.get_text() == ('CHANGED PARENT' if end == 'change' else 'UNSENT PARENT')
        if end != 'confirm':
            assert len(store.catalog()) == int(operation == 'discard')
        assert not adapter.calls and not session.agent.messages
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('size', [1024 * 1024, 10 * 1024 * 1024])
async def test_actual_chatapp_chunked_parent_image_save_beyond_old_frame_limit(tmp_path, size):
    from xdog.claw.channels.tui.tui_app import ChatApp
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    # Valid fixture header plus inert retained bytes; shared attachment validator
    # accepts exactly this representation, no provider/desktop decoding involved.
    image = attachment(PNG + b'X' * (size - len(PNG)), 'large.png')
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(20):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_uploads') == 1)
        ui._editor.set_text('LARGE UNSENT PARENT')
        ui._composer.restore((image,))
        ui._parent_draft_command('remote-save')
        await wait(lambda: ui._remote_parent_panel is not None and ui._remote_parent_panel.review is not None)
        assert not store.catalog() and not adapter.calls
        review = ui._remote_parent_panel.review
        assert review.event['body']['images'][0]['bytes'] == len(image.data)
        paint(review)
        review.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._remote_parent_panel is None)
        saved = store.load(store.catalog()[0]['id'])
        assert saved.draft.images[0].data == image.data and saved.draft.text == 'LARGE UNSENT PARENT'
        assert ui._editor.get_text() == saved.draft.text
        assert next(iter(ui._composer.items.values())).data == image.data
        assert not adapter.calls and not session.agent.messages
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('fault', ['foreign-view', 'bad-index', 'bad-hash', 'withdraw'])
async def test_parent_upload_scope_chunk_integrity_revocation_and_no_disk_mutation(tmp_path, fault):
    import base64
    import hashlib
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind='parent_draft_result'):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        ping = {'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1, 'parent_saved_images': 1,
                'parent_saved_save': 1, 'parent_saved_uploads': 1}
        caps = await send(ping, 'pong')
        scope = {'type': 'parent_draft_control', 'group_id': 'main', 'session_id': session.meta.session_id,
                 'view': 'a' * 32, 'parent_draft_owner': caps['parent_draft_owner'],
                 'parent_draft_generation': caps['parent_draft_generation']}
        first = await send({**scope, 'request_id': 'chunk0', 'action': 'upload_chunk', 'id': 'image',
                            'index': 0, 'mime_type': 'image/png', 'data': base64.b64encode(PNG[:20]).decode()})
        assert first['success'] and first['upload_status'] == 'partial'
        packet = {**scope, 'request_id': 'chunk1', 'action': 'upload_chunk', 'id': 'image', 'index': 1,
                  'mime_type': 'image/png', 'data': base64.b64encode(PNG[20:]).decode(), 'final': True,
                  'sha256': hashlib.sha256(PNG).hexdigest()}
        if fault == 'foreign-view':
            packet['view'] = 'b' * 32
        elif fault == 'bad-index':
            packet['index'] = 0
        elif fault == 'bad-hash':
            packet['sha256'] = '0' * 64
        else:
            await send({**ping, 'parent_saved_uploads': 0}, 'pong')
        rejected = await send(packet)
        assert not rejected['success'] and not adapter.calls
        assert not (Path(config.data_dir) / 'saved-parent-drafts').exists()
        assert not server._orchestrator.is_group_running('main')
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_parent_upload_withdrawal_waits_owned_validator_and_releases_buffer(tmp_path, monkeypatch):
    import base64
    import hashlib
    import threading

    from xdog.claw.channels.tui.image_upload import ImageUploads
    entered, release = threading.Event(), threading.Event()
    original = ImageUploads.chunk
    def held(self, packet):
        entered.set()
        assert release.wait(3)
        return original(self, packet)
    monkeypatch.setattr(ImageUploads, 'chunk', held)
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet, kind='parent_draft_result'):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        ping = {'type': 'ping', 'group_id': 'main', 'parent_saved_drafts': 1, 'parent_saved_images': 1,
                'parent_saved_save': 1, 'parent_saved_uploads': 1}
        caps = await send(ping, 'pong')
        packet = {'type': 'parent_draft_control', 'action': 'upload_chunk', 'request_id': 'held',
            'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32,
            'parent_draft_owner': caps['parent_draft_owner'],
            'parent_draft_generation': caps['parent_draft_generation'], 'id': 'image', 'index': 0,
            'mime_type': 'image/png', 'data': base64.b64encode(PNG).decode(), 'final': True,
            'sha256': hashlib.sha256(PNG).hexdigest()}
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await send({**ping, 'parent_saved_uploads': 0}, 'pong'))['parent_saved_uploads'] == 0
        assert not server._orchestrator.try_reserve_group('main')
        release.set()
        failed = await receive(reader, 'parent_draft_result')
        assert not failed['success'] and not adapter.calls
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_failed_parent_upload_ack_retires_unknown_reference_and_timer(tmp_path):
    import base64
    import hashlib

    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    async def failed_write(row):
        raise OSError('PRIVATE TRANSPORT FAILURE')
    controller = ParentDraftConnection(server._orchestrator,
                                       Path(config.data_dir) / 'saved-parent-drafts', failed_write)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_images': 1,
                          'parent_saved_save': 1, 'parent_saved_uploads': 1})
    try:
        await controller.request({'type': 'parent_draft_control', 'action': 'upload_chunk', 'request_id': 'upload',
            'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32,
            'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation,
            'id': 'unknown', 'index': 0, 'mime_type': 'image/png', 'data': base64.b64encode(PNG).decode(),
            'final': True, 'sha256': hashlib.sha256(PNG).hexdigest()})
        await asyncio.gather(*tuple(controller.jobs.values()), return_exceptions=True)
        await asyncio.sleep(0)
        assert not controller.uploads.contains('unknown')
        assert 'unknown' not in controller.upload_owners and 'unknown' not in controller.upload_timers
        assert 'unknown' in controller.upload_used  # Identity is retired, not made reusable.
        assert not adapter.calls
    finally:
        await controller.close()
        await server.stop()


async def test_claw_lost_committed_save_receipt_never_replays_or_rolls_back(tmp_path):
    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    events = []
    async def write(row):
        if row.get('action') == 'save_confirm':
            raise OSError('Lost save receipt')
        events.append(row)
    controller = ParentDraftConnection(server._orchestrator, Path(config.data_dir) / 'saved-parent-drafts', write)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    scope = {'type': 'parent_draft_control', 'group_id': 'main', 'session_id': session.meta.session_id,
             'view': 'a' * 32, 'parent_draft_owner': controller.owner,
             'parent_draft_generation': controller.generation}
    async def run(packet):
        await controller.request(packet)
        await asyncio.gather(*tuple(controller.jobs.values()), return_exceptions=True)
        await asyncio.sleep(0)
    try:
        receipt = composer_receipt(ChildDraft('PRIVATE'), 1)
        await run({**scope, 'request_id': 'prepare', 'action': 'save_prepare', 'text': 'PRIVATE', 'composer': receipt})
        prepared = events[-1]
        assert prepared['success']
        confirm = {**scope, 'action': 'save_confirm', 'composer': receipt,
                   'review_id': prepared['review_id'], 'review': prepared['review'], 'confirmed': True}
        await run({**confirm, 'request_id': 'confirm'})
        store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
        assert len(store.catalog()) == 1 and not controller.reviews
        await run({**confirm, 'request_id': 'retry'})
        assert len(store.catalog()) == 1 and not adapter.calls
        assert not any(row.get('action') == 'save_confirm' for row in events)
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
    finally:
        await controller.close()
        await server.stop()


async def test_claw_failed_prepare_delivery_retires_private_review_bytes(tmp_path):
    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    async def failed(row):
        raise OSError('Undelivered private review')
    controller = ParentDraftConnection(server._orchestrator, Path(config.data_dir) / 'saved-parent-drafts', failed)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        await controller.request({'request_id': 'prepare', 'action': 'save_prepare', 'group_id': 'main',
            'session_id': session.meta.session_id, 'view': 'a' * 32, 'parent_draft_owner': controller.owner,
            'parent_draft_generation': controller.generation, 'text': 'PRIVATE',
            'composer': composer_receipt(ChildDraft('PRIVATE'), 1)})
        await asyncio.gather(*tuple(controller.jobs.values()), return_exceptions=True)
        assert not controller.reviews and not adapter.calls
    finally:
        await controller.close()
        await server.stop()


async def test_claw_parent_review_auto_expiry_and_withdraw_cancel_timers(tmp_path):
    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    events = []
    async def write(row):
        events.append(row)
    controller = ParentDraftConnection(server._orchestrator, Path(config.data_dir) / 'saved-parent-drafts', write)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    async def prepare(rid):
        await controller.request({'request_id': rid, 'action': 'save_prepare', 'group_id': 'main',
            'session_id': session.meta.session_id, 'view': 'a' * 32, 'parent_draft_owner': controller.owner,
            'parent_draft_generation': controller.generation, 'text': 'PRIVATE',
            'composer': composer_receipt(ChildDraft('PRIVATE'), 1)})
        await asyncio.gather(*tuple(controller.jobs.values()))
        await asyncio.sleep(0)
        return events[-1]['review_id']
    try:
        key = await prepare('first')
        binding = controller.reviews[key]
        controller.reviews[key] = binding[0], 0, binding[2], binding[3]
        controller.review_timers[key].cancel()
        controller.review_timers[key] = asyncio.get_running_loop().call_later(.01, controller._expire_review, key)
        await asyncio.sleep(.03)
        assert not controller.reviews and not controller.review_timers
        key = await prepare('second')
        timer = controller.review_timers[key]
        controller.negotiate({'parent_saved_save': 0})
        assert timer.cancelled() and not controller.review_timers and not controller.reviews
        assert not adapter.calls
    finally:
        await controller.close()
        await server.stop()


async def test_actual_chatapp_close_revokes_held_read_without_disconnect(tmp_path, monkeypatch):
    import threading

    from xdog.claw.channels.tui.tui_app import ChatApp
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.load
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    saved = store.save(encode_draft(ChildDraft('PRIVATE'), label='held', scope=session.meta.session_id,
                                  action='parent', slot=''))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def held(self, key):
        entered.set()
        assert release.wait(3)
        return original(self, key)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_drafts') == 1)
        ui._parent_draft_command('remote')
        await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
        monkeypatch.setattr(ChildDraftStore, 'load', held)
        panel = ui._remote_parent_panel
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert await asyncio.to_thread(entered.wait, 2)
        scope = dict(ui._remote_parent_scope)
        ui._close_remote_parent()
        await asyncio.sleep(.15)  # Actual socket writer sends per-view revoke while worker remains held.
        assert ui._connection_ready and not connection.done()
        assert not server._orchestrator.try_reserve_group('main')
        release.set()
        # Reservation is separately tracked: probe only once cleanup has settled.
        await asyncio.sleep(.15)
        await wait(lambda: ui._remote_parent_panel is None)
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
        assert not ui._editor.get_text() and not ui._composer.items and not adapter.calls
        # Old view cannot re-admit even with live connection; a new view works.
        ui._queue_request({'type': 'parent_draft_control', **{key: value for key, value in scope.items()
                          if key != 'session_revision'}, 'request_id': 'old-view', 'action': 'catalog'})
        ui._parent_draft_command('remote')
        await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
        assert ui._remote_parent_panel.rows[0]['id'] == saved.id
    finally:
        release.set()
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_parent_view_cancel_only_retires_matching_view_and_preserves_sibling(tmp_path):
    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    events = []
    async def write(row):
        events.append(row)
    controller = ParentDraftConnection(server._orchestrator, Path(config.data_dir) / 'saved-parent-drafts', write)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    base = {'group_id': 'main', 'session_id': session.meta.session_id,
            'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation}
    async def prepare(view, rid):
        await controller.request({**base, 'view': view, 'request_id': rid, 'action': 'save_prepare',
                                  'text': 'PRIVATE', 'composer': composer_receipt(ChildDraft('PRIVATE'), 1)})
        await asyncio.gather(*tuple(controller.jobs.values()))
        await asyncio.sleep(0)
        return events[-1]['review_id']
    try:
        one = await prepare('a' * 32, 'one')
        two = await prepare('b' * 32, 'two')
        await controller.cancel_view({**base, 'view': 'a' * 32, 'request_id': 'foreign',
                                      'parent_draft_owner': 'foreign'})
        assert one in controller.reviews and two in controller.reviews
        await controller.cancel_view({**base, 'view': 'a' * 32, 'request_id': 'cancel'})
        await asyncio.gather(*tuple(controller.view_cleanups))
        assert one not in controller.reviews and one not in controller.review_timers
        assert two in controller.reviews and two in controller.review_timers
        await controller.request({**base, 'view': 'a' * 32, 'request_id': 'late', 'action': 'catalog'})
        assert not events[-1]['success'] and not adapter.calls
    finally:
        await controller.close()
        await server.stop()


async def test_parent_view_cancel_settles_held_upload_and_retires_its_bytes(tmp_path, monkeypatch):
    import base64
    import hashlib
    import threading

    from xdog.claw.channels.tui.image_upload import ImageUploads
    from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
    entered, release = threading.Event(), threading.Event()
    original = ImageUploads.chunk
    def held(self, packet):
        entered.set()
        assert release.wait(3)
        return original(self, packet)
    monkeypatch.setattr(ImageUploads, 'chunk', held)
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    events = []
    async def write(row):
        events.append(row)
    controller = ParentDraftConnection(server._orchestrator, Path(config.data_dir) / 'saved-parent-drafts', write)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_images': 1,
                          'parent_saved_save': 1, 'parent_saved_uploads': 1})
    scope = {'group_id': 'main', 'session_id': session.meta.session_id, 'view': 'a' * 32,
             'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation}
    try:
        await controller.request({**scope, 'action': 'upload_chunk', 'request_id': 'held', 'id': 'image', 'index': 0,
            'mime_type': 'image/png', 'data': base64.b64encode(PNG).decode(), 'final': True,
            'sha256': hashlib.sha256(PNG).hexdigest()})
        assert await asyncio.to_thread(entered.wait, 2)
        await controller.cancel_view({**scope, 'request_id': 'cancel'})
        await controller.cancel_view({**scope, 'request_id': 'repeat-cancel'})
        await asyncio.sleep(.01)
        assert len(controller.view_cleanups) == 1 and controller.upload_jobs
        assert not server._orchestrator.try_reserve_group('main')
        release.set()
        await asyncio.gather(*tuple(controller.view_cleanups))
        assert not controller.upload_jobs and not controller.jobs
        assert not controller.uploads.contains('image') and not controller.upload_owners
        assert not controller.upload_timers and not adapter.calls
        assert all(row.get('success') is not True for row in events)
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
    finally:
        release.set()
        await controller.close()
        await server.stop()


@pytest.mark.parametrize('end', ['close', 'reconnect', 'quit'])
async def test_chatapp_held_client_decode_retains_spool_then_discards_late_private_result(tmp_path, monkeypatch, end):
    import threading

    from xdog.claw.channels.tui.tui_app import ChatApp
    entered, release = threading.Event(), threading.Event()
    original = ParentDraftReceiver.finish
    captured, completions = [], []
    def held(self, key, result):
        captured.extend(self.pending[key].files)
        completions.append(dict(result))
        entered.set()
        assert release.wait(4)
        return original(self, key, result)
    monkeypatch.setattr(ParentDraftReceiver, 'finish', held)
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    saved = store.save(encode_draft(ChildDraft('PRIVATE LATE', images=(attachment(PNG, 'private.png'),)),
                                  label='held', scope=session.meta.session_id, action='parent', slot=''))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_drafts') == 1)
        ui._parent_draft_command('remote')
        await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
        panel = ui._remote_parent_panel
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        await wait(entered.is_set)
        receiver = ui._remote_parent_receiver
        worker = ui._remote_parent_worker
        assert captured and all(not stream.closed for stream in captured)
        assert len(completions) == 1
        ui._handle_response(completions[0])
        await asyncio.sleep(.01)
        assert ui._remote_parent_worker is worker and len(completions) == 1
        if end == 'close':
            ui._close_remote_parent()
        elif end == 'quit':
            ui._request_exit()
            assert not ui._exit_requested and ui._draft_quit_failures is not None
        else:
            ui._handle_response({'type': '_connection_opened', 'generation': ui._connection_generation + 1})
        assert worker.is_alive() and all(not stream.closed for stream in captured)
        assert ui._remote_parent_panel is None and ui._remote_parent_decode_cancel.is_set()
        release.set()
        assert await asyncio.to_thread(lambda: (worker.join(3), not worker.is_alive())[1])
        await wait(lambda: not receiver.pending)
        assert receiver.closed and all(stream.closed for stream in captured)
        if end == 'quit':
            await wait(lambda: ui._exit_requested)
        assert not ui._editor.get_text() and not ui._composer.items and not adapter.calls
        assert store.load(saved.id).sha256 == saved.sha256 and not session.agent.messages
    finally:
        release.set()
        if ui._remote_parent_worker is not None:
            await asyncio.to_thread(ui._remote_parent_worker.join, 3)
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('window', ['read', 'adopt'])
async def test_actual_chatapp_remote_recovery_retention_capacity_cannot_be_exceeded(tmp_path, monkeypatch, window):
    from xdog.claw.channels.tui.tui_app import ChatApp
    server, config, runtime, session, adapter, _ = await setup(tmp_path)
    Path(config.data_dir).chmod(0o700)
    store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
    saved = store.save(encode_draft(ChildDraft('PRIVATE CAPACITY', images=(attachment(PNG, 'capacity.png'),)),
                                  label='capacity', scope=session.meta.session_id, action='parent', slot=''))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._remote_parent_caps.get('parent_saved_drafts') == 1)
        ui._parent_draft_command('remote')
        await wait(lambda: ui._remote_parent_panel is not None and not ui._remote_parent_panel.busy)
        panel = ui._remote_parent_panel
        panel.render(80)
        if window == 'read':
            monkeypatch.setattr(ui, '_retained_image_bytes', lambda: 40 * 1024 * 1024)
            with pytest.raises(ValueError, match='capacity'):
                panel.handle_input(KeyEvent('enter'))
            assert not ui._remote_parent_receiver.pending
        else:
            panel.handle_input(KeyEvent('enter'))
            await wait(lambda: panel.review is not None)
            paint(panel.review)
            monkeypatch.setattr(ui, '_retained_image_bytes', lambda: 40 * 1024 * 1024)
            with pytest.raises(ValueError, match='capacity'):
                panel.review.handle_input(KeyEvent('enter'))
        assert not ui._editor.get_text() and not ui._composer.items
        assert not adapter.calls and not session.agent.messages
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
