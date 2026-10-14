"""Both actual native owners save/reload/discard child drafts without model or permission adoption."""
from __future__ import annotations

import asyncio
import base64
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.keys import KeyEvent

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def make(tmp_path, kind, monkeypatch):
    calls = []
    session = None
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=store.create_session(model='offline/model', working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        tmp_path.chmod(0o700)
        import xdog.claw.channels.tui.tui_app as module
        monkeypatch.setattr(module, 'get_data_dir', lambda: tmp_path)
        ui = ChatApp(str(tmp_path / 'not-running.sock'))
        ui._connection_ready = ui._children_supported = True
        ui._gateway_session_id = 'owned-session'
    return ui, session, calls


async def wait(ui, predicate):
    async with asyncio.timeout(8):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(0.003)


def review(browser, key):
    browser.render(100)
    browser.handle_input(KeyEvent(key))
    browser.handle_input(KeyEvent('enter'))
    assert browser._review is not None  # Unpainted Enter cannot commit.
    for _ in range(60):
        browser.render(100)
        if browser._visible:
            browser.handle_input(KeyEvent('enter'))
            return
        browser.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Full durable-draft review never became painted')


async def close(ui, session):
    await wait(ui, lambda: not ui._child_draft_io.busy)
    ui._request_exit()
    if session is not None:
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize('kind', ['coding'])
@pytest.mark.parametrize('size', [68])
async def test_reviewed_save_fresh_owner_reload_copy_and_disk_discard(
    tmp_path, kind, size, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    body = base64.b64decode(PNG) + b'\0' * (size - 68)
    original = ChildDraft('SAVED UNSENT /quit', 'inherit', {'model': 'old/model', 'tools': ['bash']},
                          (attachment(body, 'saved.png'),), False, 3)
    ui._child_drafts['original-context'] = {('steer', 'child'): original}
    ui._child_draft_labels['original-context'] = str(tmp_path) + ' · original session'
    ui._editor.set_text('PARENT TEXT')
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel.open_drafts()
        review(panel.draft_panel, 's')
        await wait(ui, lambda: not ui._child_draft_io.busy)
        saved = ui._durable_child_store().list()
        assert len(saved) == 1 and saved[0].draft.images[0].data == body
        assert ui._child_drafts['original-context'][('steer', 'child')] is original
        assert ui._editor.get_text() == 'PARENT TEXT' and not calls
    finally:
        await close(ui, session)
    # A NEW actual UI owner has no memory cache from the original process owner.
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    ui._editor.set_text('NEW PARENT DRAFT')
    try:
        assert not ui._child_drafts
        if kind == 'coding':
            ui._handle_slash_command('agents', 'saved')
        else:
            ui._agents_command('saved')
        await wait(ui, lambda: not ui._child_draft_io.busy)
        panel = ui._child_panel
        assert panel._mode == 'drafts' and len(ui._saved_child_drafts) == 1
        row = panel.draft_panel.source()[0]
        assert not row.current and row.label.startswith('SAVED PRIVATE')
        review(panel.draft_panel, 'enter')
        assert panel._mode == 'form' and panel._input.get_text() == original.text
        assert panel._access == 'read-only' and panel._config == {'inherit_context': False}
        assert panel.composer.items and next(iter(panel.composer.items.values())).data == body
        assert ui._editor.get_text() == 'NEW PARENT DRAFT' and not calls
        panel.open_drafts()
        browser = panel.draft_panel
        browser.render(100)
        browser._selected = next(row.id for row in browser.source() if row.scope.startswith('saved-draft:'))
        review(browser, 'x')
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert not ui._durable_child_store().list() and not ui._saved_child_drafts
        # Deleting disk snapshot cannot erase the independently edited local copy.
        assert any(draft.text == original.text for cache in ui._child_drafts.values() for draft in cache.values())
        assert ui._editor.get_text() == 'NEW PARENT DRAFT' and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_owned_disk_worker_holds_quit_and_rejects_repeat_without_touching_parent(tmp_path, kind, monkeypatch):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    started, release = threading.Event(), threading.Event()
    from xdog.tui.child_draft_store import ChildDraftStore
    original = ChildDraftStore.save
    def held(self, data):
        started.set()
        assert release.wait(5)
        return original(self, data)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    ui._child_drafts['scope'] = {('start', ''): ChildDraft('UNSENT TEXT')}
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel.open_drafts()
        review(panel.draft_panel, 's')
        assert await asyncio.to_thread(started.wait, 3)
        ui._request_exit()
        assert not ui._exit_requested
        with pytest.raises(ValueError, match='still settling'):
            ui._load_saved_child_drafts()
        assert not calls
        release.set()
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert len(ui._durable_child_store().list()) == 1
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_delayed_saved_load_cannot_adopt_into_replacement_view(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    store = ChildDraftStore((tmp_path / 'sessions' if kind == 'coding' else tmp_path) / 'saved-child-drafts')
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('OLD SAVED INPUT'), label='old source',
                                  scope='old', action='start', slot=''))
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.list
    def held(self):
        entered.set()
        assert release.wait(5)
        return original(self)
    monkeypatch.setattr(ChildDraftStore, 'list', held)
    try:
        ui._editor.set_text('REPLACEMENT PARENT')
        ui._load_saved_child_drafts()
        assert await asyncio.to_thread(entered.wait, 3)
        old_owner = ui._child_panel.owner_token
        ui._close_children()
        ui._open_children()
        assert ui._child_panel.owner_token != old_owner
        release.set()
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert not ui._saved_child_drafts
        assert not any(scope.startswith('saved-draft:') for scope in ui._child_drafts)
        assert ui._editor.get_text() == 'REPLACEMENT PARENT' and not calls
        assert store.load(saved.id).draft.text == 'OLD SAVED INPUT'
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_failed_saved_snapshot_keeps_local_editable_source_without_replay(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    original = ChildDraft('KEEP SOURCE /quit')
    ui._child_drafts['owned'] = {('start', ''): original}
    def fail(*args):
        raise OSError('PRIVATE-SOURCE-SHOULD-NOT-BE-LOGGED')
    monkeypatch.setattr(ChildDraftStore, 'save', fail)
    try:
        ui._editor.set_text('PARENT DRAFT')
        ui._open_children()
        ui._child_panel.publish(())
        ui._child_panel.open_drafts()
        review(ui._child_panel.draft_panel, 's')
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert ui._child_drafts['owned'][('start', '')] is original
        assert ui._editor.get_text() == 'PARENT DRAFT' and not calls
        assert 'OSError' in ui._child_panel._error and 'PRIVATE-SOURCE' not in ui._child_panel._error
        assert not ui._durable_child_store().list()
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_metadata_catalog_then_one_id_load_adopts_only_selected_snapshot(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ui._durable_child_store()
    store.root.parent.mkdir(exist_ok=True)
    image = attachment(base64.b64decode(PNG), 'catalog.png')
    first = store.save(encode_draft(ChildDraft('FIRST SAVED TASK', images=(image,)),
                                   label='first source', scope='one', action='start', slot=''))
    second = store.save(encode_draft(ChildDraft('SECOND SAVED TASK', images=(image,)),
                                    label='second source', scope='two', action='start', slot=''))
    ui._editor.set_text('PARENT CATALOG DRAFT')
    try:
        if kind == 'coding':
            ui._handle_slash_command('agents', 'saved list')
        else:
            ui._agents_command('saved list')
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert not ui._saved_child_drafts
        assert not any(scope.startswith('saved-draft:') for scope in ui._child_drafts)
        assert ui._editor.get_text() == 'PARENT CATALOG DRAFT' and not calls
        if kind == 'coding':
            ui._handle_slash_command('agents', 'saved ' + second.id)
        else:
            ui._agents_command('saved ' + second.id)
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert set(ui._saved_child_drafts) == {second.id}
        assert ui._saved_child_drafts[second.id].draft.text == 'SECOND SAVED TASK'
        assert ui._saved_child_drafts[second.id].draft.images[0].data == image.data
        assert ui._saved_child_drafts[second.id].draft.access == 'read-only'
        assert store.load(first.id).draft.text == 'FIRST SAVED TASK'
        assert ui._editor.get_text() == 'PARENT CATALOG DRAFT' and not calls
        with pytest.raises(ValueError, match='SNAPSHOT_ID'):
            ui._load_saved_child_drafts('../not-an-id')
        assert not ui._child_draft_io.busy
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_opt_in_autosave_persists_unsent_child_edits_without_parent_or_execution(tmp_path, kind, monkeypatch):
    import xdog.tui.child_draft_autosave as module
    monkeypatch.setattr(module, 'DEBOUNCE', 0)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    ui._editor.set_text('UNTOUCHED PARENT')
    image = attachment(base64.b64decode(PNG), 'autosaved.png')
    try:
        assert not ui._child_autosave.enabled
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('UNSENT AUTOSAVED TASK')
        panel.composer.restore((image,))
        ui._poll()
        assert not ui._durable_child_store().list()
        if kind == 'coding':
            ui._handle_slash_command('agents', 'autosave on')
        else:
            ui._agents_command('autosave on')
        await wait(ui, lambda: bool(ui._child_autosave._written) and not ui._child_autosave.busy)
        saved = ui._durable_child_store().list()
        assert len(saved) == 1 and saved[0].draft.text == 'UNSENT AUTOSAVED TASK'
        assert saved[0].draft.images[0].data == image.data
        panel._input.set_text('EDITED UNSENT TASK')
        await wait(ui, lambda: any(value[0][0] == 'EDITED UNSENT TASK'
                                  for value in ui._child_autosave._written.values()) and not ui._child_autosave.busy)
        edited = ui._durable_child_store().list()
        assert len(edited) == 1 and edited[0].id == saved[0].id
        assert edited[0].draft.text == 'EDITED UNSENT TASK'
        assert edited[0].draft.access == 'read-only' and edited[0].draft.config == {'inherit_context': False}
        ui._child_autosave_command('off')
        panel._input.set_text('UNSAVED AFTER OFF')
        ui._poll()
        assert ui._durable_child_store().load(edited[0].id).draft.text == 'EDITED UNSENT TASK'
        assert ui._editor.get_text() == 'UNTOUCHED PARENT' and not calls
    finally:
        ui._child_autosave.set_enabled(False)
        await wait(ui, lambda: not ui._child_autosave.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_autosave_external_change_disables_retry_without_overwriting_or_losing_local_draft(
    tmp_path, kind, monkeypatch,
):
    import xdog.tui.child_draft_autosave as module
    monkeypatch.setattr(module, 'DEBOUNCE', 0)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('AUTOSAVED ORIGINAL')
        ui._child_autosave_command('on')
        await wait(ui, lambda: bool(ui._child_autosave._written) and not ui._child_autosave.busy)
        saved = ui._durable_child_store().list()[0]
        path = ui._durable_child_store().root / (saved.id + '.json')
        before = path.read_bytes() + b' '  # Same valid JSON, different exact storage digest.
        path.write_bytes(before)
        panel._input.set_text('KEEP LOCAL EDIT ON FAILURE')
        await wait(ui, lambda: not ui._child_autosave.enabled and not ui._child_autosave.busy)
        assert path.read_bytes() == before
        assert panel._input.get_text() == 'KEEP LOCAL EDIT ON FAILURE' and not calls
        ui._poll()
        assert path.read_bytes() == before  # No periodic automatic retry.
    finally:
        ui._child_autosave.set_enabled(False)
        await wait(ui, lambda: not ui._child_autosave.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_deferred_quit_preserves_input_services_until_actual_exit(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, data, **kwargs)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    cleanup = []
    original_close = ui._argument_feed.close
    def close_feed():
        cleanup.append('argument-feed')
        original_close()
    monkeypatch.setattr(ui._argument_feed, 'close', close_feed)
    original_sync = ui._custom_presentation.sync
    def sync(*args):
        cleanup.append('custom-presentation')
        return original_sync(*args)
    monkeypatch.setattr(ui._custom_presentation, 'sync', sync)
    if kind == 'claw':
        original_cancel = ui._cancel_name_read
        def cancel_name(*args, **kwargs):
            cleanup.append('name-read')
            return original_cancel(*args, **kwargs)
        monkeypatch.setattr(ui, '_cancel_name_read', cancel_name)
    ui._child_drafts['scope'] = {('start', ''): ChildDraft('UNSENT QUIT TEST')}
    try:
        ui._open_children()
        ui._child_panel.publish(())
        ui._child_panel.open_drafts()
        review(ui._child_panel.draft_panel, 's')
        assert await asyncio.to_thread(entered.wait, 3)
        cleanup.clear()
        ui._request_exit()
        ui._request_exit()
        assert not ui._exit_requested and not cleanup
        assert not ui._argument_feed._closed and not calls
        release.set()
        await wait(ui, lambda: not ui._child_draft_io.busy)
        ui._request_exit()
        assert ui._exit_requested and ui._argument_feed._closed
        assert 'argument-feed' in cleanup and 'custom-presentation' in cleanup
        if kind == 'claw':
            assert 'name-read' in cleanup
        settled_cleanup = list(cleanup)
        ui._request_exit()
        assert cleanup == settled_cleanup  # Repeated actual exit does not rerun teardown.
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_saved_reload_counts_images_still_owned_by_reviewed_editable_copy(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ui._durable_child_store()
    store.root.parent.mkdir(exist_ok=True)
    body = base64.b64decode(PNG) + b'\0' * (1024 * 1024 - 68)
    image = attachment(body, 'shared.png')
    saved = store.save(encode_draft(ChildDraft('SHARED SAVED IMAGE', images=(image,)),
                                   label='source', scope='old', action='start', slot=''))
    ui._editor.set_text('PARENT UNTOUCHED')
    try:
        ui._load_saved_child_drafts(saved.id)
        await wait(ui, lambda: not ui._child_draft_io.busy)
        panel = ui._child_panel
        original_record = ui._saved_child_drafts[saved.id]
        review(panel.draft_panel, 'enter')
        assert panel._mode == 'form'
        assert next(iter(panel.composer.items.values())).data is original_record.draft.images[0].data
        panel.open_drafts()  # Reviewed editable copy stays in its separate local slot.
        assert ui._child_draft_bytes() == len(body)
        assert ui._child_draft_bytes(exclude_saved=True) == len(body)
        original_retained = ui._retained_image_bytes
        # Simulate 39 MiB of other local retention without allocating unrelated
        # payloads. The real shared saved/copy image is 1 MiB, so re-decoding a
        # new 1 MiB catalog image would exceed the actual application's 40 MiB cap.
        monkeypatch.setattr(ui, '_retained_image_bytes', lambda: original_retained() + 39 * 1024 * 1024)
        ui._load_saved_child_drafts(saved.id)
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert ui._saved_child_drafts[saved.id] is original_record
        assert any(draft.images and draft.images[0].data is original_record.draft.images[0].data
                   for scope, rows in ui._child_drafts.items() if not scope.startswith('saved-draft:')
                   for draft in rows.values())
        assert ui._editor.get_text() == 'PARENT UNTOUCHED' and not calls
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_saved_reload_releases_exclusive_catalog_images_without_double_counting(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ui._durable_child_store()
    store.root.parent.mkdir(exist_ok=True)
    body = base64.b64decode(PNG) + b'\0' * (1024 * 1024 - 68)
    saved = store.save(encode_draft(ChildDraft('EXCLUSIVE SAVED IMAGE', images=(attachment(body, 'only.png'),)),
                                   label='source', scope='old', action='start', slot=''))
    try:
        ui._load_saved_child_drafts(saved.id)
        await wait(ui, lambda: not ui._child_draft_io.busy)
        old_record = ui._saved_child_drafts[saved.id]
        assert ui._child_draft_bytes(exclude_saved=True) == 0
        original_retained = ui._retained_image_bytes
        monkeypatch.setattr(ui, '_retained_image_bytes', lambda: original_retained() + 39 * 1024 * 1024)
        ui._load_saved_child_drafts(saved.id)
        await wait(ui, lambda: not ui._child_draft_io.busy)
        assert ui._saved_child_drafts[saved.id] is not old_record
        assert ui._saved_child_drafts[saved.id].draft.images[0].data == body
        assert ui._child_draft_bytes() == len(body) and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('changed', [False, True])
async def test_parent_autosave_and_reviewed_recovery_never_send_or_overwrite_composer(
    tmp_path, kind, changed, monkeypatch,
):
    import xdog.tui.child_draft_autosave as module
    monkeypatch.setattr(module, 'DEBOUNCE', 0)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    image = attachment(base64.b64decode(PNG), 'parent.png')
    try:
        ui._editor.set_text('UNSENT PARENT /quit\nsecond line')
        ui._composer.restore((image,))
        assert not ui._parent_drafts.autosave.enabled
        ui._parent_draft_command('autosave on')
        await wait(ui, lambda: bool(ui._parent_drafts.autosave._written) and not ui._parent_drafts.busy)
        from xdog.tui.child_draft_store import ChildDraftStore
        store = ChildDraftStore(ui._parent_drafts.root())
        saved = store.list()[0]
        assert saved.source_action == 'parent' and saved.draft.images[0].data == image.data
        assert saved.draft.text == ui._editor.get_text() and not calls
        ui._parent_draft_command('autosave off')
        ui._editor.set_text('')
        ui._composer.take()
        ui._parent_draft_command('saved ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        panel = ui._child_panel
        assert panel.draft_panel.parent and panel._mode == 'drafts'
        assert ui._parent_saved_bytes() == len(image.data)
        browser = panel.draft_panel
        browser.render(100)
        browser.handle_input(KeyEvent('enter'))
        if changed:
            ui._editor.set_text('NEW TEXT MUST SURVIVE')
        for _ in range(60):
            browser.render(100)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        if changed:
            assert ui._editor.get_text() == 'NEW TEXT MUST SURVIVE' and not ui._composer.items
            assert 'changed' in browser._error
        else:
            assert ui._editor.get_text() == saved.draft.text
            assert next(iter(ui._composer.items.values())).data == image.data
            assert ui._parent_saved_bytes() == 0 and ui._child_panel is None
        assert store.load(saved.id).sha256 == saved.sha256 and not calls
        if session is not None:
            assert not session.agent.messages and session.permissions.mode == 'deny'
    finally:
        ui._parent_drafts.autosave.set_enabled(False)
        await wait(ui, lambda: not ui._parent_drafts.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_parent_saved_discard_keeps_current_composer_and_requires_exact_review(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('OLD PARENT SNAPSHOT'), label='old parent',
                                   scope='old-parent', action='parent', slot=''))
    try:
        ui._editor.set_text('CURRENT PARENT MUST SURVIVE')
        ui._parent_draft_command('saved ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        browser = ui._child_panel.draft_panel
        review(browser, 'x')
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert not store.catalog() and not ui._parent_drafts.saved
        assert ui._editor.get_text() == 'CURRENT PARENT MUST SURVIVE' and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_closed_parent_read_cannot_reopen_or_adopt_into_replacement_view(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('OLD PARENT READ'), label='old parent',
                                   scope='old', action='parent', slot=''))
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.load
    def held(self, key):
        entered.set()
        assert release.wait(5)
        return original(self, key)
    monkeypatch.setattr(ChildDraftStore, 'load', held)
    try:
        ui._parent_draft_command('saved ' + saved.id)
        assert await asyncio.to_thread(entered.wait, 3)
        prior = ui._child_panel.owner_token
        ui._close_children()
        ui._open_children()
        replacement = ui._child_panel
        assert replacement.owner_token != prior
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert ui._child_panel is replacement and replacement._mode == 'list'
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        assert not ui._editor.get_text() and not ui._composer.items and not calls
        assert original(store, saved.id).draft.text == 'OLD PARENT READ'
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_parent_recovery_back_releases_catalog_and_read_snapshot_without_disk_delete(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    image = attachment(base64.b64decode(PNG), 'transient-parent.png')
    saved = store.save(encode_draft(ChildDraft('PARENT TRANSIENT', images=(image,)), label='source',
                                   scope='old', action='parent', slot=''))
    try:
        ui._parent_draft_command('saved ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        panel = ui._child_panel
        assert panel.draft_panel.parent and ui._parent_saved_bytes() == len(image.data)
        panel.draft_panel.handle_input(KeyEvent('escape'))
        assert panel._mode == 'list' and ui._parent_saved_bytes() == 0
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        assert store.load(saved.id).sha256 == saved.sha256 and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('transition', ['form', 'back-roundtrip', 'draft-browser'])
async def test_parent_read_cannot_take_focus_after_navigation_within_same_panel(
    tmp_path, kind, transition, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('LATE PARENT TASK'), label='source',
                                   scope='old', action='parent', slot=''))
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.load
    def held(self, key):
        entered.set()
        assert release.wait(5)
        return original(self, key)
    monkeypatch.setattr(ChildDraftStore, 'load', held)
    try:
        ui._parent_draft_command('saved ' + saved.id)
        assert await asyncio.to_thread(entered.wait, 3)
        panel = ui._child_panel
        if transition == 'form':
            panel._form('start')
            panel._input.set_text('KEEP NEW CHILD EDITOR')
        elif transition == 'back-roundtrip':
            panel._form('start')
            panel.back()  # Same roster mode again, but its former read lease is revoked.
        else:
            panel.open_drafts()
        mode, browser = panel._mode, panel.draft_panel
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert ui._child_panel is panel and panel._mode == mode and panel.draft_panel is browser
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        if transition == 'form':
            assert panel._input.get_text() == 'KEEP NEW CHILD EDITOR'
        assert not ui._editor.get_text() and not calls
        assert original(store, saved.id).sha256 == saved.sha256
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_old_parent_browser_callback_cannot_copy_or_discard_after_same_panel_view_change(
    tmp_path, kind, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('DO NOT ADOPT OLD REVIEW'), label='source',
                                   scope='old', action='parent', slot=''))
    try:
        ui._parent_draft_command('saved ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        panel = ui._child_panel
        browser = panel.draft_panel
        entry = browser.source()[0]
        panel._form('start')
        panel._input.set_text('CURRENT CHILD DRAFT')
        for action in ('copy', 'discard'):
            with pytest.raises(ValueError, match='view changed'):
                browser.act(action, entry)
        assert not ui._parent_drafts.io.busy and not ui._editor.get_text()
        assert panel._input.get_text() == 'CURRENT CHILD DRAFT' and not calls
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('changed', [False, True])
async def test_explicit_parent_save_reviews_exact_composer_without_autosave_or_submission(
    tmp_path, kind, changed, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore
    from xdog.tui.keys import KeyEventType
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    image = attachment(base64.b64decode(PNG), 'explicit-parent.png')
    ui._editor.set_text('EXACT PARENT /quit\nDO NOT SEND')
    ui._composer.restore((image,))
    before = ui._parent_draft_snapshot()
    store = ChildDraftStore(ui._parent_drafts.root())
    try:
        ui._handle_global_input(KeyEvent('s', ctrl=True, alt=True))
        panel = ui._child_panel
        browser = panel.draft_panel
        assert browser.save_only and browser._review[0] == 'save'
        browser.handle_input(KeyEvent('enter'))
        browser.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        browser.handle_paste('\n\n')
        assert not ui._parent_drafts.io.busy and not store.list()
        if changed:
            ui._editor.set_text('NEW PARENT TEXT PRESERVED')
        for _ in range(60):
            browser.render(100)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        if changed:
            assert not store.list() and 'changed' in browser._error
            assert ui._editor.get_text() == 'NEW PARENT TEXT PRESERVED'
        else:
            saved = store.list()
            assert len(saved) == 1 and saved[0].source_action == 'parent'
            assert saved[0].draft.text == before.text and saved[0].draft.images[0].data == image.data
            assert ui._parent_draft_snapshot() == before
            assert not ui._parent_drafts.autosave.enabled
            with pytest.raises(ValueError):
                browser.act('save', browser.source()[0])  # No repeated grant after admitted save.
            assert len(store.list()) == 1
        assert not calls
        if session is not None:
            assert not session.messages and session.permissions.mode == 'deny'
    finally:
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('modal', ['dialog', 'queued-dialog', 'output'])
async def test_parent_save_shortcut_and_stale_review_respect_modal_ownership(tmp_path, kind, modal, monkeypatch):
    from xdog.tui.components.extension_dialog import ExtensionDialog
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    ui._editor.set_text('DO NOT STEAL MODAL FOCUS')
    before = ui._parent_draft_snapshot()
    try:
        ui._parent_draft_command('save')
        browser = ui._child_panel.draft_panel
        entry = browser.source()[0]
        if modal == 'dialog':
            dialog = ExtensionDialog('owned', {'id': 'question', 'kind': 'input', 'title': 'Owned question'},
                                     lambda _: None)
            ui._extension_dialog_panel = dialog
        elif modal == 'queued-dialog':
            ui._extension_dialog_queue.append({'id': 'owned-question'})
        else:
            ui._outputs._job = 'owned-output-operation'
        # Even stale editor-focused flags cannot override the actual modal policy.
        ui._editor.focused = True
        assert not ui._parent_draft_ui_available()
        ui._handle_global_input(KeyEvent('s', ctrl=True, alt=True))
        assert ui._child_panel.draft_panel is browser
        with pytest.raises(ValueError, match='view changed'):
            browser.act('save', entry)
        assert not ui._parent_drafts.io.busy and ui._parent_draft_snapshot() == before
        assert not calls
    finally:
        ui._extension_dialog_panel = None
        ui._extension_dialog_queue.clear()
        if modal == 'output':
            ui._outputs._job = None
        await close(ui, session)


async def test_coding_parent_save_respects_pending_permission_owner(tmp_path, monkeypatch):
    ui, session, calls = make(tmp_path, 'coding', monkeypatch)
    ui._editor.set_text('PRESERVE APPROVAL OWNER')
    try:
        ui._permission_prompt = object()
        ui._editor.focused = True
        assert not ui._parent_draft_ui_available()
        ui._handle_global_input(KeyEvent('s', ctrl=True, alt=True))
        assert ui._child_panel is None and not ui._parent_drafts.io.busy
        ui._parent_draft_command('save')
        assert ui._child_panel is None and ui._editor.get_text() == 'PRESERVE APPROVAL OWNER' and not calls
    finally:
        ui._permission_prompt = None
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('binding', ['f8', None])
async def test_parent_save_binding_can_be_remapped_or_disabled_without_legacy_fallback(
    tmp_path, kind, binding, monkeypatch,
):
    from xdog.tui.keys import KeyEventType
    from xdog.tui.prompt_keymap import PromptKeymap
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    ui._keymap = PromptKeymap.from_overrides({'save_draft': binding or []})
    ui._editor.set_keymap(ui._keymap)
    ui._editor.set_text('UNSENT REMAPPED PARENT')
    image = attachment(base64.b64decode(PNG), 'shortcut.png')
    ui._composer.restore((image,))
    before = ui._parent_draft_snapshot()
    try:
        ui._handle_global_input(KeyEvent('s', ctrl=True, alt=True))
        assert ui._child_panel is None and ui._parent_draft_snapshot() == before
        for event_type in (KeyEventType.REPEAT, KeyEventType.RELEASE):
            ui._handle_global_input(KeyEvent('f8', event_type=event_type))
        assert ui._child_panel is None
        ui._handle_global_input(KeyEvent('f8'))
        if binding is not None:
            browser = ui._child_panel.draft_panel
            assert browser.save_only and browser._review[0] == 'save'
            assert ui._keymap.label('save_draft') == 'F8'
        else:
            assert ui._child_panel is None
        assert ui._parent_draft_snapshot() == before and not calls
        assert not ui._parent_drafts.io.busy  # Preparing review is never actual file save.
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('modal', ['dialog', 'queued-dialog', 'output'])
async def test_parent_recovery_read_and_stale_actions_respect_modal_input_owner(tmp_path, kind, modal, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.components.extension_dialog import ExtensionDialog
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('KEEP SAVED WHILE MODAL'), label='source',
                                   scope='old', action='parent', slot=''))
    try:
        ui._parent_draft_command('saved ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        panel, browser = ui._child_panel, ui._child_panel.draft_panel
        entry = browser.source()[0]
        if modal == 'dialog':
            ui._extension_dialog_panel = ExtensionDialog('owned', {
                'id': 'question', 'kind': 'input', 'title': 'Owned question'}, lambda _: None)
        elif modal == 'queued-dialog':
            ui._extension_dialog_queue.append({'id': 'owned-question'})
        else:
            ui._outputs._job = 'owned-output'
        for action in ('copy', 'discard'):
            with pytest.raises(ValueError, match='view changed'):
                browser.act(action, entry)
        ui._parent_draft_command('saved ' + saved.id)
        assert not ui._parent_drafts.io.busy and ui._child_panel is panel and panel.draft_panel is browser
        assert not ui._editor.get_text() and not ui._composer.items and not calls
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        ui._extension_dialog_panel = None
        ui._extension_dialog_queue.clear()
        ui._outputs._job = None
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_parent_read_finishing_under_new_modal_does_not_take_focus(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.components.extension_dialog import ExtensionDialog
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('LATE MODAL READ'), label='source',
                                   scope='old', action='parent', slot=''))
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.load
    def held(self, key):
        entered.set()
        assert release.wait(5)
        return original(self, key)
    monkeypatch.setattr(ChildDraftStore, 'load', held)
    try:
        ui._parent_draft_command('saved ' + saved.id)
        assert await asyncio.to_thread(entered.wait, 3)
        panel = ui._child_panel
        dialog = ExtensionDialog('owned', {'id': 'question', 'kind': 'input', 'title': 'Owned'}, lambda _: None)
        ui._extension_dialog_panel = dialog
        # This test owns the modal directly, not an extension broker worker.
        # Keep it alive while exercising real disk-read delivery and focus guards.
        monkeypatch.setattr(ui, '_sync_extension_dialog', lambda: None)
        ui._tui.set_focus(dialog)
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        assert ui._child_panel is panel and not panel.draft_panel
        assert ui._tui._focused is dialog and not calls
        assert original(store, saved.id).sha256 == saved.sha256
    finally:
        release.set()
        ui._extension_dialog_panel = None
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('failure', [False, True])
async def test_one_quit_request_finishes_after_draft_save_or_stays_open_on_failure(
    tmp_path, kind, failure, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data, **kwargs):
        entered.set()
        assert release.wait(5)
        if failure:
            raise OSError('Owned quit-time save failure')
        return original(self, data, **kwargs)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    ui._child_drafts['scope'] = {('start', ''): ChildDraft('UNSENT QUIT SNAPSHOT')}
    try:
        ui._open_children()
        ui._child_panel.publish(())
        ui._child_panel.open_drafts()
        review(ui._child_panel.draft_panel, 's')
        assert await asyncio.to_thread(entered.wait, 3)
        ui._request_exit()  # One explicit intent; no second request after settlement.
        assert not ui._exit_requested and ui._draft_quit_failures is not None
        assert not ui._argument_feed._closed
        release.set()
        await wait(ui, lambda: not ui._child_draft_io.busy)
        if failure:
            assert not ui._exit_requested and ui._draft_quit_failures is None
            assert not ui._argument_feed._closed
            assert ui._child_drafts['scope'][('start', '')].text == 'UNSENT QUIT SNAPSHOT'
            assert not ui._durable_child_store().list()
        else:
            assert ui._exit_requested and ui._argument_feed._closed
            assert ui._durable_child_store().list()[0].draft.text == 'UNSENT QUIT SNAPSHOT'
        assert not calls
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_deferred_quit_flushes_latest_parent_edit_before_automatic_exit(tmp_path, kind, monkeypatch):
    import xdog.tui.child_draft_autosave as module
    from xdog.tui.child_draft_store import ChildDraftStore
    monkeypatch.setattr(module, 'DEBOUNCE', 0)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, data, **kwargs)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    try:
        ui._editor.set_text('FIRST PARENT SNAPSHOT')
        ui._parent_draft_command('autosave on')
        ui._poll()
        assert await asyncio.to_thread(entered.wait, 3)
        ui._request_exit()
        assert not ui._exit_requested and not ui._argument_feed._closed
        ui._editor.set_text('LATEST PARENT EDIT WHILE QUIT WAITED')
        release.set()
        await wait(ui, lambda: ui._exit_requested)
        records = ChildDraftStore(ui._parent_drafts.root()).list()
        assert len(records) == 1 and records[0].draft.text == 'LATEST PARENT EDIT WHILE QUIT WAITED'
        assert not calls and ui._draft_quit_failures is None
    finally:
        release.set()
        ui._parent_drafts.autosave.set_enabled(False)
        await wait(ui, lambda: not ui._parent_drafts.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('surface', ['parent', 'child'])
async def test_native_status_shows_pending_writing_and_latest_polled_commit(tmp_path, kind, surface, monkeypatch):
    import xdog.tui.child_draft_autosave as module
    from xdog.tui.child_draft_store import ChildDraftStore
    monkeypatch.setattr(module, 'DEBOUNCE', 60)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(store, data, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(store, data, **kwargs)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    owner = ui._parent_drafts.autosave if surface == 'parent' else ui._child_autosave
    def edit(text):
        if surface == 'parent':
            ui._editor.set_text(text)
        else:
            ui._child_drafts['source'] = {('start', ''): ChildDraft(text)}
            ui._child_draft_labels['source'] = 'source'
    try:
        edit('FIRST UNSENT')
        if surface == 'parent':
            ui._parent_draft_command('autosave on')
        else:
            ui._child_autosave_command('on')
        ui._poll()
        assert f'{surface} draft:pending' in ui._footer.render(100)[0]
        if surface == 'parent':
            ui._parent_drafts.poll(lambda: None, flush=True)
        else:
            ui._poll_child_autosave(flush=True)
        assert await asyncio.to_thread(entered.wait, 3)
        ui._poll()
        assert f'{surface} draft:writing' in ui._footer.render(100)[0]
        edit('LATEST NOT YET SAVED')
        ui._poll()
        release.set()
        await wait(ui, lambda: not owner.busy)
        assert f'{surface} draft:pending' in ui._footer.render(100)[0]
        if surface == 'parent':
            ui._parent_drafts.poll(lambda: None, flush=True)
        else:
            ui._poll_child_autosave(flush=True)
        await wait(ui, lambda: owner.durability == 'committed')
        assert f'{surface} draft:committed' in ui._footer.render(100)[0]
        assert not calls
    finally:
        release.set()
        owner.set_enabled(False)
        await wait(ui, lambda: not owner.busy)
        await close(ui, session)
