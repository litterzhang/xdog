"""Saved source viewing uses actual client/session owners without navigation or model work."""
from __future__ import annotations

import pytest
from xdog.agent import Agent
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.transcript_transfer import TransferOptions


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_unavailable_or_loading_saved_view_cannot_export_or_use_live_source(tmp_path, kind):
    if kind == 'claw':
        ui = ChatApp('/tmp/saved-source-offline.sock')
        ui._connection_ready = True
        ui._source_supported = True
        ui._gateway_session_id = 'owner'
        ui._saved_source_supported = False
    else:
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage())),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_repo=None, bash=None)
        ui = InteractiveMode(session)
    try:
        ui._chat_log.add_user('MUST-NOT-BECOME-ARCHIVE')
        ui._open_transcript(saved=('missing-saved-session', 'missing-branch'))
        captured = []
        ui._outputs.start = lambda *args, **kwargs: captured.append(args)
        ui._begin_output(TransferOptions('copy', 'conversation', 'text'))
        assert not captured and not ui._transcript_panel.settled
        assert 'MUST-NOT-BECOME-ARCHIVE' not in str(ui._transcript_panel.records)
        if kind == 'claw':
            assert ui._source_request_id is None
    finally:
        ui._request_exit()
        if kind == 'coding':
            session.dispose()


@pytest.mark.parametrize('scenario', ['stale-id', 'foreign-owner', 'after-close'])
def test_coding_saved_receipt_never_replaces_current_or_closed_view(tmp_path, scenario):
    from xdog.tui.transcript_browser import TranscriptRecord
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage())),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None)
    ui = InteractiveMode(session)
    try:
        ui._open_transcript(saved=('missing', 'archive'))
        identifier, context = ui._saved_request, ui._saved_context
        if scenario == 'after-close':
            ui._close_transcript()
        elif scenario == 'foreign-owner':
            ui._output_epoch += 1
        ui._handle_ui_event({'type': 'saved_source_ready', 'id': 'other' if scenario == 'stale-id' else identifier,
            'context': context, 'records': (TranscriptRecord('secret', 'user', 'input', 'LATE-SAVED-DATA'),)})
        assert 'LATE-SAVED-DATA' not in str(ui._saved_records)
    finally:
        ui._request_exit()
        session.dispose()
