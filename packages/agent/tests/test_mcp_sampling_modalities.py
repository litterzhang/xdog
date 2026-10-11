"""Finite modality/model wire corpus; no live model or data URI retrieval."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import pytest
from xdog.agent.mcp.sampling import request as validate_request
from xdog.agent.mcp.sampling_executor import SamplingExecutor
from xdog.ai.protocols.anthropic_messages import _convert_message as anthropic_message
from xdog.ai.protocols.openai_responses import _convert_tool_result as responses_tool_result
from xdog.ai.types import ToolResultMessage

from packages.agent.tests.test_mcp_sampling import paired_messages
from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_owner import request, selection


@pytest.mark.parametrize('payload', [None, False, [1, 'string'], {'answer': 42, 'nested': {'flag': True}}])
async def test_reviewed_structured_tool_result_reaches_actual_model_wire_not_private_details_only(payload):
    adapter, source = Adapter(), request()
    source['params']['messages'] = paired_messages()
    source['params']['messages'][1]['content'][0]['structuredContent'] = payload
    original = copy.deepcopy(source)
    assert validate_request(source) == original
    await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    message = next(row for row in adapter.calls[0][1].messages if isinstance(row, ToolResultMessage))
    # Private host details must not be serialized. Only explicit protocol content
    # is model input, including present null/false rather than truthiness loss.
    wire_message = replace(message, details={'HOST_WRAPPER_SECRET': 'DO NOT SEND'})
    serialized = 'MCP structuredContent (JSON):\n' + json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    assert serialized in responses_tool_result(wire_message)['output']
    anthropic = anthropic_message(wire_message)
    assert serialized in [row['text'] for row in anthropic['content'][0]['content'] if row['type'] == 'text']
    assert 'HOST_WRAPPER_SECRET' not in json.dumps(anthropic)
    assert source == original and message.details == payload


async def test_absent_structured_tool_result_does_not_invent_null_or_duplicate_plain_content():
    adapter, source = Adapter(), request()
    source['params']['messages'] = paired_messages()
    await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    messages = [row for row in adapter.calls[0][1].messages if isinstance(row, ToolResultMessage)]
    assert [responses_tool_result(row)['output'] for row in messages] == ['SECOND', 'FIRST']


@pytest.mark.parametrize('fields', [{'tools': []}, {'tools': [], 'toolChoice': {}},
                                   {'toolChoice': {'mode': 'auto'}}, {'toolChoice': {'mode': 'none'}}])
def test_empty_tool_catalog_and_optional_auto_none_choice_are_not_required_tool_grants(fields):
    from xdog.agent.mcp.sampling import result as validate_result
    source = request()
    source['params'].update(fields)
    assert validate_request(source) == source
    response = {'model': 'explicit-model', 'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]}
    assert validate_result(response, source) == response


async def test_native_sampling_image_tool_context_reaches_responses_typed_wire_after_actual_model_check():
    from xdog.ai.protocols.openai_responses import context_to_responses_input
    from xdog.ai.types import ImageContent

    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
    adapter, source = Adapter(), request()
    source['params']['messages'] = paired_messages()
    source['params']['messages'][1]['content'][0]['content'].append({
        'type': 'image', 'mimeType': 'image/png', 'data': png})
    source['params']['messages'][1]['content'][0]['structuredContent'] = {'typed': True}
    await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    context = adapter.calls[0][1]
    native = next(message for message in context.messages if isinstance(message, ToolResultMessage))
    assert isinstance(native.content[1], ImageContent)
    wire = context_to_responses_input(context, adapter.resolve_model('explicit-model'))
    output = next(row for row in wire if row.get('type') == 'function_call_output')
    assert [part['type'] for part in output['output']] == ['input_text', 'input_image', 'input_text']
    assert output['output'][1]['image_url'] == 'data:image/png;base64,' + png
    assert 'MCP structuredContent' in output['output'][2]['text']


@pytest.mark.parametrize('modality', ['audio', 'assistant-image'])
async def test_explicit_custom_sampling_executor_can_review_and_release_typed_non_native_modalities(modality):
    from xdog.agent.mcp.sampling_owner import SamplingOwner

    from packages.agent.tests.test_mcp_sampling_owner import answer, until
    source, events, calls = request(), [], []
    block = ({'type': 'audio', 'data': 'AAEC', 'mimeType': 'audio/wav'} if modality == 'audio' else {
        'type': 'image', 'data': ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/'
                                'x8AAwMCAO+j5ioAAAAASUVORK5CYII='),
        'mimeType': 'image/png'})
    source['params']['messages'] = [
        {'role': 'assistant' if modality == 'assistant-image' else 'user', 'content': block}]
    async def execute(value, chosen, cancel):
        calls.append(copy.deepcopy(value))
        return {'model': chosen['model'], 'role': 'assistant', 'content': block}
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle_input(source))
    try:
        await until(lambda: events)
        assert events[0]['body']['params']['messages'][0]['content'] == block and not calls
        answer(owner, events[0], selection=selection())
        await until(lambda: len(events) == 2)
        assert events[1]['body']['content'] == block and not work.done()
        answer(owner, events[1])
        assert (await work)['content'] == block and calls[0] == source
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('profile', ['text-only', 'unsupported-mime', 'image-count'])
async def test_native_incompatible_image_profile_refuses_before_any_adapter_or_wire_conversion(profile):
    from xdog.ai.types import VisionLimits
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
    adapter, source = Adapter(), request()
    model = adapter.resolve_model('explicit-model')
    if profile == 'text-only':
        model = replace(model, input=('text',))
    elif profile == 'unsupported-mime':
        model = replace(model, vision_limits=VisionLimits(supported_media_types=('image/jpeg',)))
    else:
        model = replace(model, vision_limits=VisionLimits(max_prompt_images=1))
    adapter.resolve_model = lambda name: model
    source['params']['messages'][0]['content'] = [{'type': 'image', 'data': png, 'mimeType': 'image/png'}] * 2
    with pytest.raises(ValueError):
        await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    assert not adapter.calls


async def test_reviewed_string_provider_metadata_reaches_neutral_options_not_model_text_or_routing():
    adapter, source = Adapter(), request()
    source['params']['metadata'] = {'trace': 'APPROVED', 'model': 'not-a-routing-grant'}
    await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    model, context, options = adapter.calls[0]
    assert model == 'explicit-model' and options.metadata == (('trace', 'APPROVED'), ('model', 'not-a-routing-grant'))
    assert 'APPROVED' not in str(context) and context.tools is None


@pytest.mark.parametrize('value', [False, None, [1], {'nested': 'opaque'}])
async def test_nonrepresentable_native_provider_metadata_refuses_without_silent_drop_or_model_call(value):
    adapter, source = Adapter(), request()
    source['params']['metadata'] = {'vendor': value}
    from xdog.agent.mcp.stdio import McpError
    with pytest.raises(McpError, match='metadata'):
        await SamplingExecutor(adapter, ('explicit-model',), lambda: True)(source, selection(), asyncio.Event())
    assert not adapter.calls
