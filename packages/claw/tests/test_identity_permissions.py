"""Identity-configured tools do not grant history, roles or host capabilities."""
from dataclasses import replace

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef, ToolConfigError, load_config, save_config
from xdog.claw.core.identity import ExecutionPolicy, IdentityResolver, MessageSource
from xdog.claw.core.identity.permissions import IdentityPermissions
from xdog.claw.core.runtime.gateway import _groups_from_config
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


def permissions(**tools):
    value = IdentityPermissions.from_config().config()
    for role, names in tools.items():
        value['permission_profiles'][value['defaults'][role]]['tools'] = names
    return IdentityPermissions.from_config(value['permission_profiles'], value['principals'], value['defaults'])


def test_configuration_roundtrip_and_group_runtime_policy(tmp_path):
    cfg = permissions(admin=['current_time'], system=['filesystem'], external_user=['xhs_post']).config()
    cfg['principals'] = [{'id': 'owner', 'permissions': 'reader'}]
    group = GroupDef(id='main', **cfg)
    config = ClawConfig(groups=(group,))
    path = tmp_path / 'config.yaml'
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.groups == config.groups
    runtime = _groups_from_config(loaded)[0]
    owner = IdentityResolver.local(MessageSource('tui'))
    assert runtime.permissions.tools_for(owner) == frozenset()
    system = IdentityResolver.system(MessageSource('acp'))
    assert runtime.permissions.tools_for(system) == frozenset({'filesystem'})
    external = IdentityResolver().channel('main', MessageSource('xhs', 'ours', 'A'))
    policy = ExecutionPolicy.for_identity(external, permissions=runtime.permissions)
    assert policy.allows_tool('xhs_post') and not policy.manage


@pytest.mark.parametrize('cfg', [
    {'permission_profiles': {}},
    {'permission_profiles': {'owner': {'tools': 'bash'}}},
    {'permission_profiles': {'owner': {'tools': ['*', 'bash']}}},
    {'permission_profiles': {'owner': {'tools': ['bash', 'bash']}}},
    {'permission_profiles': {'owner': {'tools': [' bash']}}},
    {'principals': [{'id': 'owner', 'permissions': 'missing'}]},
    {'principals': [{'id': 'owner', 'permissions': 'owner', 'role': 'admin'}]},
    {'principals': [{'id': 'owner', 'permissions': 'owner'}, {'id': 'owner', 'permissions': 'reader'}]},
    {'defaults': {'admin': 'owner'}},
    {'defaults': {'admin': 'missing', 'system': 'automation', 'user': 'reader', 'external_user': 'public'}},
])
def test_invalid_permissions_fail_closed(cfg):
    with pytest.raises(ToolConfigError):
        GroupDef(id='main', **cfg)


def test_removed_user_tools_does_not_silently_load_default_permissions(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('groups:\n  main:\n    user_tools: [bash]\n')
    with pytest.raises(ToolConfigError, match='user_tools is removed'):
        load_config(path)


@pytest.mark.parametrize('role', ['admin', 'external_user', 'system'])
async def test_real_model_inventory_and_executor_share_identity_ceiling(tmp_path, role):
    from xdog.agent.session_input import SessionInput
    from xdog.claw.core.types import ExternalInput
    seen, executed = [], []
    def model(name, context, options):
        seen.append(context)
        if len(seen) == 1:
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='denied', name='private', arguments={}),
                ToolCall(id='allowed', name='business', arguments={}),), stop_reason='toolUse'))
        assert any(isinstance(row, ToolResultMessage) and 'business result' in str(row.content)
                   for row in context.messages)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='answer'),)))
    async def execute(call_id, args, cancel=None, on_update=None, **kwargs):
        executed.append(call_id)
        return AgentToolResult.from_text('business result')
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=(),
                             permissions=permissions(**{role: ['business']})))
    runtime = app._runtimes['main']
    session = await runtime.prepare_session()
    runtime._tools = [AgentTool(name, 'PRIVATE-TOOL-DESCRIPTION' if name == 'private' else 'Business operation',
                               {'type': 'object'}, execute=execute) for name in ('business', 'private')]
    runtime.publish_tools(session)
    try:
        if role == 'system':
            value = ExternalInput(group_id='main', canonical=SessionInput('s1', 'acp', 'goal', 'work', (), {}))
            result = await app.route_message(value)
        elif role == 'external_user':
            identity = IdentityResolver().channel('main', MessageSource('xhs', 'ours', 'A'))
            result = await app.route_message(UserInput(group_id='main', content='work'), host_identity=identity,
                                            identity_binding='channel')
        else:
            result = await app.route_message(UserInput(group_id='main', content='work'))
        assert not result.error and executed == ['allowed']
        assert all({tool.name for tool in context.tools} == {'business'} for context in seen)
        from xdog.ai.types import system_prompt_text
        assert all('PRIVATE-TOOL-DESCRIPTION' not in (system_prompt_text(context.system_prompt) or '')
                   for context in seen)
        # Tool grant does not confer human approvals.
        assert role == 'admin' or not session._user_operation_authorized
    finally:
        await app.stop()


