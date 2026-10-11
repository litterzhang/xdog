"""Shared queued-input proposal contract and actual callback/worker ownership fences."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.message_request import host_message
from xdog.agent.extensions.queued_input import METHODS, proposal, request, reviewed
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.ai.types import ImageContent, UserMessage

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
IMAGE = ImageContent(data=PNG, mime_type='image/png')
FIXTURE = Path(__file__).parent / 'fixtures/extensions/queuedrequest'


def dialog(**changes):
    return {'id': 'request', 'kind': 'session', 'operation': 'queue_input', 'title': 'Review queued input',
            'text': ' /quit ', 'delivery': 'steer', 'images': [], **changes}


def setup(tmp_path, **settings):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'queuedrequest')
    return ExtensionRuntime(['queuedrequest'], root, tmp_path, settings={'queuedrequest': settings},
                            context=lambda: {'session_id': 'original', 'child_id': ''})


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('fields', [
    {'text': ''}, {'text': '\x00'}, {'text': 'x' * 16385}, {'delivery': 'nextTurn'}, {'delivery': []},
    {'images': 'borrowed'}, {'images': [asdict(IMAGE)] * 5}, {'session_id': 'other'}, {'target': {}},
    {'image_refs': ['draft']}, {'kind': 'confirm'},
    {'images': [{**asdict(IMAGE), 'mime_type': []}]}, {'images': [{**asdict(IMAGE), 'mime_type': {}}]},
    {'images': [{**asdict(IMAGE), 'mime_type': 'image/jpeg'}]}, {'images': [{**asdict(IMAGE), 'data': 'not base64'}]},
    {'images': [{**asdict(IMAGE), 'path': '/private'}]},
])
def test_queue_schema_refuses_foreign_scopes_invalid_types_and_unimplemented_delivery(fields):
    with pytest.raises(ValueError):
        validate_dialog(dialog(**fields))


def test_proposal_accepts_only_typed_inline_images_with_total_serialized_frame_bound():
    assert proposal('literal /quit', 'follow_up', (IMAGE,))['images'] == [asdict(IMAGE)]
    for images in ([IMAGE], ({'data': PNG},), (ImageContent(data=PNG, mime_type=[]),)):
        with pytest.raises(ValueError):
            proposal('literal /quit', 'steer', images)
    encoded = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * (4 * 1024 * 1024)).decode()
    with pytest.raises(ValueError, match='size'):
        proposal('text', 'steer', (ImageContent(data=encoded, mime_type='image/png'),))
    # Individually valid images must not collectively exceed the frame budget.
    encoded = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * (3 * 1024 * 1024)).decode()
    with pytest.raises(ValueError, match='budget'):
        proposal('text', 'steer', (ImageContent(data=encoded, mime_type='image/png'),) * 2)


@pytest.mark.parametrize('answer', [False, None, 1, 'yes', {'approve': True}])
async def test_only_exact_true_can_stage_a_review(answer):
    commits = []
    lease = InvocationLease('queuedrequest', 'original', '', 'tools/call', lambda: True, object())
    token = _current.set(lease)
    async def ask(*args):
        return answer
    async def apply(*args):
        commits.append(args)
    try:
        assert not await reviewed('queuedrequest', dialog(), ask=ask, apply=apply, alive=lambda: True)
        await lease.complete(True)
        assert not commits
    finally:
        _current.reset(token)


@pytest.mark.parametrize('change', ['success', 'failure', 'host-revoked', 'worker-revoked', 'mutated', 'nested'])
async def test_review_projects_only_descriptors_and_commits_exact_snapshot_once_after_success(change):
    live, owner, reviews, commits = [True], [True], [], []
    parent = InvocationLease('queuedrequest', 'original', '', 'tools/call', lambda: True, object())
    lease = InvocationLease('queuedrequest', 'original', '', 'tools/call', lambda: owner[0], object(),
                            parent=parent if change == 'nested' else None)
    token = _current.set(lease)
    raw = dialog(images=[asdict(IMAGE)])
    async def ask(extension, projected):
        reviews.append(projected)
        assert projected['images'] == [] and PNG not in str(projected)
        assert projected['image_summaries'] == [{'mime_type': 'image/png', 'bytes': len(base64.b64decode(PNG)),
                                                'sha256': hashlib.sha256(base64.b64decode(PNG)).hexdigest()}]
        assert validate_dialog(projected) == projected
        if change == 'mutated':
            raw['text'] = 'foreign input'
            raw['images'][0]['data'] = 'foreign image'
            projected['image_summaries'][0]['sha256'] = 'f' * 64
        return True
    async def apply(*args):
        commits.append(args)
    try:
        assert await reviewed('queuedrequest', raw, ask=ask, apply=apply, alive=lambda: live[0])
        assert not commits and len(reviews) == 1
        if change == 'host-revoked':
            live[0] = False
        if change == 'worker-revoked':
            owner[0] = False
        await lease.complete(change != 'failure')
        if change == 'nested':
            assert not commits
            await parent.complete(True)
        await lease.complete(True)
        expected = change in {'success', 'mutated', 'nested'}
        assert bool(commits) is expected
        if expected:
            assert len(commits) == 1 and commits[0][0].startswith('extq-')
            assert commits[0][1:] == (host_message('queuedrequest', ' /quit '), 'steer', (IMAGE,))
    finally:
        _current.reset(token)


@pytest.mark.parametrize('method', ['xdog/command', 'xdog/custom_frame', 'xdog/terminal_input',
                                    'xdog/provider', 'xdog/context', 'xdog/context_finish'])
async def test_helper_does_not_admit_other_callback_kinds(method):
    lease = InvocationLease('queuedrequest', 'original', '', method, lambda: True, object())
    token = _current.set(lease)
    async def forbidden(*args):
        pytest.fail('Unsupported callback borrowed queue review or admission')
    try:
        assert not await reviewed('queuedrequest', dialog(), ask=forbidden, apply=forbidden, alive=lambda: True)
        await lease.complete(True)
    finally:
        _current.reset(token)


@pytest.mark.parametrize('fields', [{'host_message': 'forged'}, {'image_summaries': []}])
async def test_worker_cannot_supply_its_own_review_projection(fields):
    lease = InvocationLease('queuedrequest', 'original', '', 'tools/call', lambda: True, object())
    token = _current.set(lease)
    async def forbidden(*args):
        pytest.fail('Forged projection reached the review')
    try:
        with pytest.raises(ValueError):
            await reviewed('queuedrequest', dialog(**fields), ask=forbidden, apply=forbidden, alive=lambda: True)
    finally:
        _current.reset(token)


@pytest.mark.parametrize('scope', ['tool', 'event', 'pre', 'post', 'input'])
@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
async def test_actual_allowed_callback_scopes_stage_reviewed_text_images_only(scope, delivery, tmp_path):
    runtime = setup(tmp_path, scope=scope, delivery=delivery, image=True)
    reviews, commits = [], []
    async def ask(extension, projected):
        reviews.append(projected)
        return True
    async def apply(*args):
        commits.append(args)
    async def handler(extension, value):
        return await reviewed(extension, value, ask=ask, apply=apply, alive=lambda: True)
    runtime.queued_input_handler = handler
    try:
        await runtime.initialize()
        if scope == 'tool':
            result = await runtime.tools()[0].execute('owned', {})
            assert result.content[0].text == 'QUEUE-True'
        elif scope == 'event':
            await runtime.event('turn_start', {'host_queued_input': False, 'session_id': 'foreign'})
        elif scope == 'pre':
            assert await runtime.pre('safe', {}) == {}
        elif scope == 'post':
            assert await runtime.post('safe', {}, 'unchanged') == 'unchanged'
        else:
            value = UserMessage(content='unchanged')
            assert await runtime.input(value) == value
        assert len(reviews) == len(commits) == 1
        assert commits[0][1:] == (host_message('queuedrequest', ' QUEUED 界\n/quit '), delivery, (IMAGE,))
        assert not runtime._failed and METHODS == {'tools/call', 'xdog/event', 'xdog/pre', 'xdog/post', 'xdog/input'}
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('ending', ['success', 'failure', 'error', 'cancel', 'host-revoked', 'session-replaced'])
async def test_actual_callback_cleanup_and_failure_barrier_never_admits_early(ending, tmp_path):
    marker, release = tmp_path / 'marker', tmp_path / 'release'
    runtime = setup(tmp_path, marker=str(marker), release=str(release))
    live, commits = [True], []
    async def ask(*args):
        return True
    async def apply(*args):
        commits.append(args)
    async def handler(extension, value):
        return await reviewed(extension, value, ask=ask, apply=apply, alive=lambda: live[0])
    runtime.queued_input_handler = handler
    await runtime.initialize()
    task = asyncio.create_task(runtime.tools()[0].execute('owned', {'mode': 'fail' if ending == 'failure' else ending}))
    try:
        await until(marker.exists)
        assert marker.read_text() == 'True' and not commits and not task.done()
        if ending == 'host-revoked':
            live[0] = False
        elif ending == 'session-replaced':
            runtime.context = lambda: {'session_id': 'other'}
        elif ending == 'cancel':
            task.cancel()
        release.touch()
        if ending == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending == 'failure':
            with pytest.raises(Exception, match='callback|Extension'):
                await task
        else:
            result = await task
            assert result.is_error is (ending == 'error')
        assert len(commits) == int(ending == 'success')
    finally:
        release.touch()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_actual_default_command_headless_context_and_terminal_cannot_borrow_queue_host(tmp_path):
    runtime = setup(tmp_path, scope='context')
    async def forbidden(*args):
        pytest.fail('Unsupported scope borrowed queue handler')
    runtime.queued_input_handler = forbidden
    try:
        assert await runtime.command('ext-queuedrequest-request', '') == 'QUEUE-False'
        with silent_extension_ui():
            assert (await runtime.tools()[0].execute('headless', {})).content[0].text == 'QUEUE-False'
        assert await runtime.transform([]) == []
        assert await runtime.terminal_input('x', kind='key', surface='prompt') == {'consume': False, 'data': 'x'}
        assert not runtime._failed
    finally:
        await runtime.aclose()


async def test_actual_custom_render_and_input_remain_unsupported_for_queue_proposals(tmp_path):
    runtime, broker, events = setup(tmp_path), DialogBroker(), []
    broker.publish = events.append
    runtime.custom_handler = broker.ask_custom
    async def forbidden(*args):
        pytest.fail('Custom callback borrowed active-run queue authority')
    runtime.queued_input_handler = forbidden
    task = asyncio.create_task(runtime.command('ext-queuedrequest-custom', ''))
    try:
        await until(lambda: events)
        identifier = events[0]['dialog']['id']
        assert broker.request_custom(identifier, {'kind': 'resize', 'width': 40, 'height': 5, 'painted': -1})
        await until(lambda: len(events) == 2)
        assert broker.request_custom(identifier, {'kind': 'key', 'key': 'enter', 'ctrl': False,
            'alt': False, 'shift': False, 'width': 40, 'height': 5, 'painted': 1})
        assert await task == 'QUEUE-False'
    finally:
        broker.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_pending_proposal_cannot_adopt_replaced_host_handler(tmp_path):
    before, allow = tmp_path / 'before', tmp_path / 'allow'
    runtime = setup(tmp_path, before_request=str(before), allow_request=str(allow))
    async def original(*args):
        pytest.fail('Original handler should have been revoked')
    async def replacement(*args):
        pytest.fail('Pending request adopted replacement host')
    runtime.queued_input_handler = original
    await runtime.initialize()
    task = asyncio.create_task(runtime.tools()[0].execute('owned', {}))
    try:
        await until(before.exists)
        runtime.queued_input_handler = replacement
        allow.touch()
        assert (await task).content[0].text == 'QUEUE-False'
    finally:
        allow.touch()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_api_without_host_returns_unavailable_and_no_ordinary_dialog_grant():
    api = ExtensionAPI({})
    assert await api.request_queued_input('no active run') is False
    with pytest.raises(ValueError):
        await api.request_queued_input('invalid', images=[IMAGE])


def test_projected_summary_unhashable_mime_is_a_validation_error_not_typeerror():
    value = dialog(host_message='host', image_summaries=[{'mime_type': [], 'bytes': 1, 'sha256': 'a' * 64}])
    with pytest.raises(ValueError):
        request(value)
