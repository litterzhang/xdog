"""Successful Goal work and exact original contexts survive bounded tool frames."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Goal, Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.tools import create_context_tool, create_goal_tool

from packages.skills.tests.goal.agent_host import AgentHost
from packages.skills.tests.goal.test_cumulative_state import large_goal


@asynccontextmanager
async def actual_service(tmp_path):
    owner = Owner('actual-sdk-data-pages-owner', tmp_path)
    store = await GoalStore.open(tmp_path / 'private' / 'goal.sqlite3', owner)
    host = AgentHost(owner)
    service = GoalService(store, host)
    host.service = service
    try:
        yield service, host, OperationContext(True)
    finally:
        await host.supervisor.aclose()
        await service.close()


async def test_large_committed_mutation_returns_success_not_a_false_failure(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        goal = large_goal(amount=60)
        await service.store.put(goal, expected_version=0)
        tool = create_goal_tool(service, lambda: human)
        result = await tool.execute('actual-add', {'action': 'add_task', 'goal_id': goal.id,
                                                   'description': 'A separately committed actual task'})
        saved = await service.status(goal.id)
        assert saved.version == 2 and len(saved.tasks) == 61  # Real mutation already committed.
        assert not result.is_error, 'A wire-size failure lied about an actually committed mutation'
        assert len(result.content[0].text.encode()) <= 256 * 1024 and not host.model_calls
        body = json.loads(result.content[0].text)
        assert body['snapshot_committed'] is True and body['selector']['version'] == 2


async def test_bound_actual_worker_can_read_large_original_context_after_criteria_change(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        (tmp_path / 'result.txt').write_text('Actual allowed file evidence')
        goal = large_goal(amount=60)
        await service.store.put(goal, expected_version=0)
        assigned = await service.assign(goal.id, goal.tasks[0].id, 'Inspect original evidence')
        attempt = assigned.tasks[0].attempts[-1]
        await host.supervisor.control('wait', attempt.worker.id)
        await service.revise(goal.id, 'New current outcome', ('New current criterion',), 'Explicit user revision',
                             context=human)
        tool = create_context_tool(service, goal.id, task_id=goal.tasks[0].id, attempt_id=attempt.id)
        result = await tool.execute('actual-context', {})
        assert not result.is_error, 'Role context became unusable merely because earlier valid work accumulated'
        assert len(result.content[0].text.encode()) <= 256 * 1024
        assert len(host.model_calls) == 3  # Real Agent/ChildSupervisor, scripted model boundary only.


async def test_list_more_legal_goals_than_one_wire_frame_is_still_usable(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        for number in range(70):
            goal = Goal(f'goal-{number}', f'{number}: ' + 'x' * 4000, 'Explicit outcome', ('Original criterion',))
            await service.store.put(goal, expected_version=0)
        result = await create_goal_tool(service, lambda: human).execute('actual-list', {'action': 'list'})
        assert not result.is_error and len(result.content[0].text.encode()) <= 256 * 1024
        assert len(await service.list()) == 70 and not host.model_calls


async def collect_pages(tool, first, *, after_first=None):
    import hashlib

    page = json.loads(first.content[0].text)
    assert page['format'] == 'xdog.goal_data_page.v1'
    selector, checksum = page['selector'], page['sha256']
    data = bytearray()
    if after_first is not None:
        await after_first()
    while True:
        assert page['selector'] == selector and page['sha256'] == checksum
        assert page['offset'] == len(data)
        block = page['text'].encode('ascii')
        assert len(block) <= 64 * 1024
        data.extend(block)
        if page['next_query'] is None:
            assert page['next_offset'] is None and len(data) == page['total_bytes']
            break
        assert page['next_offset'] == len(data)
        result = await tool.execute('actual-next-page', page['next_query'])
        assert not result.is_error and len(result.content[0].text.encode()) <= 256 * 1024
        page = json.loads(result.content[0].text)
    assert hashlib.sha256(data).hexdigest() == checksum
    return json.loads(data)


async def test_parent_pages_keep_committed_version_after_another_actual_mutation(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        goal = large_goal(amount=60)
        await service.store.put(goal, expected_version=0)
        tool = create_goal_tool(service, lambda: human)
        result = await tool.execute('actual-committed-action',
            {'action': 'add_task', 'goal_id': goal.id, 'description': 'Exact committed addition'})
        assert not result.is_error

        async def change_current():
            await service.add_task(goal.id, 'Later mutation must not enter prior pages')

        saved = await collect_pages(tool, result, after_first=change_current)
        assert saved['version'] == 2 and len(saved['tasks']) == 61
        assert saved['tasks'][-1]['description'] == 'Exact committed addition'
        assert (await service.status(goal.id)).version == 3 and not host.model_calls


async def test_actual_bound_worker_pages_remain_original_and_cannot_select_another_role(tmp_path):
    from dataclasses import asdict

    async with actual_service(tmp_path) as (service, host, human):
        (tmp_path / 'result.txt').write_text('Actual allowed evidence')
        goal = large_goal(amount=60)
        await service.store.put(goal, expected_version=0)
        assigned = await service.assign(goal.id, goal.tasks[0].id, 'Original exact assignment')
        attempt = assigned.tasks[0].attempts[-1]
        await host.wait(attempt.worker)
        original = await service.worker_context(goal.id, goal.tasks[0].id, attempt.id)
        tool = create_context_tool(service, goal.id, task_id=goal.tasks[0].id, attempt_id=attempt.id)
        first = await tool.execute('context-first', {})

        async def change_contract():
            await service.revise(goal.id, 'New current contract', ('New current criterion',), 'Actual user choice',
                                 context=human)

        assert await collect_pages(tool, first, after_first=change_contract) == json.loads(json.dumps(asdict(original)))
        for query in ({'goal_id': 'other'}, {'version': original.version}, {'attempt_id': 'other'},
                      {'owner': 'other'}, {'offset': True}, {'offset': -1}, {'limit': 65537}):
            assert (await tool.execute('reject-context-query', query)).is_error
        assert len(host.model_calls) == 3


async def test_collection_pages_are_exact_and_refuse_to_splice_changed_revisions(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        for number in range(70):
            await service.store.put(Goal(f'goal-{number}', f'{number}: ' + 'x' * 4000,
                'Outcome', ('Criterion',)), expected_version=0)
        tool = create_goal_tool(service, lambda: human)
        first = await tool.execute('first-collection', {'action': 'list'})
        rows = await collect_pages(tool, first)
        assert len(rows) == 70 and {row['id'] for row in rows} == {f'goal-{i}' for i in range(70)}
        next_query = json.loads(first.content[0].text)['next_query']
        await service.add_task('goal-0', 'New facts invalidate collection revision')
        changed = await tool.execute('stale-collection', next_query)
        assert changed.is_error and 'collection changed' in changed.content[0].text.lower()
        assert not host.model_calls


async def test_data_queries_reject_invalid_offsets_versions_and_changed_owner(tmp_path):
    async with actual_service(tmp_path) as (service, host, human):
        goal = await service.create('Small', 'Exact contract', ('Criterion',), context=human)
        tool = create_goal_tool(service, lambda: human)
        plain = await tool.execute('small-status', {'action': 'status', 'goal_id': goal.id})
        assert json.loads(plain.content[0].text)['id'] == goal.id  # Small original API is unchanged.
        for query in ({'offset': True}, {'offset': -1}, {'offset': 10**9}, {'limit': True}, {'limit': 65537},
                      {'version': True}, {'version': 0}):
            result = await tool.execute('bad-page', {'action': 'read', 'goal_id': goal.id, 'version': 1, **query})
            assert result.is_error
        page = await tool.execute('actual-small-page',
            {'action': 'read', 'goal_id': goal.id, 'version': 1, 'limit': 31})
        assert (await collect_pages(tool, page))['id'] == goal.id
        host.owner = Owner('another-current-owner', tmp_path)
        assert (await tool.execute('cross-owner', {'action': 'read', 'goal_id': goal.id, 'version': 1})).is_error


async def test_actual_readonly_reviewer_pages_stay_bound_to_original_contract(tmp_path):
    from dataclasses import asdict, replace

    async with actual_service(tmp_path) as (service, host, human):
        (tmp_path / 'result.txt').write_text('Actual independent reviewer evidence')
        initial = large_goal(amount=60)
        goal = replace(initial, tasks=tuple(replace(task, state='cancelled') for task in initial.tasks))
        await service.store.put(goal, expected_version=0)
        reviewing = await service.request_complete(goal.id, paths=('result.txt',))
        review = reviewing.review
        await host.wait(review.worker)
        original = await service.review_context(goal.id, review.id)
        tool = create_context_tool(service, goal.id, review_id=review.id)
        first = await tool.execute('review-context-first', {})
        await service.revise(goal.id, 'New actual user outcome', ('New actual criterion',),
                             'User revision', context=human)
        assert await collect_pages(tool, first) == json.loads(json.dumps(asdict(original)))
        assert (await tool.execute('reject-other-review', {'review_id': 'other'})).is_error
        assert [role for role, _turn in host.model_calls] == ['reviewer', 'reviewer']


async def test_data_rendering_is_owned_and_rechecks_actual_owner_after_yield(tmp_path, monkeypatch):
    import asyncio
    import threading

    from xdog.skills.goal import tools as module

    async with actual_service(tmp_path) as (service, host, human):
        goal = large_goal(amount=60)
        await service.store.put(goal, expected_version=0)
        tool = create_goal_tool(service, lambda: human)
        entered, release = threading.Event(), threading.Event()
        original = module.response
        threads = []

        def actual_render(*args, **kwargs):
            result = original(*args, **kwargs)
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(module, 'response', actual_render)
        work = asyncio.create_task(tool.execute('actual-read', {'action': 'status', 'goal_id': goal.id}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            host.owner = Owner('another-current-owner', tmp_path)
            release.set()
            result = await work
            assert result.is_error and 'owner' in result.content[0].text.lower()
            assert all(thread != threading.get_ident() for thread in threads) and not host.model_calls
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)


async def test_packaged_reference_explains_data_pages_and_committed_receipts(tmp_path):
    from xdog.agent.skills.manager import SkillManager

    reference = SkillManager(tmp_path / 'no-host').load_skill('goal')
    assert reference.packaged
    for term in ('next_query', 'snapshot_committed', 'offset', 'version', 'original'):
        assert term in reference.content, 'Packaged reference omitted exact continuation semantics'