async def test_permission_narrowing_after_model_request_blocks_execution_and_cannot_expand_turn(tmp_path):
    executed, contexts = [], []
    app = None
    def model(name, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            runtime = app._runtimes['main']
            runtime.group = replace(runtime.group, permissions=permissions(admin=['new']))
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='revoked', name='original', arguments={}),
                ToolCall(id='expanded', name='new', arguments={}),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    async def execute(*args, **kwargs):
        executed.append(True)
        return AgentToolResult.from_text('must not execute')
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=(),
                             permissions=permissions(admin=['original'])))
    runtime = app._runtimes['main']
    session = await runtime.prepare_session()
    runtime._tools = [AgentTool(name, '', {'type': 'object'}, execute=execute) for name in ('original', 'new')]
    runtime.publish_tools(session)
    try:
        result = await app.route_message(UserInput(group_id='main', content='run'))
        assert not result.error and executed == []
        assert {tool.name for tool in contexts[0].tools} == {'original'}
        assert not contexts[-1].tools
    finally:
        await app.stop()


async def test_children_capture_original_identity_ceiling_not_later_speakers_permissions(tmp_path):
    from xdog.agent.children import ChildSpec
    from xdog.claw.core.runtime.identity_scope import resolve

    from packages.claw.tests.eyes_helpers import ScriptedModel
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=ScriptedModel())
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'),
                             permissions=permissions(admin=['current_time'])))
    runtime = app._runtimes['main']
    session = await runtime.prepare_session()
    session._input_execution = resolve(UserInput(group_id='main', content='delegate'), 'owner-input',
                                       permissions=runtime.group.permissions)
    try:
        with pytest.raises(ValueError, match='exceeds parent'):
            await session.children.capture(ChildSpec('denied', 'do task', tools=('filesystem',)))
        captured = await session.children.capture(ChildSpec('allowed', 'do task', tools=('current_time',)))
        guard = session.children.snapshots[captured.id].identity_allows
        assert guard('current_time') and not guard('filesystem')
        external = IdentityResolver().channel('main', MessageSource('xhs', 'ours', 'A'))
        session._input_execution = resolve(UserInput(group_id='main', content='public', identity=external),
                                           'external-input', permissions=runtime.group.permissions)
        assert guard('current_time')  # Another speaker does not revoke the owner's accepted work.
        runtime.group = replace(runtime.group, permissions=permissions(admin=[]))
        assert not guard('current_time')  # Revoking the actual original identity does.
    finally:
        session._input_execution = None
        await app.stop()


async def test_parallel_prepared_tool_is_rechecked_after_later_validation_revokes_grant(tmp_path):
    seen, executed = [], []
    def model(name, context, options):
        seen.append(context)
        if len(seen) == 1:
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='first', name='original', arguments={}),
                ToolCall(id='second', name='trigger', arguments={}),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=(),
                             permissions=permissions(admin=['original', 'trigger'])))
    runtime = app._runtimes['main']
    session = await runtime.prepare_session()
    async def execute(*args, **kwargs):
        executed.append(True)
        return AgentToolResult.from_text('must not execute')
    async def revoke(args):
        runtime.group = replace(runtime.group, permissions=permissions(admin=[]))
    runtime._tools = [AgentTool('original', '', {'type': 'object'}, execute=execute),
                      AgentTool('trigger', '', {'type': 'object'}, execute=execute, validate_arguments=revoke)]
    runtime.publish_tools(session)
    try:
        result = await app.route_message(UserInput(group_id='main', content='run'))
        assert not result.error and executed == []
        results = [row for row in session.agent.messages if isinstance(row, ToolResultMessage)]
        assert len(results) == 2 and all(row.is_error for row in results)
        assert not seen[-1].tools
    finally:
        await app.stop()
