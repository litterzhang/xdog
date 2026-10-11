"""Actual worker write proposals preserve original ownership and callback completion."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog
from xdog.agent.extensions.editor_request import reviewed
from xdog.agent.extensions.invocation import InvocationLease
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.ai.types import UserMessage

FIXTURE = Path(__file__).parent / 'fixtures/extensions/editorwrite'


def setup(tmp_path, **settings):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'editorwrite')
    runtime = ExtensionRuntime(['editorwrite'], root, tmp_path, settings={'editorwrite': settings},
        context=lambda: {'session_id': 'original', 'child_id': ''})
    return runtime


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.003)


@pytest.mark.parametrize('scope', ['command', 'tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_real_worker_reviewed_proposal_from_exact_callback_no_composer_read(tmp_path, scope):
    runtime, reviews, commits = setup(tmp_path, scope=scope), [], []
    async def ask(extension, dialog):
        reviews.append(dialog)
        return {'approved': True, 'revision': 'a' * 64}
    async def commit(event):
        commits.append(event)
    async def handler(extension, dialog):
        return await reviewed(extension, dialog, ask=ask, commit=commit)
    runtime.editor_write_handler = handler
    try:
        await runtime.initialize()
        if scope == 'command':
            assert await runtime.command('ext-editorwrite-write', '') == 'EDITOR-WRITE-True'
        elif scope == 'tool':
            assert (await runtime.tools()[0].execute('owned', {})).content[0].text == 'EDITOR-WRITE-True'
        elif scope == 'event':
            await runtime.event('turn_start', {'host_editor_write': False, 'session_id': 'foreign'})
        elif scope == 'pre':
            assert await runtime.pre('safe', {}) == {}
        elif scope == 'post':
            assert await runtime.post('safe', {}, 'unchanged') == 'unchanged'
        elif scope == 'input':
            message = UserMessage(content='unchanged')
            assert await runtime.input(message) == message
        else:
            assert await runtime.transform([]) == []
        assert len(reviews) == len(commits) == 1
        identity = reviews[0]['editor_write']
        assert identity['session_id'] == 'original' and identity['child_id'] == ''
        assert commits[0]['editor_write'] == identity and commits[0]['text'] == 'PROPOSED 界\nunsent'
        assert set(commits[0]) == {'type', 'editor_write', 'extension', 'revision', 'text'}
        assert not runtime._failed
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('mode', ['ok', 'fail', 'error', 'cancel', 'changed'])
async def test_actual_worker_cleanup_barrier_failures_tool_error_and_cancel_never_commit_early(tmp_path, mode):
    marker, release = tmp_path / 'marker', tmp_path / 'release'
    runtime = setup(tmp_path, marker=str(marker), release=str(release))
    commits, discards = [], []
    async def ask(extension, dialog):
        return {'approved': True, 'revision': 'a' * 64}
    async def commit(event):
        commits.append(event)
    async def handler(extension, dialog):
        return await reviewed(extension, dialog, ask=ask, commit=commit, discard=discards.append)
    runtime.editor_write_handler = handler
    await runtime.initialize()
    task = asyncio.create_task(runtime.tools()[0].execute('owned', {'mode': mode}))
    try:
        await until(marker.exists)
        assert not task.done() and not commits and not discards
        if mode == 'changed':
            runtime.context = lambda: {'session_id': 'replacement'}
        if mode == 'cancel':
            task.cancel()
        release.write_text('finish original finally')
        if mode == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == 'fail':
            with pytest.raises(Exception, match='callback|Extension'):
                await task
        else:
            result = await task
            assert result.is_error == (mode == 'error')
        assert len(commits) == (1 if mode == 'ok' else 0)
        assert len(discards) == (0 if mode == 'ok' else 1)
    finally:
        release.write_text('finish')
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_handler_presence_cannot_grant_headless_or_terminal_ui(tmp_path):
    runtime, calls = setup(tmp_path), []
    async def forbidden(extension, dialog):
        calls.append(dialog)
        return {'approved': True, 'revision': 'a' * 64}
    runtime.editor_write_handler = forbidden
    try:
        with silent_extension_ui():
            assert await runtime.command('ext-editorwrite-write', '') == 'EDITOR-WRITE-False'
        assert await runtime.terminal_input('x', kind='key', surface='editor') == {'consume': False, 'data': 'x'}
        assert not calls
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('fail', [False, True])
async def test_nested_custom_input_stages_into_original_callback_and_render_cannot_propose(tmp_path, fail):
    runtime, broker, notices, commits = setup(tmp_path), DialogBroker(), [], []
    broker.publish = notices.append
    runtime.custom_handler = broker.ask_custom
    async def ask(extension, dialog):
        return {'approved': True, 'revision': 'a' * 64}
    async def commit(event):
        commits.append(event)
    async def handler(extension, dialog):
        return await reviewed(extension, dialog, ask=ask, commit=commit)
    runtime.editor_write_handler = handler
    task = asyncio.create_task(runtime.command('ext-editorwrite-custom', 'fail' if fail else ''))
    try:
        await until(lambda: notices)
        identifier = notices[0]['dialog']['id']
        assert broker.request_custom(identifier, {'kind': 'resize', 'width': 40, 'height': 5, 'painted': -1})
        await until(lambda: len(notices) == 2)
        assert not commits
        assert broker.request_custom(identifier, {'kind': 'key', 'key': 'enter', 'ctrl': False,
            'alt': False, 'shift': False, 'width': 40, 'height': 5, 'painted': 1})
        if fail:
            with pytest.raises(Exception, match='callback|Extension'):
                await task
        else:
            assert await task == 'EDITOR-WRITE-True'
        assert len(commits) == int(not fail)
    finally:
        broker.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


@pytest.mark.parametrize('bad', ['\x1b[2J', '\x00', '\r', '\ud800', 'a' * 65537])
def test_proposal_codec_refuses_controls_and_oversize(bad):
    with pytest.raises(ValueError):
        validate_dialog({'kind': 'editor', 'id': 'owned', 'title': 'Write', 'operation': 'propose', 'text': bad})


async def test_once_only_generic_lease_discard_nested_success_and_no_retry_of_failed_commit():
    events = []
    async def commit():
        events.append('commit')
    def discard():
        events.append('discard')
    key = object()
    root = InvocationLease('extension', 'session', '', 'tools/call', lambda: True, key)
    nested = InvocationLease('extension', 'session', '', 'xdog/custom_frame', lambda: True, key, root)
    assert nested.defer(commit, discard)
    await nested.complete(True)
    assert not events
    await root.complete(False)
    await root.complete(True)
    assert events == ['discard'] and not root.defer(commit)
    fresh = InvocationLease('extension', 'session', '', 'tools/call', lambda: True, key)
    async def broken():
        events.append('uncertain')
        raise OSError('write outcome unknown')
    assert fresh.defer(broken, discard) and fresh.defer(commit, discard)
    with pytest.raises(OSError):
        await fresh.complete(True)
    await fresh.complete(True)
    assert events == ['discard', 'uncertain', 'discard']


async def test_real_provider_callback_cannot_borrow_installed_editor_proposal_handler(tmp_path):
    from xdog.agent.extensions.providers import ExtensionProviderStream
    from xdog.ai.types import Context, StreamOptions
    runtime, requests = setup(tmp_path), []
    async def forbidden(extension, dialog):
        requests.append(dialog)
        return {'approved': True, 'revision': 'a' * 64}
    runtime.editor_write_handler = forbidden
    try:
        await runtime.initialize()
        def base(*args):
            raise AssertionError('No provider fallback')
        adapter = ExtensionProviderStream(runtime, base)
        stream = adapter('ext-editorwrite-offline/scope', Context(messages=(UserMessage(content='scope'),)),
                         StreamOptions())
        events = [event async for event in stream]
        assert events[-1].type == 'done'
        assert (await stream.result()).content[0].text == 'PROVIDER-SCOPE-REFUSED'
        assert not requests
    finally:
        await runtime.aclose()
