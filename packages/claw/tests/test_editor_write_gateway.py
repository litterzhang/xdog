"""Editor proposal capability and transport failures cannot mutate a foreign/native draft."""
from __future__ import annotations

import asyncio

import pytest

from packages.claw.tests.test_editor_write_ui import approve, editor_server
from packages.claw.tests.test_extension_message_gateway import handshake, peer
from packages.claw.tests.test_extension_message_ui import native_ui


@pytest.mark.parametrize('version', [None, True, '1', 0, 1])
async def test_editor_write_negotiates_separately_from_old_command_editor(tmp_path, version):
    async with editor_server(tmp_path) as (_, _, owner, config, calls, _marker, _release):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_editor=1,
                **({'extension_editor_write': version} if version is not None else {}))
            enabled = type(version) is int and version == 1
            assert caps['extension_editor_write'] == int(enabled)
            assert bool(caps['extension_editor_write_owner']) is enabled
            if not enabled:
                await send({'type': 'extension_control', 'request_id': 'off', 'group_id': 'main',
                            'session_id': owner.meta.session_id, 'action': 'command', 'name': 'ext-editorwrite-write'})
                result = await receive('extension_result')
                assert result['success'] and result['output'] == 'EDITOR-WRITE-False'
            old_generation = caps['extension_editor_write_generation']
            caps = await handshake(send, receive, extension_editor=0)
            assert caps['extension_editor_write'] == int(enabled)
            assert caps['extension_editor_write_generation'] == old_generation and not calls


async def test_lost_commit_frame_is_not_replayed_or_restored_into_parent_editor(tmp_path, monkeypatch):
    async with editor_server(tmp_path) as (_, _, owner, config, calls, _marker, _release):
        real_write = asyncio.StreamWriter.write
        failed = []
        def write(writer, data):
            if b'"type": "extension_editor_write_commit"' in data:
                failed.append(data)
                raise BrokenPipeError('Injected failure before any editor commit bytes are delivered')
            return real_write(writer, data)
        async with native_ui(config) as (ui, wait):
            monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
            ui._editor.set_text('PRIVATE DRAFT KEPT')
            ui._editor._cursor = 5
            ui._extension_command('ext-editorwrite-write', '')
            await wait(lambda: ui._extension_dialog_panel is not None)
            approve(ui._extension_dialog_panel)
            await wait(lambda: ui._extension_pending is None)
            await wait(lambda: not ui._editor_write_reviews._pending)
            assert len(failed) == 1 and not calls and not owner.agent.messages
            assert ui._editor.get_text() == 'PRIVATE DRAFT KEPT' and ui._editor._cursor == 5
            ui._queue_request({'type': 'ping'})
            await wait(lambda: ui._connection_ready)
            assert len(failed) == 1 and ui._editor.get_text() == 'PRIVATE DRAFT KEPT'
