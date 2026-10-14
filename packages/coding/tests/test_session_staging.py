"""Transactional extension preparation: failed provider restore cannot retire current owners or rewrite targets."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

ALIAS = 'ext-stagedprovider-local/choice'


def entries(**flags):
    return {'stagedprovider': [{'id': 'flags', 'kind': 'note', 'data': flags}]}


@pytest.mark.parametrize('operation', ['resume'])
@pytest.mark.parametrize('scenario', ['success'])
async def test_actual_coding_navigation_stages_target_entries_and_preserves_original_until_commit(
    tmp_path, monkeypatch, operation, scenario,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    marker = tmp_path / 'staging-entered'
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['stagedprovider'], 'extension_dir': str(root),
                                   'extension_settings': {'stagedprovider': {'marker': str(marker)}}}))
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage()),
                                      config=AgentConfig(model='opaque/original')),
        session_data=manager.create_session(model='opaque/original', working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))
    ui = InteractiveMode(session)
    target_entries = entries(allow=scenario not in {'missing', 'failure'}, fail=scenario == 'failure',
                             hold=scenario == 'cancel')
    target_messages = [UserMessage(content='TARGET-OWNED-CONTEXT'), AssistantMessage()]
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await session.initialize_extensions()
        session.agent.replace_messages([UserMessage(content='ORIGINAL-LIVE-CONTEXT'), AssistantMessage()])
        session._persist()
        original_id, original_data, original_ext = session.session_id, session.session_data, session.extensions
        original_mcp, original_plan, original_children = session.mcp, session.plan, session.children
        original_state = session.agent.state
        old_process = original_ext._manager._clients['stagedprovider'].process
        if operation == 'resume':
            target = manager.create_session(model=ALIAS, working_dir=tmp_path)
            target.messages = target_messages
            target.extension_entries = target_entries
            target.settings = {'thinking_level': 'low'}
            manager.save_session(target)
            command, args = 'resume', target.session_id
        elif operation == 'branch':
            from xdog.agent.messages import messages_to_dicts
            session.session_data.branches.append({'branch_id': 'target', 'model': ALIAS,
                'messages': messages_to_dicts(target_messages), 'extension_entries': target_entries,
                'settings': {'thinking_level': 'low'}, 'plan': None})
            session._persist()
            target = session.session_data
            command, args = 'branch', 'target'
        else:
            from xdog.agent.messages import messages_to_dicts
            target = manager.create_session(model='opaque/unselected-head', working_dir=tmp_path)
            target.messages = [UserMessage(content='UNSELECTED-LIVE-TARGET-HEAD')]
            target.branches = [{'branch_id': 'target', 'branch_point': 1, 'model': ALIAS,
                'messages': messages_to_dicts(target_messages), 'extension_entries': target_entries,
                'settings': {'thinking_level': 'low'}, 'plan': None}]
            manager.save_session(target)
            command = 'branch' if operation == 'crossbranch' else 'goto'
            args = target.session_id + ' target' + (' 2' if operation == 'goto' else '')
        before = manager._session_path(target.session_id).read_bytes()
        before_entries = session.extensions.entries.snapshot()
        if scenario == 'save-failure':
            original_save = manager.save_session
            def fail_target(value):
                if value.model == ALIAS:
                    raise OSError('offline target save')
                return original_save(value)
            monkeypatch.setattr(manager, 'save_session', fail_target)
        ui._editor.set_text('RETAINED-TRANSITION-DRAFT')
        ui._handle_slash_command(command, args)
        if scenario == 'cancel':
            await wait(marker.exists)
            assert session.extensions is original_ext and old_process.returncode is None
            ui._cancel_command()
        await wait(lambda: ui._command is None)
        assert ui._editor.get_text() == 'RETAINED-TRANSITION-DRAFT'
        assert session.permissions.mode == 'deny'
        if scenario == 'success':
            assert session.model == ALIAS and session.agent.context_window == 32000
            assert session.agent.options.thinking == 'low'
            assert 'TARGET-OWNED-CONTEXT' in str(session.messages)
            observed = json.loads(await session.extensions.command('ext-stagedprovider-status', ''))
            assert observed['starts'] == 1 and observed['session_id'] == session.session_id
            assert session.extensions is not original_ext and original_ext._closed
            committed = manager.load_session(session.session_id)
            assert committed.model == ALIAS
            assert sum('prepared' in row['data'] for row in committed.extension_entries['stagedprovider']) == 1
            # Follow-on owner writes must target the adopted state, not the staged buffer or old session.
            session.extensions.entries.append('stagedprovider', [{'kind': 'note', 'data': {'live': 'adopted'}}])
            assert 'adopted' in str(manager.load_session(session.session_id).extension_entries)
            await session.send_message('run adopted provider')
            assert 'STAGED-OWNED-ANSWER' in str(session.messages)
        else:
            assert session.session_id == original_id and session.session_data is original_data
            assert session.agent.state == original_state and session.extensions is original_ext
            assert session.mcp is original_mcp and session.plan is original_plan
            assert session.children is original_children
            assert not original_ext._closed and old_process.returncode is None
            assert session.extensions.entries.snapshot() == before_entries
            assert manager._session_path(target.session_id).read_bytes() == before
            assert 'TARGET-OWNED-CONTEXT' not in str(ui._chat_log.render(120))
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
