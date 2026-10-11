"""Responses image tool outputs remain typed and associated with their call identity."""
from __future__ import annotations

import pytest
from xdog.ai.protocols.openai_responses import _convert_tool_result, context_to_responses_input
from xdog.ai.proxy_responses import InvalidRequest, parse_request
from xdog.ai.types import AssistantMessage, Context, ImageContent, Model, TextContent, ToolCall, ToolResultMessage

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def result():
    return ToolResultMessage(tool_call_id='call_owned', tool_name='screenshot', content=(
        TextContent(text='before'), ImageContent(data=PNG, mime_type='image/png'), TextContent(text='after')))


def test_vision_tool_output_remains_typed_ordered_and_call_scoped_instead_of_fabricating_user_input():
    context = Context(messages=(AssistantMessage(content=(ToolCall(id='call_owned', name='screenshot'),)), result()))
    wire = context_to_responses_input(context, Model(id='vision', input=('text', 'image')))
    output = next(item for item in wire if item.get('type') == 'function_call_output')
    assert output == {'type': 'function_call_output', 'call_id': 'call_owned', 'output': [
        {'type': 'input_text', 'text': 'before'},
        {'type': 'input_image', 'detail': 'auto', 'image_url': 'data:image/png;base64,' + PNG},
        {'type': 'input_text', 'text': 'after'}]}
    assert not any(item.get('role') == 'user' for item in wire)
    _, restored, _, _ = parse_request({'model': 'vision', 'input': wire})
    assert restored.messages[-1] == result()


def test_text_only_model_does_not_acquire_vision_from_tool_result_and_legacy_text_output_is_unchanged():
    output = _convert_tool_result(result(), Model(id='text', input=('text',)))
    assert output['output'] == 'before\nafter' and PNG not in str(output)
    text = ToolResultMessage(tool_call_id='call_owned', tool_name='read', content=(TextContent(text='plain'),))
    assert _convert_tool_result(text, Model(id='vision', input=('text', 'image')))['output'] == 'plain'


@pytest.mark.parametrize('bad', [{'type': 'input_image', 'image_url': 'https://no-fetch.invalid/image.png'},
                                 {'type': 'input_image', 'image_url': 'data:image/png;base64,not-base64!'},
                                 {'type': 'input_file', 'file_id': 'unsupported'}])
def test_inbound_tool_images_do_not_fetch_remote_urls_or_accept_invalid_binary_or_unsupported_files(bad):
    with pytest.raises(InvalidRequest):
        parse_request({'model': 'vision', 'input': [
            {'type': 'function_call', 'call_id': 'owned', 'name': 'image', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'owned', 'output': [bad]}]})
