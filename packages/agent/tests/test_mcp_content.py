from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.content import MAX_BINARY_BYTES, content
from xdog.agent.mcp.context import McpDocument, prompt_blocks, resource_blocks
from xdog.ai.types import ImageContent, TextContent

FIXTURE = Path(__file__).parent / 'fixtures/mcp_schema_peer.py'
AUDIO = {'type': 'audio', 'data': 'AAECAw==', 'mimeType': 'audio/wav',
         'annotations': {'audience': ['user', 'assistant'], 'priority': 0.5,
                         'lastModified': '2024-01-01T00:00:00Z'}, '_meta': {'vendor/opaque': [False, None, 0]}}


@pytest.mark.parametrize('updates', [
    {'data': 'bad!'}, {'data': 1}, {'data': 'é'}, {'data': 'AA==\n'}, {'mimeType': None},
    {'mimeType': ''}, {'mimeType': 'audio/wav\r\nunsafe'}, {'mimeType': 'x' * 257}, {'_meta': []},
    {'_meta': {'nan': float('nan')}}, {'_meta': {'unicode': '\ud800'}}, {'annotations': None},
    {'annotations': {'audience': ['system']}}, {'annotations': {'audience': 'user'}},
    {'annotations': {'priority': True}}, {'annotations': {'priority': -0.1}},
    {'annotations': {'priority': 1.1}}, {'annotations': {'priority': float('inf')}},
    {'annotations': {'lastModified': 'not a date'}}, {'annotations': {'lastModified': '2024-02-30T00:00:00Z'}},
    {'annotations': {'lastModified': '2024-01-01T00:00:00'}},
])
def test_invalid_audio_annotations_and_nonfinite_metadata_cannot_pass_tool_or_prompt_boundary(updates):
    value = {**AUDIO, **updates}
    with pytest.raises(McpError):
        content(value)
    with pytest.raises(McpError):
        prompt_blocks([{'role': 'user', 'content': value}])


@pytest.mark.parametrize('value', [
    {'type': 'resource', 'resource': {'uri': 'test://x', 'text': 'x', 'blob': 'AA=='}},
    {'type': 'resource', 'resource': {'uri': 'test://x', 'blob': 'bad!'}},
    {'type': 'resource', 'resource': {'uri': 'test://x\0secret', 'text': 'x'}},
    {'type': 'resource', 'resource': {'uri': '', 'text': 'x'}},
    {'type': 'resource', 'resource': {'uri': 'test://x', 'text': True}},
    {'type': 'resource', 'resource': {'uri': 'test://x', 'blob': 'AA==', '_meta': None}},
    {'type': 'resource_link', 'uri': 'test://x', 'name': '', 'size': 1},
    {'type': 'resource_link', 'uri': 'test://x', 'name': 'x', 'size': True},
    {'type': 'resource_link', 'uri': 'test://x', 'name': 'x', 'annotations': {'audience': ['system']}},
    {'type': 'text', 'text': '\ud800'}, {'type': 'text', 'text': 'x', '_meta': ['wrong']},
])
def test_tool_resource_and_text_blocks_have_the_same_validation_as_previews(value):
    with pytest.raises(McpError):
        content(value)
    with pytest.raises(McpError):
        prompt_blocks([{'role': 'assistant', 'content': value}])


