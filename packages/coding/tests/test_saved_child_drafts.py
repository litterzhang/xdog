"""Native transient child recovery/refused disk UI, plus retained primary durability safeguards."""
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
            tool_repo=None, bash=None, permissions=PermissionManager('deny'))
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
    await wait(ui, lambda: not ui._parent_drafts.busy)
    ui._request_exit()
    if not ui._exit_requested:
        await wait(ui, lambda: not ui._parent_drafts.busy)
        ui._request_exit()
    if session is not None:
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        session.dispose()


def saved_child_fixture(ui, *, with_image=True):
    """Existing data is fixture-owned, never loaded/deleted through the removed UI."""
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    store = ChildDraftStore(ui._parent_drafts.root().with_name('saved-child-drafts'))
    store.root.parent.mkdir(exist_ok=True)
    image = attachment(base64.b64decode(PNG), 'old-snapshot.png')
    saved = store.save(encode_draft(ChildDraft('OLD UNSENT /quit', 'inherit', {'tools': ['bash']},
                                             (image,) if with_image else (), cursor=3),
                                   label='stored old scope', scope='old-session', action='start', slot=''))
    return store, saved


def saved_files(store):
    return {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_mode)
            for path in store.root.iterdir()}


def forbid_disk(monkeypatch):
    from xdog.tui.child_draft_io import ChildDraftIO
    from xdog.tui.child_draft_store import ChildDraftStore
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        pytest.fail('Removed child draft UI admitted storage or a background disk worker')
    monkeypatch.setattr(ChildDraftIO, 'start', forbidden)
    for method in ('save', 'load', 'list', 'catalog', 'discard', 'update'):
        monkeypatch.setattr(ChildDraftStore, method, forbidden)
    return called


def agents(ui, kind, args):
    if kind == 'coding':
        ui._handle_slash_command('agents', args)
    else:
        ui._agents_command(args)


def confirm_primary_save(ui):
    browser = ui._parent_draft_panel
    for _ in range(80):
        browser.render(100)
        if browser._visible:
            browser.handle_input(KeyEvent('enter'))
            return
        browser.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Primary save review did not become fully painted')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('command', ['saved', 'saved list', 'saved ID', 'saved discard ID', 'saved ../not-an-id'])
