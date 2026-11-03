"""Preserve useful backend failure stages without leaking browser/account data."""
from __future__ import annotations

import httpx
import pytest
from xdog.claw.channels.xhs.api import XhsApi


@pytest.mark.parametrize(('details', 'stage'), [
    ('未找到评论 secret-comment-id，它可能不在列表', 'comment_not_found'),
    ('通知条目渲染数(0)少于目标位置(4)，页面可能未加载完', 'notification_not_rendered'),
    ('该通知没有回复入口: private-diagnostic', 'reply_entry_missing'),
    ('回复输入框未出现: private-diagnostic', 'reply_input_missing'),
    ('回复对象不符：期望 "private-user"，实际 "other-user"', 'reply_target_mismatch'),
    ('无法输入回复内容: private-diagnostic', 'reply_input_failed'),
    ('无法点击发送: private-diagnostic', 'submit_click_failed'),
    ('回复未确认成功：发送后输入框仍未收起', 'submit_unconfirmed'),
    ('https://example.invalid/?access_token=private-token', 'unclassified'),
])
async def test_reply_backend_error_retains_stage_but_not_raw_diagnostic(details, stage):
    requests = []
    def backend(request):
        requests.append(request)
        return httpx.Response(500, json={'code': 'REPLY_NOTIFICATION_FAILED', 'error': 'private-error',
            'details': details + ' Cookie=session-secret Authorization=Bearer private-token'})
    api = XhsApi('http://127.0.0.1:18060', transport=httpx.MockTransport(backend))
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await api.request('POST', 'notifications/reply', json={'comment_id': 'original', 'content': 'reply'})
        error = caught.value
        assert error.response.status_code == 500
        assert f'stage={stage}' in str(error) and 'REPLY_NOTIFICATION_FAILED' in str(error)
        assert error.request is requests[0] and len(requests) == 1
        for secret in ('private-', 'session-secret', 'other-user', 'secret-comment-id', 'https://'):
            assert secret not in str(error)
    finally:
        await api.close()


@pytest.mark.parametrize('body', [b'<html>private-token</html>', b'{bad json', b'\xff', b'x' * (65536 + 1)])
async def test_unparseable_or_oversized_backend_errors_stay_bounded_generic_and_not_retried(body):
    calls = []
    def backend(request):
        calls.append(request)
        return httpx.Response(500, content=body)
    api = XhsApi('http://127.0.0.1:18060', transport=httpx.MockTransport(backend))
    try:
        with pytest.raises(httpx.HTTPStatusError, match='stage=unclassified') as caught:
            await api.request('POST', 'notifications/reply', json={'comment_id': 'original', 'content': 'reply'})
        assert len(str(caught.value)) < 240 and len(calls) == 1
        assert 'private-token' not in str(caught.value)
    finally:
        await api.close()


async def test_reported_pre_submit_stage_does_not_authorize_automatic_retry(tmp_path):
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore
    from xdog.claw.core.runtime.input_adapter import freeze_input
    from xdog.claw.core.types import UserInput

    calls = []
    def backend(request):
        calls.append(request)
        return httpx.Response(500, json={'code': 'REPLY_NOTIFICATION_FAILED', 'details': '未找到评论 original'})
    api = XhsApi('http://127.0.0.1:18060', transport=httpx.MockTransport(backend))
    async def send(row):
        await api.request('POST', 'notifications/reply', json={'comment_id': 'original', 'content': row.text})
    outputs = OutputDelivery(await OutputStore.open(tmp_path / 'outputs.sqlite3'), lambda _: send)
    envelope = freeze_input(UserInput(group_id='main', channel='xhs', content='original'), tmp_path, message_id='input')
    try:
        await outputs.emit(envelope, 'attempt', 'original-binding', ['reply'])
        rows = await outputs.store.records(InputScope('main', tmp_path))
        assert len(rows) == 1 and rows[0].status == 'unknown'
        assert 'stage=comment_not_found' in rows[0].error
        await outputs.resume()
        assert len(calls) == 1
    finally:
        await outputs.close()
        await api.close()
