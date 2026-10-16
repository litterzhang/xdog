"""Native persistence/history/export survives removal of dedicated RPC source readers."""
from __future__ import annotations

import json
import threading

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.coding.core.transcript_source import source_records
from xdog.tui.transcript_transfer import TransferOptions, prepare_transfer, write_snapshot

from packages.coding.tests.extension_session_helpers import PNG


def test_local_typed_history_and_images_roundtrip_without_rpc(coding_session, tmp_path):
    image = ImageContent(data=PNG, mime_type='image/png')
    messages = [
        UserMessage(content=(TextContent(text='Inspect retained image'), image)),
        AssistantMessage(content=(ToolCall(id='read-1', name='read_file', arguments={'path': 'a.py'}),)),
        ToolResultMessage(tool_call_id='read-1', tool_name='read_file',
                          content=(TextContent(text='Retained tool result'), image)),
        AssistantMessage(content=(TextContent(text='Retained final answer'),)),
    ]
    coding_session.agent.replace_messages(messages)
    coding_session._persist()
    saved = coding_session.session_manager.load_session(coding_session.session_id)
    assert saved.messages == messages
    assert saved.messages[1].content[0].id == saved.messages[2].tool_call_id
    assert saved.messages[0].content[1] == saved.messages[2].content[1] == image

    live = source_records(coding_session.session_manager, coding_session.session_id,
                          live_messages=tuple(coding_session.messages))
    restored = source_records(coding_session.session_manager, coding_session.session_id)
    assert [(row.role, row.body) for row in restored] == [(row.role, row.body) for row in live]
    transfer = prepare_transfer(TransferOptions('export', 'all', 'json'), restored, coding_session.session_id)
    assert transfer.entries == len(restored)
    assert 'Retained tool result' in transfer.text and 'Retained final answer' in transfer.text
    assert PNG not in transfer.text and PNG.encode() not in transfer.data
    target = tmp_path / 'history.json'
    write_snapshot(transfer.data, target, threading.Event())
    assert json.loads(target.read_text()) == json.loads(transfer.data)
    with pytest.raises(FileExistsError):
        write_snapshot(b'NEVER OVERWRITE', target, threading.Event())
    assert target.read_bytes() == transfer.data
    assert coding_session.messages == messages and coding_session.runtime is None
