"""Extensions retain hooks/UI but cannot register or delegate model tools."""
from __future__ import annotations

import pytest
from xdog.agent.extensions import ExtensionAPI
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


async def test_parent_and_child_extensions_have_no_tool_registration_surface(tmp_path):
    root = tmp_path / 'extensions'
    extension = root / 'guarded'
    extension.mkdir(parents=True)
    (extension / 'extension.yaml').write_text('name: guarded\nentry_point: main\n')
    (extension / 'main.py').write_text('''
def setup(api):
    assert not hasattr(api, 'tool')
    async def hello(args, context):
        return 'child' if context.get('child_id') else 'parent'
    api.command('hello', 'Still a command, never a tool', hello)
    api.status('ready', 'Hooks and UI remain available')
''')
    assert not hasattr(ExtensionAPI({}), 'tool')
    config = ClawConfig(data_dir=str(tmp_path / 'data'), extensions=('guarded',), extension_dir=str(root))
    app = Orchestrator(config, model='test/parent',
                       stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    try:
        session = await app._runtimes['main'].prepare_session(allow_reset=False)
        await session.initialize_extensions()
        before = tuple(session.agent.state.tools)
        for removed in ('tools', 'sync_tools', 'aliases', 'tool_owner'):
            assert not hasattr(session.extensions, removed)
        assert session.extensions.commands()
        with pytest.raises(ValueError, match='delegable'):
            await session.children.runtime.start('cannot invent an extension grant',
                config={'tools': ['ext_guarded_effect']})
        assert not session.children.runtime.snapshot()['children']
        identifier = await session.children.runtime.start('ordinary child with command hooks', config={'tools': []})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record
        assert tuple(session.agent.state.tools) == before
        assert tuple(session._runtime.tool_repo.tools) == before
    finally:
        await app.stop()
