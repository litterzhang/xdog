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
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.message_request import host_message
from xdog.agent.extensions.queued_input import proposal, request, reviewed
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import ImageContent

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


async def test_api_without_host_returns_unavailable_and_no_ordinary_dialog_grant():
    api = ExtensionAPI({})
    assert await api.request_queued_input('no active run') is False
    with pytest.raises(ValueError):
        await api.request_queued_input('invalid', images=[IMAGE])


def test_projected_summary_unhashable_mime_is_a_validation_error_not_typeerror():
    value = dialog(host_message='host', image_summaries=[{'mime_type': [], 'bytes': 1, 'sha256': 'a' * 64}])
    with pytest.raises(ValueError):
        request(value)