def test_binary_input_and_decoded_budgets_are_enforced_before_large_allocations():
    with pytest.raises(McpError, match='retention'):
        content({**AUDIO, 'data': 'A' * (4 * ((MAX_BINARY_BYTES + 2) // 3) + 4)})
    with pytest.raises(McpError, match='decoded'):
        content({**AUDIO, 'data': 'A' * (4 * ((MAX_BINARY_BYTES + 2) // 3))})


def test_audio_and_embedded_blob_metadata_are_lossless_inert_and_never_draft_as_text():
    normalized = content(AUDIO)
    assert normalized == AUDIO and normalized is not AUDIO
    normalized['_meta']['vendor/opaque'].append('changed')
    assert AUDIO['_meta']['vendor/opaque'] == [False, None, 0]
    blocks = prompt_blocks([{'role': 'user', 'content': AUDIO}, {'role': 'assistant', 'content': {
        'type': 'resource', 'resource': {'uri': 'https://must-not-fetch.invalid/document', 'blob': 'AAECAw==',
                                      '_meta': {'vendor/inner': 'INNER'}},
        'annotations': {'audience': ['user']}, '_meta': {'vendor/outer': 'OUTER'}}}])
    document = McpDocument('peer', 'prompt', 'data', blocks)
    assert all(block.data == 'AAECAw==' for block in blocks)
    assert 'Binary audio/wav' in document.text and 'INNER' in document.text and 'OUTER' in document.text
    assert 'vendor/opaque' in document.text and 'must-not-fetch.invalid' in document.text
    with pytest.raises(McpError, match='Binary'):
        document.draft_text()
    text = resource_blocks([{'uri': 'test://text', 'text': 'payload', '_meta': {'vendor/inner': 'retained'}}])
    assert 'retained' in text[0].text


@pytest.mark.parametrize('profile', ['modern-content', 'legacy-content'])
async def test_actual_tool_content_metadata_and_inert_audio_resource_link_survive(tmp_path, profile):
    log = tmp_path / 'requests'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(FIXTURE), profile, str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        result = await manager.tools()[0].execute('call', {'value': None})
        texts = '\n'.join(part.text for part in result.content if isinstance(part, TextContent))
        for marker in ('MCP-CONTENT-TEXT', 'MCP-CONTENT-AUDIO', 'MCP-CONTENT-LINK', 'MCP-CONTENT-METADATA'):
            assert marker in texts
        assert result.details['mcp_content_metadata'][0]['metadata']['annotations']['priority'] == 0.25
        assert any(isinstance(part, ImageContent) for part in result.content)
        assert len(result.details['mcp_content_metadata']) == 2
        assert 'data' not in result.details['mcp_content_metadata'][1]['metadata']
        assert result.details['mcp_content_metadata'][1]['metadata']['_meta']['fixture/image'] in texts
        assert result.details['mcp_content_metadata'][0]['metadata']['_meta']['fixture/provenance'] in texts
        assert 'AAECAw==' in texts and 'UklGRg==' in texts and 'must-not-fetch.invalid' in texts
        calls = [json.loads(line)['method'] for line in log.read_text().splitlines()]
        assert calls.count('tools/call') == 1 and 'resources/read' not in calls
    finally:
        await manager.aclose()


async def test_invalid_tool_binary_is_rejected_after_one_request_without_replay(tmp_path):
    log = tmp_path / 'requests'
    manager = McpManager({'peer': {'command': sys.executable,
        'args': [str(FIXTURE), 'modern-bad-content', str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        with pytest.raises(McpError, match='binary encoding'):
            await manager.tools()[0].execute('call', {'value': None})
        assert sum(json.loads(line)['method'] == 'tools/call' for line in log.read_text().splitlines()) == 1
    finally:
        await manager.aclose()


def test_unhashable_prompt_role_is_a_protocol_error_not_a_host_type_error():
    with pytest.raises(McpError, match='role'):
        prompt_blocks([{'role': [], 'content': {'type': 'text', 'text': 'payload'}}])


def test_inert_prompt_image_formats_remain_exportable_but_cannot_bypass_typed_model_image_admission():
    image = {'type': 'image', 'mimeType': 'image/gif',
             'data': 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'}
    preview = McpDocument('peer', 'prompt', 'inert image', prompt_blocks([{'role': 'user', 'content': image}]))
    assert preview.blocks[0].data == image['data'] and 'Binary image/gif' in preview.text
    with pytest.raises(McpError, match='Binary'):
        preview.draft_text()
    with pytest.raises(McpError, match='image content'):
        content(image)
    with pytest.raises(McpError, match='image content'):
        content({'type': 'image', 'mimeType': 'image/png', 'data': 'AA=='})


@pytest.mark.parametrize('block,expected', [
    ({'type': 'audio', 'mimeType': 'audio/wav', 'data': ''}, 'audio'),
    ({'type': 'image', 'mimeType': 'image/png', 'data': ''}, 'image'),
    ({'type': 'resource', 'resource': {'uri': 'test://empty', 'blob': ''}}, 'resource_blob'),
])
def test_empty_binary_payload_type_survives_preview_metadata_and_blocks_text_drafting(block, expected):
    from dataclasses import asdict
    block = {**block, '_meta': {'vendor/empty': True}}
    document = McpDocument('peer', 'prompt', 'empty', prompt_blocks([{'role': 'user', 'content': block}]))
    item = document.blocks[0]
    assert item.content_type == expected and item.is_binary and item.data == ''
    assert '0 bytes' in document.text and 'vendor/empty' in document.text
    exported = asdict(document)
    assert exported['blocks'][0]['data'] == '' and exported['blocks'][0]['content_type'] == expected
    with pytest.raises(McpError, match='Binary'):
        document.draft_text()


def test_empty_resource_blob_is_not_conflated_with_empty_text_or_legacy_text():
    from xdog.agent.mcp.context import McpBlock
    binary = resource_blocks([{'uri': 'test://empty', 'blob': '', 'mimeType': 'application/octet-stream'}])[0]
    text = resource_blocks([{'uri': 'test://empty', 'text': ''}])[0]
    assert binary.content_type == 'resource_blob' and binary.is_binary and not text.is_binary
    assert text.content_type == 'resource_text'
    assert McpDocument('peer', 'resource', 'text', (text,)).draft_text()
    assert not McpBlock('resource', text='legacy').is_binary
    assert McpBlock('resource', data='AA==').is_binary
    with pytest.raises(ValueError):
        McpBlock('resource', data='AA==', content_type='text')
    with pytest.raises(ValueError):
        McpBlock('resource', content_type='unimplemented')
