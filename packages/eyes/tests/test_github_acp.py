"""Mock GitHub reads through the original executor to a real third-party ACP process."""
import asyncio
import time
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.executor import observe
from xdog.eyes.handlers.acp import AcpHandler
from xdog.eyes.models import ConditionDetectorDef, ObserverDef, Predicate, RecordDetectorDef, RouteDef
from xdog.eyes.sources.remote import RemoteSampler

from packages.eyes.tests.test_acp_enqueue import enqueue_definition
from packages.eyes.tests.test_acp_handler import rows
from packages.eyes.tests.test_github_source import issue, source
from packages.eyes.tests.test_http_source import Transport


@pytest.mark.parametrize('resource', ['issues', 'workflow_runs'])
async def test_github_change_to_acp_enqueue_not_an_execution_receipt(tmp_path, resource):
    calls = []
    def handle(request):
        calls.append(request)
        if resource == 'issues':
            return httpx.Response(200, json=[] if len(calls) == 1 else [issue()])
        run = issue(status='in_progress' if len(calls) == 1 else 'completed',
                    conclusion=None if len(calls) == 1 else 'failure', run_attempt=1)
        return httpx.Response(200, json={'total_count': 1, 'workflow_runs': [run]})
    record = tmp_path/'peer.jsonl'
    target = replace(enqueue_definition(record), cwd=tmp_path)
    detector = (RecordDetectorDef(id='changes', source='github') if resource == 'issues' else
        ConditionDetectorDef(id='changes', source='github', all=(Predicate('/status', 'eq', 'completed'),
                                                                Predicate('/conclusion', 'eq', 'failure'))))
    event_type = 'record.created' if resource == 'issues' else 'condition.met'
    definition = ObserverDef(2, 'github', (source(resource, poll_interval_seconds=1),), (detector,),
                             (target,), (RouteDef('route', 'changes', target.id, (event_type,)),))
    sampler, cancel, events = RemoteSampler(Transport(handle)), asyncio.Event(), []
    handler = AcpHandler(target)
    await handler.start()
    def emit(event):
        events.append(event)
        if event.kind == 'delivery_queued':
            cancel.set()
    async def never(_):
        raise AssertionError('No file scanner or hidden shell execution')
    try:
        async with asyncio.timeout(8):
            await observe(definition, registration_id='github-registration', scanner=never,
                deliver=handler.deliver, emit=emit, clock=time.monotonic, sleep=asyncio.sleep,
                cancel=cancel, remote_sampler=sampler)
        sent = [row['params'] for row in rows(record)
                if row['kind'] == 'extension' and row.get('method') == 'xdog/enqueue']
        assert len(sent) == 1 and sent[0]['evidence'][0]['type'] == event_type
        assert sent[0]['evidence'][0]['evidence'][0]['url'].startswith('https://github.com/')
        assert not [row for row in rows(record) if row['kind'] in {'prompt', 'load'}]
        queued = next(event for event in events if event.kind == 'delivery_queued')
        assert queued.receipt_status == 'queued'  # Intake only, not a model turn or chat delivery.
    finally:
        await handler.close()
