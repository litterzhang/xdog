import json

import httpx
import pytest
from xdog.eyes.models import GithubSourceDef
from xdog.eyes.sources.github import utc
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


@pytest.mark.parametrize('resource', ['issues', 'issue_comments', 'workflow_runs'])
@pytest.mark.parametrize('last,reason', [(NOW-90000, 'observation_gap'), (NOW+600, 'clock_regression')])
async def test_gap_resumes_bounded_acquisition_and_preserves_audit(resource, last, reason):
    seen = []
    def handle(request):
        seen.append(request)
        rows = [issue()]
        return httpx.Response(200, json={'total_count': 1, 'workflow_runs': rows}
                              if resource == 'workflow_runs' else rows)
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source(resource), {'last_success': last}, now=NOW)
        assert result.snapshot.gap and result.snapshot.valid and result.snapshot.healthy
        assert result.state['last_success'] == NOW and not result.state['error']
        original = json.loads(json.dumps(result.state))
        coverage = result.state['coverage_gap']
        assert coverage['count'] == 1 and coverage['first'] == coverage['latest']
        assert coverage['first'] == {'reason': reason, 'previous_success': last,
                                     'resumed_at': NOW, 'window_start': NOW-86400}
        query = seen[0].url.params
        expected = ('>=' if resource == 'workflow_runs' else '') + utc(NOW-86400)
        assert query.get('since', query.get('created')) == expected
        # A normal subsequent sample (including after restart) must not clear the
        # historical warning or create a second gap for the same interruption.
        resumed = await sampler.poll(source(resource), json.loads(json.dumps(result.state)), now=NOW+60)
        assert resumed.snapshot.healthy and resumed.snapshot.valid and resumed.snapshot.gap
        assert resumed.state['coverage_gap'] == coverage and resumed.state['last_success'] == NOW+60
        # A second interruption keeps bounded first/latest evidence rather than
        # growing an unbounded history or erasing the first gap.
        again = await sampler.poll(source(resource), resumed.state, now=NOW+100000)
        assert again.state['coverage_gap']['count'] == 2
        assert again.state['coverage_gap']['first'] == coverage['first']
        assert again.state['coverage_gap']['latest']['previous_success'] == NOW+60
        assert result.state == original  # caller-owned input was not mutated
    finally:
        await sampler.close()


@pytest.mark.parametrize('failure', ['http', 'parse', 'capacity'])
async def test_gap_recovery_failure_retains_watermark_and_prior_coverage(failure):
    responses = [httpx.Response(200, json=[])]
    sampler = RemoteSampler(Transport(lambda request: responses.pop(0)))
    try:
        initial = await sampler.poll(source(), {'last_success': NOW-90000}, now=NOW)
        state = json.loads(json.dumps(initial.state))
        responses.append(httpx.Response(503) if failure == 'http' else
                         httpx.Response(200, content=b'invalid') if failure == 'parse' else
                         httpx.Response(200, json=[issue(), issue(2)]))
        failed = await sampler.poll(source(max_records=1), state, now=NOW+100000)
        assert not failed.snapshot.valid and not failed.snapshot.healthy
        assert failed.state['last_success'] == NOW
        assert failed.state['gap'] and failed.state['coverage_gap'] == initial.state['coverage_gap']
        assert state == initial.state
        responses.append(httpx.Response(200, json=[]))
        recovered = await sampler.poll(source(), failed.state, now=NOW+100060)
        assert recovered.snapshot.healthy and recovered.state['last_success'] == NOW+100060
        assert recovered.state['coverage_gap']['count'] == 2
        assert recovered.state['coverage_gap']['first'] == initial.state['coverage_gap']['first']
    finally:
        await sampler.close()


async def test_gap_keeps_tracking_old_active_workflow_runs():
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'total_count': 0, 'workflow_runs': []}
                              if request.url.path.endswith('/runs') else
                              issue(9, status='completed', conclusion='success', run_attempt=1))
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(source('workflow_runs'),
                                    {'last_success': NOW-90000, 'active_runs': [9]}, now=NOW)
        assert result.snapshot.healthy and result.snapshot.gap
        assert [row.key for row in result.snapshot.records] == ['9:1']
        assert result.state['active_runs'] == []
        assert paths == ['/repos/o/r/actions/runs', '/repos/o/r/actions/runs/9']
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


async def test_failed_first_gap_recovery_cannot_advance_or_rebaseline():
    responses = [httpx.Response(503), httpx.Response(200, json=[issue()])]
    sampler = RemoteSampler(Transport(lambda request: responses.pop(0)))
    original = {'last_success': NOW-90000, 'active_runs': []}
    try:
        failed = await sampler.poll(source(), original, now=NOW)
        assert not failed.snapshot.valid and failed.state['last_success'] == original['last_success']
        assert 'coverage_gap' not in failed.state  # No successful new collection yet.
        recovered = await sampler.poll(source(), failed.state, now=NOW+60)
        assert recovered.snapshot.valid and recovered.snapshot.healthy and recovered.snapshot.gap
        assert recovered.state['coverage_gap']['first']['previous_success'] == NOW-90000
        assert recovered.state['coverage_gap']['count'] == 1
        assert original == {'last_success': NOW-90000, 'active_runs': []}
    finally:
        await sampler.close()
