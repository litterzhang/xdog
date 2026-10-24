"""Two independent host processes reopen real stores; ceilings are data, not grants."""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

_SCRIPT = textwrap.dedent('''
    import asyncio
    import json
    import sys
    from dataclasses import replace
    from pathlib import Path

    from xdog.agent import Agent, AgentConfig
    from xdog.agent.skills import SkillRepo
    from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    root, host, phase, scenario = Path(sys.argv[1]), *sys.argv[2:]
    allowed = root / 'allowed'
    inside, outside = allowed / 'inside', root / 'outside'
    calls, executed = [], []
    old_effect = allowed / 'old-effect'

    class Adapter:
        def resolve_model(self, name):
            return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True)

        def __call__(self, model, context, options):
            calls.append(context)
            if phase == 'save' and context.messages[-1].role == 'user':
                message = AssistantMessage(content=(ToolCall(id='old-tool', name='filesystem', arguments={
                    'action': 'write', 'path': str(old_effect), 'content': 'original effect'}),))
            elif phase == 'reopen' and context.messages[-1].role == 'user':
                message = AssistantMessage(content=tuple(
                    ToolCall(id=f'{name}-{len(calls)}', name='filesystem', arguments={
                        'action': 'write', 'path': str(path), 'content': name})
                    for name, path in (('inside', inside), ('outside', outside))))
            else:
                message = AssistantMessage(content=(TextContent(text='done'),))
            return EventStream.empty(message)

    async def main():
        from xdog.coding.core.agent_session import AgentSession
        from xdog.coding.core.permissions import PermissionManager
        from xdog.coding.core.session_manager import SessionManager
        from xdog.coding.core.settings_manager import SettingsManager
        from xdog.coding.core.tools import get_default_tools
        from packages.claw.tests.test_child_runtime import application, prepared

        manifest = root / 'manifest.json'
        saved = json.loads(manifest.read_text()) if phase == 'reopen' else None
        if host == 'coding':
            manager = SessionManager(root / 'sessions')
            data = (manager.load_session(saved['session']) if saved else
                    manager.create_session(model='test/model', working_dir=root))
            session = AgentSession(agent=Agent(Adapter(), config=AgentConfig(model='test/model'),
                tools=get_default_tools(root), tool_ctx={'fs_workspace': str(root)},
                skills=SkillRepo().register(directory=root / 'skills', name="shared")),
                session_data=data, session_manager=manager,
                settings=SettingsManager(global_path=root / 'settings.json'),
                tool_repo=None, bash=None, working_dir=root, permissions=PermissionManager('allow-all'))
            sid = session.session_id
            async def close():
                await session.aclose()
        else:
            app = application(root, Adapter(), tools=('filesystem',))
            session = await prepared(app)
            sid = session.meta.session_id
            async def close():
                await app.stop()
        try:
            roots = ([root] if phase == 'reopen' and scenario == 'broadened-parent' else
                     None if scenario == 'same-unconfined' else [] if scenario == 'same-empty' else [allowed])
            session.agent.set_tool_context({**session.agent.tool_context, 'fs_confine_to': roots,
                                           '_private_parent_marker': object()})
            owner = session.children
            filesystem = next(tool for tool in session.agent.local_tools if tool.name == 'filesystem')
            async def observe(call_id, args, *extra, **kwargs):
                executed.append(call_id)
                return await filesystem.execute(call_id, args, *extra, **kwargs)
            session.agent.set_tools(tuple(replace(tool, execute=observe) if tool is filesystem else tool
                                          for tool in session.agent.local_tools))
            if phase == 'save':
                identifier = await owner.runtime.start('retain filesystem ceiling', access='inherit',
                    config={'tools': ['filesystem'], 'skills': []})
                result = await owner.runtime.control('wait', identifier)
                assert result['status'] == 'completed', result
                assert executed == ['old-tool']  # Actual protected executor and real filesystem tool.
                old_effect.write_text('host marker after the saved effect')
                row, = owner.store.load()
                expected_roots = (None if scenario == 'same-unconfined' else
                                      [] if scenario == 'same-empty' else [str(allowed)])
                assert row['filesystem_constraints'] == {'version': 1, 'roots': expected_roots}
                if scenario == 'unknown-old':
                    row.pop('filesystem_constraints')
                    owner.store.save(row)
                elif scenario == 'invalid-version':
                    row['filesystem_constraints']['version'] = 999
                    owner.store.save(row)
                manifest.write_text(json.dumps({'session': sid, 'child': identifier}))
            else:
                assert sid == saved['session'], (sid, saved)
                assert not owner.filesystem_limits  # No original process authority survived.
                if scenario == 'invalid-version':
                    assert owner.runtime.diagnostic and not calls
                    try:
                        await owner.runtime.start('cannot bypass malformed catalog')
                    except ValueError as exc:
                        assert 'filesystem constraints' in str(exc), str(exc)
                    else:
                        raise AssertionError('Malformed historical constraints accepted')
                    assert not executed and old_effect.read_text() == 'host marker after the saved effect'
                    return
                row, = owner.store.load()  # A new store/host read, not cached state.
                assert row['id'] == saved['child']
                await owner.runtime.control('follow-up', saved['child'], 'exercise original filesystem ceiling')
                result = await owner.runtime.control('wait', saved['child'])
                if scenario == 'broadened-parent':
                    assert result['status'] == 'failed' and not calls, result
                    assert 'Original child filesystem execution constraints changed' in result['error']
                else:
                    assert result['status'] == 'completed', result
                    assert outside.exists() == (scenario == 'same-unconfined')
                    assert inside.exists() == (scenario in {'same-parent', 'same-unconfined'})
                    if scenario == 'unknown-old':
                        assert 'outside this run' in str(result['messages'])
                        row, = owner.store.load()
                        assert row['filesystem_constraints'] == {'version': 1, 'roots': []}
                        # The explicit deny-all history remains usable as data on later follow-ups.
                        await owner.runtime.control('follow-up', saved['child'], 'still deny all')
                        again = await owner.runtime.control('wait', saved['child'])
                        assert again['status'] == 'completed', again
                        assert not inside.exists() and not outside.exists()
                assert 'old-tool' not in executed
                assert old_effect.read_text() == 'host marker after the saved effect'
        finally:
            await close()
        print('REAL-STORE-' + phase.upper() + '-VERIFIED')

    asyncio.run(main())
''')


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('scenario', [
    'same-parent', 'same-unconfined', 'same-empty', 'broadened-parent', 'unknown-old', 'invalid-version',
])
async def test_versioned_child_filesystem_ceiling_survives_real_process_and_store_reopen(tmp_path, host, scenario):
    (tmp_path/'allowed').mkdir()
    for phase in ('save', 'reopen'):
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', _SCRIPT,
            str(tmp_path), host, phase, scenario, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(30):
                output, error = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, (output.decode(), error.decode())
        if phase == 'save' or scenario != 'invalid-version':
            assert f'REAL-STORE-{phase.upper()}-VERIFIED' in output.decode()
    assert (tmp_path/'outside').exists() == (scenario == 'same-unconfined')
    assert json.loads((tmp_path/'manifest.json').read_text())['child']


