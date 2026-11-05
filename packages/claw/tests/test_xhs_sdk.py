"""Optional SDK boundary: no browser, live account, model or external write."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from xdog.claw.channels.xhs.api import (
    DependencyMissing,
    LoginRequired,
    XhsApi,
    XhsUnavailable,
    cookie_path,
    credential_home,
    require_sdk,
)


def cookies(home: Path, **values):
    path = cookie_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'a1': 'fixture-a1', 'web_session': 'fixture-session', **values}))
    path.chmod(0o600)


def mention(identifier='c1'):
    return {'type': 'comment/item', 'user_info': {'userid': 'A'}, 'item_info': {'id': 'feed1'},
            'comment_info': {'id': identifier, 'content': 'hello'}}


class Client:
    def __init__(self):
        self.calls = []
        self.pages = [{'message_list': [mention()], 'has_more': False}]
        self.closed = 0
        self.failure = None

    def close(self):
        self.closed += 1

    def get_self_info(self):
        self.calls.append(('identity', threading.get_ident()))
        return {'user_id': 'ours', 'basic_info': {'nickname': 'Profile'}, 'guest': False}

    def get_notification_mentions(self, **params):
        self.calls.append(('notifications', params))
        return self.pages.pop(0)

    def get_note_from_html(self, identifier):
        self.calls.append(('post', identifier))
        return {'noteId': identifier, 'title': '原帖', 'desc': '正文', 'xsec_token': 'do-not-expose'}

    def reply_comment(self, feed, comment, text):
        self.calls.append(('reply', feed, comment, text))
        if self.failure:
            raise self.failure
        return {'id': 'submission-only'}

    def get_upload_permit(self):
        self.calls.append(('permit',))
        return {'fileId': 'uploaded', 'token': 'private-upload-token'}

    def upload_file(self, identifier, token, path):
        self.calls.append(('upload', identifier, path))

    def create_image_note(self, title, text, identifiers):
        self.calls.append(('publish', title, text, identifiers))


def api(tmp_path, client):
    home = credential_home(tmp_path, 'main')
    cookies(home)
    def factory(values, **options):
        assert values == {'a1': 'fixture-a1', 'web_session': 'fixture-session'}
        assert options == {'timeout': 30, 'max_retries': 1}
        return client
    return XhsApi(tmp_path, 'main', factory=factory)


async def test_sdk_operations_are_owned_off_loop_and_do_not_use_global_cookie_discovery(tmp_path):
    client = Client()
    adapter = api(tmp_path, client)
    try:
        assert await adapter.identity() == ('ours', 'Profile')
        assert client.calls[0][1] != threading.get_ident()
        batch = await adapter.notifications()
        assert batch.items[0] == {
            'from': {'user_id': 'A'}, 'feed_id': 'feed1', 'comment_id': 'c1', 'comment_text': 'hello'}
        assert not batch.truncated
        assert (await adapter.post('feed1')) == {
            'feed_id': 'feed1', 'title': '原帖', 'text': '正文', 'untrusted': True}
        await adapter.reply('feed1', 'c1', 'answer')
        await adapter.publish('Title', 'Body', ['frozen.png'])
        assert client.calls[-1] == ('publish', 'Title', 'Body', ['uploaded'])
        assert client.closed == 5
    finally:
        await adapter.close()
    with pytest.raises(XhsUnavailable, match='closed'):
        await adapter.identity()


async def test_mentions_follow_cursor_and_ignore_items_without_a_reply_target(tmp_path):
    client = Client()
    client.pages = [
        {'message_list': [mention(), {'type': 'mention/item'}], 'has_more': True, 'cursor': 'next'},
        {'message_list': [mention('c2')], 'has_more': False},
    ]
    adapter = api(tmp_path, client)
    try:
        assert [item['comment_id'] for item in (await adapter.notifications()).items] == ['c1', 'c2']
        assert client.calls[-1][1]['cursor'] == 'next'
    finally:
        await adapter.close()


@pytest.mark.parametrize('bad', ['missing-target', 'cursor-stuck'])
async def test_bad_notification_contract_does_not_guess_comment_ids(tmp_path, bad):
    client = Client()
    item = mention()
    if bad == 'missing-target':
        item.pop('comment_info')
        client.pages = [{'message_list': [item]}]
    elif bad == 'missing-author':
        item['user_info'] = {'nickname': 'not-an-identity', 'user_id': 'wrong-field'}
        client.pages = [{'message_list': [item]}]
    elif bad == 'cursor-stuck':
        client.pages = [{'message_list': [item], 'has_more': True, 'cursor': ''}]
    elif bad == 'overflow':
        client.pages = [{'message_list': [item] * 201}]
    else:
        item['type'] = 'future-unknown-type'
        client.pages = [{'message_list': [item]}]
    adapter = api(tmp_path, client)
    try:
        with pytest.raises(XhsUnavailable):
            await adapter.notifications()
    finally:
        await adapter.close()


async def test_saturated_window_is_visible_not_silently_complete(tmp_path):
    client = Client()
    client.pages = [{'message_list': [mention()] * 100, 'has_more': True, 'cursor': '1'},
                    {'message_list': [mention()] * 100, 'has_more': True, 'cursor': '2'}]
    adapter = api(tmp_path, client)
    try:
        batch = await adapter.notifications()
        assert len(batch.items) == 200 and batch.truncated
    finally:
        await adapter.close()


@pytest.mark.parametrize('kind', ['SessionExpiredError', 'OtherError'])
async def test_errors_are_sanitized_and_writes_are_not_replayed(tmp_path, kind):
    client = Client()
    client.failure = type(kind, (Exception,), {})('Cookie=secret; token=private-response')
    adapter = api(tmp_path, client)
    try:
        with pytest.raises(LoginRequired if kind == 'SessionExpiredError' else XhsUnavailable) as caught:
            await adapter.reply('feed1', 'c1', 'answer')
        assert 'secret' not in str(caught.value) and 'private-response' not in str(caught.value)
        assert sum(call[0] == 'reply' for call in client.calls) == 1 and client.closed == 1
    finally:
        await adapter.close()


async def test_cancellation_settles_original_sdk_work_before_close(tmp_path):
    client = Client()
    entered, release = threading.Event(), threading.Event()
    def write(*_args):
        entered.set()
        assert release.wait(5)
    client.reply_comment = write
    adapter = api(tmp_path, client)
    task = asyncio.create_task(adapter.reply('feed1', 'c1', 'answer'))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not client.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.closed == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await adapter.close()


def test_optional_dependency_is_lazy_and_group_credentials_are_isolated(monkeypatch, tmp_path):
    from xdog.claw.channels.xhs import api as module
    original = module.importlib.import_module
    def missing(name, *args, **kwargs):
        if name == 'xhs_cli.client':
            raise ModuleNotFoundError('optional dependency missing')
        return original(name, *args, **kwargs)
    monkeypatch.setattr('xdog.claw.channels.xhs.api.importlib.import_module', missing)
    with pytest.raises(DependencyMissing, match=r'xdog-claw\[xhs\]'):
        require_sdk()
    assert credential_home(tmp_path, 'main') != credential_home(tmp_path, 'maotao')


async def test_sdk_html_fallback_cannot_substitute_another_post(tmp_path):
    client = Client()
    client.get_note_from_html = lambda _identifier: {'noteId': 'other', 'title': 'PRIVATE', 'desc': 'SECRET'}
    adapter = api(tmp_path, client)
    try:
        with pytest.raises(XhsUnavailable, match='original post') as caught:
            await adapter.post('original')
        assert 'SECRET' not in str(caught.value) and 'PRIVATE' not in str(caught.value)
    finally:
        await adapter.close()
