import json
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.models import GithubSourceDef
from xdog.eyes.sources.remote import RemoteSampler

from packages.eyes.tests.test_http_source import Transport

NOW = 1793178000
STAMP = '2026-10-28T08:00:00Z'


def issue(identifier=1, **fields):
    return {'id': identifier, 'html_url': f'https://github.com/o/r/issues/{identifier}',
            'updated_at': STAMP, 'title': '用户反馈', 'body': '内容', **fields}


def source(resource='issues', **kwargs):
    return GithubSourceDef(id='github', repository='o/r', resource=resource, **kwargs)


async def test_issues_follow_pages_exclude_prs_and_keep_updates():
    seen = []
    def handle(request):
        seen.append(request)
        assert request.method == 'GET'
        if len(seen) == 1:
            return httpx.Response(200, json=[issue(), issue(2, pull_request={})], headers={
                'link': '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'})
        return httpx.Response(200, json=[issue(3)])
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source(), {}, now=NOW)
        assert result.snapshot.valid and not result.snapshot.complete
        assert [row.key for row in result.snapshot.records] == ['1', '3']
        assert result.state['last_success'] == NOW
        assert 'since' in seen[0].url.params
        assert 'if-none-match' not in seen[0].headers
    finally:
        await sampler.close()


@pytest.mark.parametrize('next_url', ['https://attacker.test/repos/o/r/issues?page=2',
    'https://api.github.com/repos/other/repo/issues?page=2',
    'https://secret:token@api.github.com/repos/o/r/issues?page=2'])
async def test_unsafe_pagination_cannot_advance_cursor_or_leak_tokens(monkeypatch, next_url):
    monkeypatch.setenv('EYES_TOKEN', 'private-secret')
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=[issue()], headers={'link': f'<{next_url}>; rel="next"'})
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source(bearer_env='EYES_TOKEN'), {'last_success': NOW-60}, now=NOW)
        assert not result.snapshot.valid and result.state['last_success'] == NOW-60
        assert len(seen) == 1 and 'private-secret' not in repr(result)
    finally:
        await sampler.close()


async def test_last_page_failure_does_not_publish_partial_collection():
    values = [httpx.Response(200, json=[issue()], headers={
        'link': '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'}),
        httpx.Response(429, headers={'retry-after': '900'})]
    sampler = RemoteSampler(Transport(lambda request: values.pop(0)))
    try:
        result = await sampler.poll(source(), {'last_success': NOW-60}, now=NOW)
        assert not result.snapshot.valid and not result.snapshot.records
        assert result.state['last_success'] == NOW-60 and result.delay == 900
    finally:
        await sampler.close()


@pytest.mark.parametrize('limit', ['pages', 'records', 'bytes'])
async def test_capacity_is_explicit_failure_not_silent_truncation(limit):
    values = {'max_pages': 1} if limit == 'pages' else {'max_records': 1} if limit == 'records' else {'max_bytes': 8}
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, json=[issue(), issue(2)], headers={
        'link': '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'})))
    try:
        result = await sampler.poll(source(**values), {'last_success': NOW-1}, now=NOW)
        assert not result.snapshot.valid and result.state['last_success'] == NOW-1
    finally:
        await sampler.close()


async def test_gap_is_reported_without_network_or_watermark_advance():
    def forbidden(request):
        pytest.fail('A known unfillable gap must not silently become a new baseline')
    sampler = RemoteSampler(Transport(forbidden))
    try:
        result = await sampler.poll(source(), {'last_success': NOW-90000}, now=NOW)
        assert result.snapshot.gap and not result.snapshot.valid
        assert result.state['gap'] and result.state['last_success'] == NOW-90000
    finally:
        await sampler.close()


async def test_comments_are_first_class_stable_records():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=[issue(123, body='评论更新')])
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source('issue_comments'), {}, now=NOW)
        assert '/issues/comments' in str(seen[0].url)
        assert result.snapshot.records[0].key == '123'
        assert json.loads(result.snapshot.records[0].value)['body'] == '评论更新'
    finally:
        await sampler.close()


async def test_workflow_attempt_identity_and_old_active_runs_are_retained():
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith('/runs'):
            return httpx.Response(200, json={'total_count': 1, 'workflow_runs': [issue(10,
                status='completed', conclusion='failure', run_attempt=2)]})
        assert request.url.path.endswith('/runs/9')
        return httpx.Response(200, json=issue(9, status='in_progress', run_attempt=1))
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source('workflow_runs'), {'last_success': NOW-60, 'active_runs': [9]}, now=NOW)
        assert {row.key for row in result.snapshot.records} == {'10:2', '9:1'}
        assert result.state['active_runs'] == [9]
        assert 'created' in seen[0].url.params
    finally:
        await sampler.close()


async def test_actions_service_cap_cannot_be_reported_as_complete():
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200,
        json={'total_count': 1200, 'workflow_runs': []})))
    try:
        result = await sampler.poll(replace(source('workflow_runs'), max_records=2000), {}, now=NOW)
        assert not result.snapshot.valid and 'last_success' not in result.state
    finally:
        await sampler.close()


@pytest.mark.parametrize('target', ['https://api.github.com/repos/o/r/../../private',
    'https://api.github.com/repos/o/r/%2e%2e/private', 'https://api.github.com/repos/o/r/..%2fprivate'])
async def test_normalized_pagination_cannot_escape_repository(target):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=[issue()], headers={'link': f'<{target}>; rel="next"'})
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source(), {}, now=NOW)
        assert not result.snapshot.valid and len(seen) == 1
    finally:
        await sampler.close()


async def test_redirect_cannot_escape_github_repository_scope():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={'location': '/repos/other/private/issues'})
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source(), {}, now=NOW)
        assert not result.snapshot.valid and len(seen) == 1
    finally:
        await sampler.close()


async def test_active_run_rate_limit_retains_cursor_and_honors_retry_after():
    responses = [httpx.Response(200, json={'total_count': 0, 'workflow_runs': []}),
                 httpx.Response(429, headers={'retry-after': '900'})]
    sampler = RemoteSampler(Transport(lambda request: responses.pop(0)))
    try:
        result = await sampler.poll(source('workflow_runs'), {'last_success': NOW-60, 'active_runs': [9]}, now=NOW)
        assert not result.snapshot.valid and result.state['last_success'] == NOW-60
        assert result.state['active_runs'] == [9] and result.delay == 900
    finally:
        await sampler.close()