@pytest.mark.parametrize('value', [
    None, {}, {'version': True, 'roots': None}, {'version': 2, 'roots': []},
    {'version': 1, 'roots': ['relative']}, {'version': 1, 'roots': [False]},
    {'version': 1, 'roots': [], 'grant': True},
])
def test_historical_filesystem_codec_never_treats_malformed_data_as_unconfined(value):
    from xdog.agent.tools.filesystem_constraints import decode_filesystem_roots

    with pytest.raises(ValueError):
        decode_filesystem_roots(value)


def test_filesystem_ceiling_capture_and_codec_are_lazy_data_only(tmp_path, monkeypatch):
    from pathlib import Path

    from xdog.agent.tools.filesystem_constraints import (
        capture_filesystem_roots,
        decode_filesystem_roots,
        encode_filesystem_roots,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError('Capturing historical constraints must not perform filesystem IO')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'resolve', forbidden)
        patch.setattr(Path, 'stat', forbidden)
        roots = [str(tmp_path)]
        captured = capture_filesystem_roots({'fs_confine_to': roots, '_private': object()})
        roots.clear()
        assert captured == (tmp_path,)
        assert decode_filesystem_roots(encode_filesystem_roots(captured)) == captured
        assert capture_filesystem_roots({}) is None
        assert capture_filesystem_roots({'fs_confine_to': None}) is None
        assert capture_filesystem_roots({'fs_confine_to': []}) == ()
