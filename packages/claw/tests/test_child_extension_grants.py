"""Real isolated extension executors obey host revocation after batch preparation."""
from __future__ import annotations

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


@pytest.mark.parametrize('revoke', [False, True])
async def test_child_extension_alias_rechecks_live_allowlist_at_actual_effect(tmp_path, revoke):
    root = tmp_path / 'extensions'
    extension = root / 'guarded'
    extension.mkdir(parents=True)
    (extension / 'extension.yaml').write_text('name: guarded\nentry_point: main\n')
    (extension / 'main.py').write_text('''
from pathlib import Path

def setup(api):
    async def effect(params, context):
        Path(api.settings['marker']).write_text('ACTUAL CHILD EXTENSION EFFECT')
        return 'EXTENSION EXECUTED'
    api.tool('effect', 'A marker effect', {'type': 'object'}, effect)
''')
    marker = tmp_path / 'effect'
    calls = []
    alias = ''
    def stream(model, context, options):
        calls.append(context)
        content = ((ToolCall(id='first', name='revoke', arguments={}),
                    ToolCall(id='second', name=alias, arguments={})) if len(calls) == 1
                   else (TextContent(text='Observed actual tool results'),))
        return EventStream.empty(AssistantMessage(content=content))
    config = ClawConfig(data_dir=str(tmp_path / 'data'), extensions=('guarded',),
                        extension_allow_tools=('guarded',), extension_dir=str(root),
                        extension_settings={'guarded': {'marker': str(marker)}})
    app = Orchestrator(config, model='test/parent', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    try:
        session = await app._runtimes['main'].prepare_session(allow_reset=False)
        await session.initialize_extensions()
        originals = session.extensions.tools()
        assert len(originals) == 1
        alias = originals[0].name
        async def first_effect(*args, **kwargs):
            if revoke:
                session._runtime.extension_allow_tools = ()
            return AgentToolResult.from_text('FIRST EFFECT COMPLETED')
        first = AgentTool('revoke', parameters={'type': 'object'}, execute=first_effect)
        session.agent.set_tools((first, *originals, *session.children.supervisor.tools()))
        identifier = await session.children.supervisor.start('Use delegated tools', access='inherit',
            config={'tools': ['revoke', alias]})
        record = await session.children.supervisor.control('wait', identifier)
        results = [row for row in record['messages'] if row['role'] == 'toolResult']
        assert len(results) == 2, record
        assert 'FIRST EFFECT COMPLETED' in str(results[0])
        assert marker.exists() is (not revoke)
        if revoke:
            assert results[1]['is_error'] and 'disabled' in str(results[1]).lower()
        else:
            assert not results[1]['is_error'] and marker.read_text() == 'ACTUAL CHILD EXTENSION EFFECT'
    finally:
        await app.stop()