@pytest.mark.parametrize('open_child', [False, True])
async def test_removed_child_disk_commands_refuse_immediately_without_disk_jobs_or_file_changes(
    tmp_path, kind, command, open_child, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, saved = saved_child_fixture(ui)
    before = saved_files(store)
    ui._editor.set_text('UNTOUCHED PRIMARY DRAFT')
    ui._composer.restore((attachment(base64.b64decode(PNG), 'primary.png'),))
    primary = ui._parent_draft_snapshot()
    try:
        if open_child:
            ui._open_children()
            ui._child_panel.publish(())
            ui._child_panel._form('start')
            ui._child_panel._input.set_text('KEEP UNSENT CHILD')
        panel = ui._child_panel
        actor = ui._tui._focused
        queued = ui._send_queue.qsize() if kind == 'claw' else None
        called = forbid_disk(monkeypatch)
        agents(ui, kind, command.replace('ID', saved.id))
        for _ in range(4):
            ui._poll()
            await asyncio.sleep(0)
        assert ui._child_panel is panel and ui._tui._focused is actor
        if kind == 'coding':
            assert ui._command is None and ui._command_thread is None
        else:
            assert ui._send_queue.qsize() == queued
        if panel is not None:
            assert panel._input.get_text() == 'KEEP UNSENT CHILD'
        assert ui._parent_draft_snapshot() == primary and not calls and not called
        assert saved_files(store) == before
        assert not ui._parent_drafts.busy and not ui._parent_drafts.autosave.enabled
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_current_session_child_recovery_has_no_disk_management_and_preserves_exact_editing_state(
    tmp_path, kind, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, _saved = saved_child_fixture(ui)
    before = saved_files(store)
    ui._editor.set_text('PRIMARY TEXT MUST SURVIVE')
    image = attachment(base64.b64decode(PNG), 'transient.png')
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('CURRENT UNSENT /quit\n宽 🐕')
        panel._input._cursor = 3
        panel._access = 'inherit'
        panel._config = {'label': 'Local editing settings', 'tools': []}
        panel.composer.restore((image,))
        panel.back()
        cached = ui._child_drafts[ui._child_context]['start', '']
        called = forbid_disk(monkeypatch)
        panel.open_drafts()
        browser = panel.draft_panel
        assert len(browser.source()) == 1 and browser.source()[0].current
        selected = browser.source()[0]
        for key in ('s', 'x', 'delete', 'r', '/'):
            browser.render(100)
            browser.handle_input(KeyEvent(key))
            assert browser._review is None
        for action in ('save', 'discard', 'copy'):
            with pytest.raises(ValueError):
                browser.act(action, selected)
        browser.render(100)
        browser.handle_input(KeyEvent('p'))
        assert browser._preview.item.data is image.data
        browser.handle_input(KeyEvent('escape'))
        review(browser, 'enter')
        assert panel._mode == 'form' and panel._input.get_text() == cached.text
        assert panel._input._cursor == cached.cursor and panel._access == cached.access
        assert panel._config == cached.config and panel.composer.items[image.id] is image
        assert set(ui._child_drafts[ui._child_context]) == {('start', '')}
        assert ui._editor.get_text() == 'PRIMARY TEXT MUST SURVIVE' and not calls and not called
        assert saved_files(store) == before
        if session is not None:
            assert not session.agent.messages and session.permissions.mode == 'deny'
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_new_ui_owner_cannot_reload_prior_transient_child_input_or_old_disk_snapshot(tmp_path, kind, monkeypatch):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, saved = saved_child_fixture(ui)
    before = saved_files(store)
    ui._open_children()
    panel = ui._child_panel
    panel.publish(())
    panel._form('start')
    panel._input.set_text('UNSAVED PREVIOUS OWNER')
    await close(ui, session)
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    try:
        called = forbid_disk(monkeypatch)
        agents(ui, kind, 'saved ' + saved.id)
        assert ui._child_panel is None
        ui._open_children()
        ui._child_panel.publish(())
        ui._child_panel.open_drafts()
        assert ui._child_panel.draft_panel.source() == ()
        assert not ui._child_drafts[ui._child_context] and not calls and not called
        assert saved_files(store) == before
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_child_disk_and_autosave_apis_are_absent_without_periodic_writes(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_autosave import ChildDraftAutosave
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, _saved = saved_child_fixture(ui)
    before = saved_files(store)
    ui._editor.set_text('UNTOUCHED PARENT')
    image = attachment(base64.b64decode(PNG), 'transient-only.png')
    try:
        for name in ('_child_autosave', '_poll_child_autosave', '_child_autosave_command', '_child_draft_io',
                     '_durable_child_store', '_load_saved_child_drafts', '_finish_child_draft_io',
                     '_saved_child_drafts', '_child_draft_action', '_child_draft_entries', '_child_draft_labels'):
            assert not hasattr(ui, name)
        assert isinstance(ui._parent_drafts.autosave, ChildDraftAutosave)
        assert not ui._parent_drafts.autosave.enabled
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('UNSENT TRANSIENT CHILD TASK')
        panel.composer.restore((image,))
        called = forbid_disk(monkeypatch)
        for _ in range(4):
            ui._poll()
        agents(ui, kind, 'autosave on')
        if kind == 'coding':
            await wait(ui, lambda: ui._command is None)
        assert not ui._parent_drafts.autosave.enabled and saved_files(store) == before and not called
        assert panel._input.get_text() == 'UNSENT TRANSIENT CHILD TASK' and panel.composer.items[image.id] is image
        assert ui._editor.get_text() == 'UNTOUCHED PARENT' and not calls
        assert 'child draft:' not in ui._footer.render(120)[0]
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_child_edits_do_not_rewrite_existing_snapshot_or_external_file_changes(tmp_path, kind, monkeypatch):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, saved = saved_child_fixture(ui, with_image=False)
    path = store.root / (saved.id + '.json')
    path.write_bytes(path.read_bytes() + b' ')
    before = saved_files(store)
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('KEEP LOCAL CHILD EDIT')
        called = forbid_disk(monkeypatch)
        panel.open_drafts()
        review(panel.draft_panel, 'enter')
        for _ in range(4):
            ui._poll()
        assert saved_files(store) == before
        assert panel._input.get_text() == 'KEEP LOCAL CHILD EDIT' and not calls and not called
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_deferred_quit_preserves_input_services_until_actual_primary_write_exit(tmp_path, kind, monkeypatch):
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
    original_composer_close = ui._composer.close
    def close_composer():
        cleanup.append('composer')
        original_composer_close()
    monkeypatch.setattr(ui._composer, 'close', close_composer)
    if kind == 'claw':
        original_cancel = ui._cancel_name_read
        def cancel_name(*args, **kwargs):
            cleanup.append('name-read')
            return original_cancel(*args, **kwargs)
        monkeypatch.setattr(ui, '_cancel_name_read', cancel_name)
    ui._editor.set_text('UNSENT PRIMARY QUIT TEST')
    try:
        ui._parent_draft_command('save')
        confirm_primary_save(ui)
        assert await asyncio.to_thread(entered.wait, 3)
        cleanup.clear()
        ui._request_exit()
        ui._request_exit()
        assert not ui._exit_requested and not cleanup
        assert not ui._argument_feed._closed and not calls
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        ui._request_exit()
        assert ui._exit_requested and ui._argument_feed._closed
        assert 'argument-feed' in cleanup and 'composer' in cleanup
        if kind == 'claw':
            assert 'name-read' in cleanup
        settled_cleanup = list(cleanup)
        ui._request_exit()
        assert cleanup == settled_cleanup
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_child_transient_quit_has_no_disk_receipt_wait_or_background_task(tmp_path, kind, monkeypatch):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, _saved = saved_child_fixture(ui)
    before = saved_files(store)
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('UNSAVED CHILD STAYS TRANSIENT')
        called = forbid_disk(monkeypatch)
        ui._request_exit()
        assert ui._exit_requested and not ui._parent_drafts.busy and ui._draft_quit_failures is None
        assert ui._argument_feed._closed and not calls and not called
        assert saved_files(store) == before
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_transient_reopen_preserves_exact_image_ownership_without_duplicate_retention(
    tmp_path, kind, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    body = base64.b64decode(PNG) + b'\0' * (1024 * 1024 - 68)
    image = attachment(body, 'current-shared.png')
    ui._editor.set_text('PARENT UNTOUCHED')
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('CURRENT IMAGE INPUT')
        panel.composer.restore((image,))
        called = forbid_disk(monkeypatch)
        for _ in range(2):
            panel.open_drafts()
            review(panel.draft_panel, 'enter')
            assert panel.composer.items[image.id].data is body
            assert ui._child_draft_bytes() == len(body)
            assert set(ui._child_drafts[ui._child_context]) == {('start', '')}
        assert ui._editor.get_text() == 'PARENT UNTOUCHED' and not calls and not called
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_new_child_context_releases_only_old_cache_ownership_not_primary_images_or_files(
    tmp_path, kind, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store, _saved = saved_child_fixture(ui)
    before = saved_files(store)
    image = attachment(base64.b64decode(PNG), 'old-child.png')
    primary_image = attachment(base64.b64decode(PNG), 'primary-kept.png')
    ui._composer.restore((primary_image,))
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel._form('start')
        panel._input.set_text('OLD CONTEXT INPUT')
        panel.composer.restore((image,))
        panel.open_drafts()
        old_browser = panel.draft_panel
        old_entry = old_browser.source()[0]
        called = forbid_disk(monkeypatch)
        ui._output_epoch += 1
        ui._poll()
        assert ui._child_panel is None
        ui._open_children()
        ui._child_panel.publish(())
        ui._child_panel.open_drafts()
        assert set(ui._child_drafts) == {ui._child_context}
        assert not ui._child_panel.draft_panel.source() and ui._child_draft_bytes() == 0
        assert ui._composer.items[primary_image.id] is primary_image
        with pytest.raises(ValueError, match='owner/view changed'):
            old_browser.act('reopen', old_entry)
        assert saved_files(store) == before and not calls and not called
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
        browser = ui._parent_draft_panel
        assert browser.parent and browser.mode == 'restore' and ui._child_panel is None
        assert ui._parent_saved_bytes() == len(image.data)
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
async def test_parent_recovery_has_no_delete_catalog_or_management_controls(tmp_path, kind, monkeypatch):
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
        browser = ui._parent_draft_panel
        entry = browser.source()[0]
        for key in ('x', 'delete', 's', 'r', '/'):
            browser.render(100)
            browser.handle_input(KeyEvent(key))
            assert browser._review is None and not browser._searching
        with pytest.raises(ValueError, match='only supports explicit restore'):
            browser.act('discard', entry)
        for command in ('saved discard ' + saved.id, 'saved catalog', 'saved all', 'saved latest'):
            ui._parent_draft_command(command)
            assert not ui._parent_drafts.io.busy
        assert store.load(saved.id).sha256 == saved.sha256
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
        prior = ui._parent_draft_panel
        ui._close_parent_drafts()
        ui._open_children()
        replacement = ui._child_panel
        assert ui._parent_draft_panel is None and prior is not None
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
        browser = ui._parent_draft_panel
        assert browser.parent and ui._parent_saved_bytes() == len(image.data)
        browser.handle_input(KeyEvent('escape'))
        assert ui._parent_draft_panel is None and ui._child_panel is None and ui._parent_saved_bytes() == 0
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        assert store.load(saved.id).sha256 == saved.sha256 and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('transition', ['child-editor', 'focus-roundtrip', 'close'])
async def test_parent_read_cannot_take_focus_after_native_view_or_focus_change(
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
        prior = ui._parent_draft_panel
        if transition == 'child-editor':
            ui._open_children()
            ui._child_panel._form('start')
            ui._child_panel._input.set_text('KEEP NEW CHILD EDITOR')
        elif transition == 'focus-roundtrip':
            ui._tui.set_focus(ui._editor)
            ui._tui.set_focus(prior)  # Former read lease is revoked even after returning.
        else:
            ui._close_parent_drafts()
        owner = ui._tui._focused
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert ui._parent_draft_panel is None
        if transition != 'focus-roundtrip':
            assert ui._tui._focused is owner
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        if transition == 'child-editor':
            assert ui._child_panel._input.get_text() == 'KEEP NEW CHILD EDITOR'
        assert not ui._editor.get_text() and not calls
        assert original(store, saved.id).sha256 == saved.sha256
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_old_parent_callback_cannot_restore_or_delete_after_native_view_change(
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
        browser = ui._parent_draft_panel
        entry = browser.source()[0]
        ui._open_children()
        panel = ui._child_panel
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
        browser = ui._parent_draft_panel
        assert browser.save_only and browser._review[0] == 'save'
        reviewed_entry = browser.source()[0]
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
                browser.act('save', reviewed_entry)  # No repeated grant after admitted save.
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
        browser = ui._parent_draft_panel
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
        assert ui._parent_draft_panel is browser and ui._child_panel is None
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
        assert ui._child_panel is None and ui._parent_draft_panel is None and not ui._parent_drafts.io.busy
        ui._parent_draft_command('save')
        assert ui._child_panel is None and ui._parent_draft_panel is None
        assert ui._editor.get_text() == 'PRESERVE APPROVAL OWNER' and not calls
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
        assert ui._child_panel is None and ui._parent_draft_panel is None and ui._parent_draft_snapshot() == before
        for event_type in (KeyEventType.REPEAT, KeyEventType.RELEASE):
            ui._handle_global_input(KeyEvent('f8', event_type=event_type))
        assert ui._child_panel is None and ui._parent_draft_panel is None
        ui._handle_global_input(KeyEvent('f8'))
        if binding is not None:
            browser = ui._parent_draft_panel
            assert browser.save_only and browser._review[0] == 'save'
            assert ui._keymap.label('save_draft') == 'F8'
        else:
            assert ui._child_panel is None and ui._parent_draft_panel is None
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
        browser = ui._parent_draft_panel
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
        assert not ui._parent_drafts.io.busy and ui._parent_draft_panel is browser and ui._child_panel is None
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
        dialog = ExtensionDialog('owned', {'id': 'question', 'kind': 'input', 'title': 'Owned'}, lambda _: None)
        ui._extension_dialog_panel = dialog
        # This test owns the modal directly, not an extension broker worker.
        # Keep it alive while exercising real disk-read delivery and focus guards.
        monkeypatch.setattr(ui, '_sync_extension_dialog', lambda: None)
        ui._tui.set_focus(dialog)
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert not ui._parent_drafts.saved and ui._parent_drafts._read_snapshot is None
        assert ui._child_panel is None and ui._parent_draft_panel is None
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
    ui._editor.set_text('UNSENT QUIT SNAPSHOT')
    store = ChildDraftStore(ui._parent_drafts.root())
    try:
        ui._parent_draft_command('save')
        confirm_primary_save(ui)
        assert await asyncio.to_thread(entered.wait, 3)
        ui._request_exit()  # One explicit intent; no second request after settlement.
        assert not ui._exit_requested and ui._draft_quit_failures is not None
        assert not ui._argument_feed._closed
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        if failure:
            assert not ui._exit_requested and ui._draft_quit_failures is None
            assert not ui._argument_feed._closed
            assert ui._editor.get_text() == 'UNSENT QUIT SNAPSHOT'
            assert not store.list()
        else:
            assert ui._exit_requested and ui._argument_feed._closed
            assert store.list()[0].draft.text == 'UNSENT QUIT SNAPSHOT'
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
async def test_native_parent_status_shows_pending_writing_and_latest_polled_commit(tmp_path, kind, monkeypatch):
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
    owner = ui._parent_drafts.autosave
    def edit(text):
        ui._editor.set_text(text)
    try:
        edit('FIRST UNSENT')
        ui._parent_draft_command('autosave on')
        ui._poll()
        assert 'parent draft:pending' in ui._footer.render(100)[0]
        ui._parent_drafts.poll(lambda: None, flush=True)
        assert await asyncio.to_thread(entered.wait, 3)
        ui._poll()
        assert 'parent draft:writing' in ui._footer.render(100)[0]
        edit('LATEST NOT YET SAVED')
        ui._poll()
        release.set()
        await wait(ui, lambda: not owner.busy)
        assert 'parent draft:pending' in ui._footer.render(100)[0]
        ui._parent_drafts.poll(lambda: None, flush=True)
        await wait(ui, lambda: owner.durability == 'committed')
        assert 'parent draft:committed' in ui._footer.render(100)[0]
        assert not calls
    finally:
        release.set()
        owner.set_enabled(False)
        await wait(ui, lambda: not owner.busy)
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('command', ['restore', 'saved', 'saved list'])
async def test_primary_bounded_selector_independent_of_child_browser_and_stored_scope(
    tmp_path, kind, command, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    originals = [store.save(encode_draft(
        ChildDraft('EXACT SNAPSHOT ' + str(i) + '\n宽 🐕', access='inherit',
                   config={'tools': ['bash'], 'model': 'not-current'}, cursor=1),
        label='Stored source ' + str(i), scope='opaque foreign scope ' + str(i),
        action='steer' if i == 1 else 'parent', slot='stored-slot-' + str(i))) for i in range(3)]
    before = {path.name: path.read_bytes() for path in store.root.iterdir()}
    def no_children(*args):
        raise AssertionError('Primary recovery must not open the child browser')
    monkeypatch.setattr(ui, '_open_children', no_children)
    if kind == 'claw':
        ui._children_supported = False  # Local recovery needs no server/browser capability.
    try:
        ui._parent_draft_command(command)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        browser = ui._parent_draft_panel
        assert ui._child_panel is None and browser.mode == 'restore'
        rows = browser.source()
        assert len(rows) == 3 and {row.key[1] for row in rows} == {row.id for row in originals}
        for row in rows:
            original = next(saved for saved in originals if saved.id == row.key[1])
            assert original.source_scope in row.label and original.source_slot in row.label
            assert original.source_action == row.key[0] and original.id in row.label
        target = rows[1]
        browser.render(100)
        browser.handle_input(KeyEvent('down'))
        review(browser, 'enter')
        assert ui._editor.get_text() == target.value.text and ui._editor._cursor == len(target.value.text)
        assert ui._parent_draft_panel is None and ui._child_panel is None and not calls
        assert {path.name: path.read_bytes() for path in store.root.iterdir()} == before
        if session is not None:
            assert session.permissions.mode == 'deny' and not session.agent.messages
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_primary_save_cancel_and_image_preview_require_fresh_full_review(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    image = attachment(base64.b64decode(PNG), 'unsent-primary.png')
    ui._editor.set_text('KEEP UNSENT\n' + '宽 \n' * 10)
    ui._composer.restore((image,))
    before = ui._parent_draft_snapshot()
    store = ChildDraftStore(ui._parent_drafts.root())
    try:
        ui._parent_draft_command('save')
        browser = ui._parent_draft_panel
        for _ in range(80):
            browser.render(90)
            if browser._visible:
                break
            browser.handle_input(KeyEvent('pagedown'))
        assert browser._visible
        ui._handle_global_input(KeyEvent('p'))
        assert browser._preview.item.data == image.data and ui._child_panel is None
        ui._handle_global_input(KeyEvent('escape'))
        ui._handle_global_input(KeyEvent('enter'))
        assert not store.list() and not ui._parent_drafts.io.busy and not browser._visible
        ui._handle_global_input(KeyEvent('escape'))
        assert ui._parent_draft_panel is None and ui._tui._focused is ui._editor
        assert not store.list() and ui._parent_draft_snapshot() == before and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('failure', [False, True])
async def test_primary_manual_write_receipt_holds_quit_and_preserves_exact_unsent_source(
    tmp_path, kind, failure, monkeypatch,
):
    from xdog.tui.child_draft_store import ChildDraftStore
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data):
        entered.set()
        assert release.wait(5)
        if failure:
            raise OSError('private source must not appear in diagnostic')
        return original(self, data)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    ui._editor.set_text('PRIMARY UNSENT SOURCE')
    closed = []
    original_close = ui._composer.close
    def tracked_close():
        closed.append(True)
        original_close()
    monkeypatch.setattr(ui._composer, 'close', tracked_close)
    try:
        ui._parent_draft_command('save')
        browser = ui._parent_draft_panel
        for _ in range(80):
            browser.render(90)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        assert await asyncio.to_thread(entered.wait, 3)
        assert ui._parent_draft_panel is None and ui._child_panel is None
        ui._request_exit()
        assert not ui._exit_requested and not closed
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert ui._exit_requested is not failure and ui._editor.get_text() == 'PRIMARY UNSENT SOURCE'
        assert len(ChildDraftStore(ui._parent_drafts.root()).list()) == (0 if failure else 1)
        assert not calls
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_primary_full_store_refuses_create_without_changing_files_or_quitting(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import MAX_DRAFTS, ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    for i in range(MAX_DRAFTS):
        store.save(encode_draft(ChildDraft('OLD SNAPSHOT ' + str(i)), label='source',
                               scope='opaque old owner ' + str(i), action='parent', slot=''))
    before = {path.name: path.read_bytes() for path in store.root.iterdir()}
    ui._editor.set_text('CURRENT UNSAVED SOURCE MUST SURVIVE')
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data):
        entered.set()
        assert release.wait(5)
        return original(self, data)  # Actual fixed-capacity refusal, not a mocked write failure.
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    notices = []
    monkeypatch.setattr(ui._chat_log, 'add_system', notices.append)
    try:
        ui._parent_draft_command('save')
        browser = ui._parent_draft_panel
        for _ in range(80):
            browser.render(90)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        assert await asyncio.to_thread(entered.wait, 3)
        ui._request_exit()
        assert not ui._exit_requested
        release.set()
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert ui._parent_drafts.io.failures == 1 and not ui._exit_requested
        assert ui._draft_quit_failures is None  # Failed receipt canceled deferred quit.
        assert any('store full (32 snapshots)' in text for text in notices)
        assert any('Quit canceled' in text for text in notices)
        assert {path.name: path.read_bytes() for path in store.root.iterdir()} == before
        assert ui._editor.get_text() == 'CURRENT UNSAVED SOURCE MUST SURVIVE' and not calls
        assert ui._child_panel is None and ui._parent_draft_panel is None
    finally:
        release.set()
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_primary_view_retirement_does_not_steal_protected_overlay_input_or_return_focus(
    tmp_path, kind, monkeypatch,
):
    from xdog.tui.tui import Component
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    events = []
    class Protected(Component):
        focused = False
        def handle_input(self, event):
            events.append(event.key)
            return True
        def render(self, width):
            return ['Protected native approval']
    ui._editor.set_text('UNCHANGED PRIMARY')
    try:
        ui._parent_draft_command('save')
        browser = ui._parent_draft_panel
        entry = browser.source()[0]
        protected = Protected()
        handle = ui._tui.show_overlay(protected)
        assert ui._tui._focused is protected
        ui._tui._dispatch_input(KeyEvent('enter'))
        assert events == ['enter'] and not ui._parent_drafts.busy
        ui._poll_parent_drafts()
        assert ui._parent_draft_panel is None and ui._tui._focused is protected
        with pytest.raises(ValueError, match='view changed'):
            browser.act('save', entry)
        handle.hide()
        assert ui._tui._focused is ui._editor
        assert ui._editor.get_text() == 'UNCHANGED PRIMARY' and not calls
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('transition', ['focus-roundtrip', 'context'])
async def test_primary_save_old_actor_cannot_admit_after_focus_or_context_change(
    tmp_path, kind, transition, monkeypatch,
):
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    ui._editor.set_text('EXACT UNSENT COMPOSER')
    try:
        ui._parent_draft_command('save')
        browser = ui._parent_draft_panel
        entry = browser.source()[0]
        if transition == 'focus-roundtrip':
            ui._tui.set_focus(ui._editor)
            ui._tui.set_focus(browser)
        else:
            ui._output_epoch += 1
        with pytest.raises(ValueError, match='view changed'):
            browser.act('save', entry)
        assert not ui._parent_drafts.io.busy and not calls
        assert ui._editor.get_text() == 'EXACT UNSENT COMPOSER'
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('occupied', ['text', 'image'])
async def test_primary_restore_never_overwrites_an_already_nonempty_composer(tmp_path, kind, occupied, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('STORED UNSENT SNAPSHOT'), label='source',
                                   scope='old owner', action='parent', slot=''))
    if occupied == 'text':
        ui._editor.set_text('CURRENT MUST NOT BE OVERWRITTEN')
    else:
        ui._composer.restore((attachment(base64.b64decode(PNG), 'current.png'),))
    before = ui._parent_draft_snapshot()
    try:
        ui._parent_draft_command('restore ' + saved.id)
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        browser = ui._parent_draft_panel
        review(browser, 'enter')
        assert 'must be empty' in browser._error
        assert ui._parent_draft_snapshot() == before and not calls
        assert store.load(saved.id).sha256 == saved.sha256
        assert ui._child_panel is None
        if kind == 'claw':
            assert ui._send_queue.empty()
    finally:
        await close(ui, session)


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_primary_read_cannot_partially_adopt_images_over_unrelated_owner_budget(tmp_path, kind, monkeypatch):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    ui, session, calls = make(tmp_path, kind, monkeypatch)
    store = ChildDraftStore(ui._parent_drafts.root())
    store.root.parent.mkdir(exist_ok=True)
    image = attachment(base64.b64decode(PNG), 'saved.png')
    saved = store.save(encode_draft(ChildDraft('SAVED IMAGE', images=(image,)), label='source',
                                   scope='old owner', action='parent', slot=''))
    # Native retention accounting includes unrelated owners; recovery cannot reclaim them.
    monkeypatch.setattr(ui, '_retained_image_bytes', lambda: 40 * 1024 * 1024 - len(image.data) + 1)
    notices = []
    monkeypatch.setattr(ui._chat_log, 'add_system', notices.append)
    try:
        ui._parent_draft_command('restore')
        await wait(ui, lambda: not ui._parent_drafts.io.busy)
        assert not ui._parent_drafts.saved and ui._parent_draft_panel is None
        assert any('retained image capacity' in text for text in notices)
        assert not ui._editor.get_text() and not ui._composer.items and not calls
        assert store.load(saved.id).sha256 == saved.sha256 and ui._child_panel is None
    finally:
        await close(ui, session)
