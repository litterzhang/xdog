"""Reviewed Coding selection persists without restoring permissions or broad defaults."""
from __future__ import annotations

import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools


async def create(tmp_path, data=None):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / 'sessions')
    data = data or store.create_session(model='offline/model', working_dir=tmp_path)
    agent = Agent(stream, config=AgentConfig(model='offline/model'), tools=get_default_tools(tmp_path))
    owner = AgentSession(agent=agent,
        session_data=data, session_manager=store, settings=SettingsManager(global_path=tmp_path / 'settings.json'),
        working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    return owner, calls


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


@pytest.mark.parametrize('selected', [None, [], ['current_time']])
async def test_durable_selection_restarts_without_default_expansion_or_policy_restore(tmp_path, selected):
    owner, calls = await create(tmp_path)
    restored = None
    try:
        await owner.initialize_extensions()
        owner.select_active_tools(selected, expected_revision=owner.agent.tool_selection_revision)
        stored = owner.session_manager.load_session(owner.session_id)
        assert stored.active_tools == (None if selected is None else tuple(selected))
        restored, restored_calls = await create(tmp_path, stored)
        if selected is not None:
            assert restored.agent.active_tools == ()  # No pre-initialization broad inventory grant.
        await restored.initialize_extensions()
        assert restored.agent.active_tool_selection == (None if selected is None else tuple(selected))
        assert restored.permissions.mode == 'deny' and not restored.agent.messages
        assert not calls and not restored_calls
    finally:
        if restored is not None:
            await close(restored)
        await close(owner)


async def test_failed_selection_save_keeps_runtime_and_persisted_state(tmp_path, monkeypatch):
    owner, calls = await create(tmp_path)
    try:
        await owner.initialize_extensions()
        owner.select_active_tools(['current_time'], expected_revision=owner.agent.tool_selection_revision)
        path = owner.session_manager._session_path(owner.session_id)
        original_bytes = path.read_bytes()
        original = owner.agent.active_tool_selection, owner.agent.tool_selection_revision
        def fail(*args, **kwargs):
            raise OSError('disk unavailable')
        monkeypatch.setattr(owner.session_manager, 'save_session', fail)
        with pytest.raises(OSError):
            owner.select_active_tools([], expected_revision=owner.agent.tool_selection_revision)
        assert original == (owner.agent.active_tool_selection, owner.agent.tool_selection_revision)
        assert owner.session_data.active_tools == ('current_time',) and path.read_bytes() == original_bytes
        assert not calls
    finally:
        await close(owner)


async def test_branch_checkpoint_and_new_session_keep_original_selection_scopes(tmp_path):
    owner, calls = await create(tmp_path)
    try:
        await owner.initialize_extensions()
        owner.select_active_tools(['current_time'], expected_revision=owner.agent.tool_selection_revision)
        owner.agent.replace_messages([UserMessage(content='saved'),
                                      AssistantMessage(content=(TextContent(text='done'),))])
        owner._persist()
        branch = owner.create_branch()
        owner.select_active_tools([], expected_revision=owner.agent.tool_selection_revision)
        assert owner.restore_branch(branch)
        assert owner.agent.active_tool_selection == ('current_time',)
        owner.select_active_tools([], expected_revision=owner.agent.tool_selection_revision)
        owner.navigate_checkpoint(owner.session_id, branch_id=branch, index=1)
        assert owner.agent.active_tool_selection == ('current_time',)
        previous = owner.session_id
        owner.new_session()
        assert owner.session_id != previous and owner.agent.active_tool_selection is None
        assert owner.switch_session(previous)
        assert owner.agent.active_tool_selection == ('current_time',)
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        await close(owner)


@pytest.mark.parametrize('bad', [False, {}, ['same', 'same'], [''], [1]])
def test_invalid_persisted_selection_refuses_instead_of_becoming_defaults(bad):
    with pytest.raises(ValueError):
        SessionData.from_dict({'session_id': 'saved', 'active_tools': bad})


async def test_missing_saved_tools_are_not_created_or_implicitly_enabled_later(tmp_path):
    owner, calls = await create(tmp_path)
    restored = None
    try:
        stored = owner.session_data.to_dict()
        stored['active_tools'] = ['missing-extension-tool', 'current_time']
        restored, _ = await create(tmp_path, SessionData.from_dict(json.loads(json.dumps(stored))))
        await restored.initialize_extensions()
        assert restored.agent.active_tool_selection == ('current_time',)
        from xdog.agent import AgentTool
        restored.agent.set_tools((*restored.agent.state.tools, AgentTool(name='missing-extension-tool', parameters={})))
        assert restored.agent.active_tool_selection == ('current_time',) and not calls
    finally:
        if restored is not None:
            await close(restored)
        await close(owner)


async def test_live_producer_cleanup_refuses_before_any_disk_write(tmp_path, monkeypatch):
    import asyncio
    owner, _ = await create(tmp_path)
    producer = None
    try:
        await owner.initialize_extensions()
        producer = asyncio.create_task(asyncio.Event().wait())
        owner.agent._producer_task = producer
        def forbidden(*args, **kwargs):
            raise AssertionError('Must refuse before writing')
        monkeypatch.setattr(owner.session_manager, 'save_session', forbidden)
        with pytest.raises(RuntimeError, match='idle agent'):
            owner.select_active_tools([], expected_revision=owner.agent.tool_selection_revision)
        assert owner.agent.active_tool_selection is None
    finally:
        if producer is not None:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        owner.agent._producer_task = None
        await close(owner)


async def test_real_extension_selection_is_restored_only_after_worker_inventory_initializes(tmp_path):
    from packages.coding.tests.test_rpc_extensions import session
    owner, calls = session(tmp_path)
    restored = None
    try:
        await owner.initialize_extensions()
        selected = (next(tool.name for tool in owner.agent.state.tools if tool.name.startswith('ext_dialogue_ask_')), )
        assert selected[0] in {tool.name for tool in owner.agent.state.tools}
        owner.select_active_tools(selected, expected_revision=owner.agent.tool_selection_revision)
        saved = owner.session_manager.load_session(owner.session_id)
        await close(owner)
        restored, restored_calls = await create(tmp_path, saved)
        assert restored.agent.active_tool_selection == ()
        restored._persist()  # Startup save must not overwrite pending extension names with the empty safety mask.
        assert restored.session_manager.load_session(restored.session_id).active_tools == selected
        await restored.initialize_extensions()
        assert restored.agent.active_tool_selection == selected
        branch = restored.create_branch()
        restored.select_active_tools([], expected_revision=restored.agent.tool_selection_revision)
        assert restored.restore_branch(branch)
        assert restored.agent.active_tool_selection == selected
        assert not calls and not restored_calls and restored.permissions.mode == 'deny'
    finally:
        if restored is not None:
            await close(restored)
        await close(owner)


async def test_compaction_recovery_and_return_checkpoint_restore_their_own_selection(tmp_path):
    owner, calls = await create(tmp_path)
    try:
        await owner.initialize_extensions()
        owner.agent.set_stream_fn(lambda *args: EventStream.empty(
            AssistantMessage(content=(TextContent(text='offline summary'),))))
        owner.select_active_tools(['current_time'], expected_revision=owner.agent.tool_selection_revision)
        owner.agent.replace_messages([UserMessage(content='old ' + 'x' * 6000), AssistantMessage(),
                                      UserMessage(content='recent'), AssistantMessage()])
        owner._persist()
        await owner.compact()
        recovery = owner.session_data.compaction['recovery_branch_id']
        assert owner.session_manager.load_session(owner.session_id).active_tools == ('current_time',)
        owner.select_active_tools([], expected_revision=owner.agent.tool_selection_revision)
        assert owner.restore_branch(recovery)
        assert owner.agent.active_tool_selection == ('current_time',)
        return_branch = next(row for row in owner.session_data.branches if row['label'].startswith('Return checkpoint'))
        assert owner.restore_branch(return_branch['branch_id'])
        assert owner.agent.active_tool_selection == ()
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        await close(owner)
