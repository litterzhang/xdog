"""Actual xdog task responses checked by pinned independent legacy SDK vocabulary.

Schema validation is not serving-runtime or blanket interoperability evidence.
No install/network/model/provider calls. Explicit external SDK root required.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from xdog.agent.mcp.elicitation_owner import ElicitationOwner
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.sampling_owner import SamplingOwner
from xdog.agent.mcp.task_registry import TaskRegistry

from packages.agent.tests.test_mcp_elicitation import form
from packages.agent.tests.test_mcp_sampling_owner import answer, request, selection, until

SDK_ROOT = os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT')
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent MCP SDK 2.3.1 required')


@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
async def test_actual_task_control_result_metadata_and_status_match_official_schemas(tmp_path, family):
    registry, reverse, wire, reviews = TaskRegistry(), ReverseRequests(), [], []
    reverse.active, reverse.tasks = True, registry
    async def execute(value, chosen, cancel):
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
    if family == 'sampling':
        owner = SamplingOwner('view', reviews.append, execute, lambda: True)
        reverse.handler, reverse.sampling_task_handler = owner.handle, owner.handle_task
        source = request()
    else:
        owner = ElicitationOwner('view', reviews.append, lambda: True)
        reverse.elicitation_handler, reverse.elicitation_task_handler = owner.handle, owner.handle_task
        source = form()
    source['params']['task'] = {'ttl': 60000}
    async def write(value):
        wire.append(value)
    async def control(rid, method, params):
        envelope = {'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}
        schemas = {'tasks/get': 'GetTaskRequestSchema', 'tasks/list': 'ListTasksRequestSchema',
                   'tasks/result': 'GetTaskPayloadRequestSchema', 'tasks/cancel': 'CancelTaskRequestSchema'}
        add(schemas[method], envelope)
        assert reverse.admit(envelope, write, timeout=2)
        await until(lambda: any(row.get('id') == rid for row in wire))
        return next(row for row in wire if row.get('id') == rid)['result']
    async def approve(event, response=None):
        if family == 'sampling':
            answer(owner, event, **({'selection': selection()} if event['phase'] == 'input' else {}))
        else:
            await owner.answer(event['id'], lease='view', phase=event['phase'], receipt=event['receipt'],
                               sha256=event['sha256'], response=response)
    cases = []
    def add(schema, value):
        cases.append({'schema': schema, 'value': value})
    process = None
    try:
        add('ClientCapabilitiesSchema', reverse.advertised())
        add('ClientTasksCapabilitySchema', reverse.advertised()['tasks'])
        add('TaskCreationParamsSchema', source['params']['task'])
        if family == 'sampling':
            from xdog.agent.mcp.sampling import request as validate_request
            optional_choice = request()
            optional_choice['params'].update(tools=[{'name': 'proposal', 'inputSchema': {'type': 'object'}}],
                                             toolChoice={})
            assert validate_request(optional_choice) == optional_choice
            add('CreateMessageRequestSchema', optional_choice)
            add('ToolChoiceSchema', optional_choice['params']['toolChoice'])
            from xdog.agent.mcp.sampling import result as validate_result
            for fields in ({'tools': []}, {'tools': [], 'toolChoice': {}},
                           {'toolChoice': {'mode': 'auto'}}, {'toolChoice': {'mode': 'none'}}):
                empty_choice = request()
                empty_choice['params'].update(fields)
                assert validate_request(empty_choice) == empty_choice
                add('CreateMessageRequestSchema', empty_choice)
                wide = {'model': 'explicit-model', 'role': 'assistant', 'content': [
                    {'type': 'text', 'text': 'FIRST'}, {'type': 'text', 'text': 'SECOND'}]}
                assert validate_result(wide, empty_choice) == wide
                add('CreateMessageResultWithToolsSchema', wide)
            from dataclasses import replace

            from xdog.agent.mcp.sampling_context import project
            from xdog.tui.utils import redact_sensitive_text

            from packages.agent.tests.test_mcp_sampling_context import turn
            history = list(turn('alias', 'call', 'PUBLIC'))
            history[2] = replace(history[2], details={'mcp_server': 'owned',
                'structured_content_present': True, 'structured_content': {
                    'answer': 42, 'credential': 'PRIVATE NEVER SHARE'}})
            projected = project(tuple(history), {'alias': ('owned', 'lookup')},
                                'owned', 'thisServer', redact_sensitive_text)
            assert 'PRIVATE NEVER SHARE' not in json.dumps(projected)
            projected_request = request()
            projected_request['params']['messages'] = projected
            assert validate_request(projected_request) == projected_request
            add('CreateMessageRequestSchema', projected_request)
            for row in projected:
                add('SamplingMessageSchema', row)
            structured_block = next(row['content'][0] for row in projected
                                    if row['content'][0]['type'] == 'tool_result')
            add('ToolResultContentSchema', structured_block)

        add('CreateMessageRequestSchema' if family == 'sampling' else 'ElicitRequestSchema', source)
        assert reverse.admit(source, write, timeout=2)
        await until(lambda: reviews and any(row.get('id') == source['id'] for row in wire))
        created = next(row for row in wire if row.get('id') == source['id'])['result']
        identifier = created['task']['taskId']
        add('CreateTaskResultSchema', created)
        add('GetTaskResultSchema', await control('get', 'tasks/get', {'taskId': identifier}))
        add('ListTasksResultSchema', await control('list', 'tasks/list', {}))
        await approve(reviews[0], {'action': 'accept', 'content': {'name': 'PRIVATE'}})
        await until(lambda: len(reviews) == 2)
        await approve(reviews[1], {'action': 'accept'})
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        output = await control('result', 'tasks/result', {'taskId': identifier})
        add('GetTaskPayloadResultSchema', output)
        add('CreateMessageResultSchema' if family == 'sampling' else 'ElicitResultSchema', output)
        related = output['_meta']['io.modelcontextprotocol/related-task']
        assert related == {'taskId': identifier}
        add('RelatedTaskMetadataSchema', related)
        add('CancelTaskResultSchema', await control('cancel', 'tasks/cancel', {'taskId': identifier}))
        await until(lambda: not reverse.status._jobs)
        notices = [row for row in wire if row.get('method') == 'notifications/tasks/status']
        assert notices and all('PRIVATE' not in json.dumps(row) for row in notices)
        for row in notices:
            add('TaskStatusNotificationSchema', row)
            assert row['params']['_meta']['io.modelcontextprotocol/related-task'] == related
            add('RelatedTaskMetadataSchema', row['params']['_meta']['io.modelcontextprotocol/related-task'])
        payload = tmp_path / 'cases.json'
        payload.write_text(json.dumps(cases))
        process = await asyncio.create_subprocess_exec(NODE, str(Path(__file__).parent / 'fixtures/mcp_official_sdk'
            / 'task_contract.mjs'), str(payload), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, stderr.decode()
        receipt = json.loads(stdout)
        assert receipt['version'] == '2.3.1'
        assert set(receipt['validated']) == {row['schema'] for row in cases}
        (tmp_path / 'official-task-schema-receipt.json').write_text(json.dumps(receipt, indent=2))
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        await reverse.close()
        await owner.close()
