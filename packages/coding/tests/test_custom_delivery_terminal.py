"""Current Linux physical Coding/Claw custom delivery, using private terminals/models only."""
from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Current Linux private tmux terminal required')

# The existing offline CLI fixture supplies real startup/termios/HTTP-denial.
# Substitute only its model in an owned temporary bootstrap, never production.
MODEL = '''
import json
_custom_calls = []
def _custom_stream(self, model, context, options):
    _custom_calls.append(str(context.messages))
    Path('model-contexts.json').write_text(json.dumps(_custom_calls))
    if 'CANONICAL-CUSTOM-DELIVERY' in str(context.messages):
        from xdog.agent.core import CustomAgentMessage
        from xdog.coding.core.session_manager import SessionManager
        saved = SessionManager(Path(os.environ['CODING_DIR']) / 'sessions').get_most_recent()
        assert any(isinstance(item, CustomAgentMessage) for item in saved.messages)
    if os.environ['CUSTOM_MODE'] == 'active' and len(_custom_calls) == 1:
        tool = next(item for item in context.tools if item.name.startswith('ext_customdelivery_'))
        response = AssistantMessage(content=(ToolCall(id='original-tool', name=tool.name,
            arguments={'delivery': os.environ['CUSTOM_DELIVERY'], 'trigger': True,
                       'gate': str(Path.cwd() / 'release-custom')}),))
    else:
        response = AssistantMessage(content=(TextContent(text='CUSTOM-PHYSICAL-ANSWER'),))
    return EventStream.empty(response)
FixtureProvider.stream = _custom_stream
'''


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.02)
    raise AssertionError('Private model/storage did not settle')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('mode', ['idle', 'trigger', 'active'])
def test_physical_custom_modes_full_paint_draft_focus_canonical_and_termios(tmp_path, kind, delivery, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/customdelivery',
                    root / 'customdelivery')
    fixture = root / 'customdelivery/main.py'
    source = fixture.read_text()
    source = source.replace('        approved = await api.request_context_message', '''        if options.get('gate'):
            import asyncio
            from pathlib import Path
            gate = Path(options['gate'])
            gate.with_suffix('.entered').write_text('original callback waiting')
            while not gate.exists():
                await asyncio.sleep(.01)
        approved = await api.request_context_message''')
    fixture.write_text(source)
    gate = tmp_path / 'release-custom'
    terminal = Terminal(tmp_path)
    calls, errors, owned = [], [], {}
    ready, stop = threading.Event(), threading.Event()
    worker = None
    home = tmp_path / 'home'
    home.mkdir()
    if kind == 'coding':
        (home / 'settings.json').write_text(json.dumps({'extensions': ['customdelivery'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), CUSTOM_MODE=mode, CUSTOM_DELIVERY=delivery)
        original = Path(__file__).parent / 'fixtures/terminal_client.py'
        bootstrap = tmp_path / 'private_custom_terminal.py'
        bootstrap.write_text(original.read_text().replace('if __name__ == "__main__":',
                                                          MODEL + '\nif __name__ == "__main__":'))
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
            extensions=('customdelivery',), extension_dir=str(root), extension_allow_tools=('customdelivery',))
        def stream(model, context, options):
            calls.append(str(context.messages))
            if 'CANONICAL-CUSTOM-DELIVERY' in calls[-1]:
                runtime = owned['runtime']
                records = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
                assert any(row.get('role') == 'custom' for row in records)
            if mode == 'active' and len(calls) == 1:
                tool = next(item for item in context.tools if item.name.startswith('ext_customdelivery_'))
                response = AssistantMessage(content=(ToolCall(id='original-tool', name=tool.name,
                    arguments={'delivery': delivery, 'trigger': True, 'gate': str(gate)}),))
            else:
                response = AssistantMessage(content=(TextContent(text='CUSTOM-PHYSICAL-ANSWER'),))
            return EventStream.empty(response)
        def serve():
            async def run():
                gateway = GatewayServer(config)
                try:
                    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                               return_value=('offline/model', stream)):
                        await gateway.start()
                    owned['runtime'] = gateway._orchestrator._runtimes['main']
                    owned['runtime'].get_or_create_session(allow_reset=False)
                    ready.set()
                    while not stop.is_set():
                        await asyncio.sleep(.01)
                except BaseException as exc:
                    errors.append(exc)
                    ready.set()
                finally:
                    await gateway.stop()
            asyncio.run(run())
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()

    def model_calls():
        if kind == 'claw':
            return calls
        path = tmp_path / 'model-contexts.json'
        return json.loads(path.read_text()) if path.exists() else []

    def canonical():
        if kind == 'coding':
            saved = SessionManager(home / 'sessions').get_most_recent()
            return saved.messages if saved else []
        runtime = owned['runtime']
        return runtime.transcript_store.load_transcript(runtime.get_new_session_id())

    def is_custom(item):
        return isinstance(item, CustomAgentMessage) if kind == 'coding' else item.get('role') == 'custom'

    try:
        if kind == 'coding':
            command = shlex.join(['env', *(f'{key}={value}' for key, value in terminal.child_env.items()),
                sys.executable, str(bootstrap), 'coding', '--model', 'terminal-fixture', '--permission-mode',
                'allow-all' if mode == 'active' else 'deny'])
            terminal.text("printf 'SHELL-BEFORE\\n'; " + command)
            terminal.keys('Enter')
            terminal.wait('ready |')
        else:
            assert ready.wait(8) and not errors
            terminal.start('claw', config.socket_path)
        terminal.resize(100, 30)
        if mode == 'active':
            terminal.text('ONLY ORIGINAL USER')
        else:
            terminal.text('/ext-customdelivery-request ' + json.dumps(
                {'delivery': delivery, 'trigger': mode == 'trigger', 'gate': str(gate)}))
        terminal.keys('Enter')
        eventually(gate.with_suffix('.entered').exists)
        draft = 'UNCHANGED CUSTOM DRAFT'
        terminal.text(draft)
        terminal.wait_editor(draft)
        gate.touch()
        terminal.wait('Deliver this canonical custom context')
        assert len(model_calls()) == int(mode == 'active')
        terminal.keys('Right', 'Enter')
        terminal.wait('form review')
        terminal.resize(52, 16)
        terminal.resize(80, 24)
        terminal.wait_cursor(False)
        seen = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            visible = terminal.visible()
            seen += visible
            if 'Enter submits reviewed form' in visible:
                break
            terminal.keys('NPage')
            time.sleep(.04)
        else:
            raise AssertionError(terminal.visible())
        assert delivery in seen and 'NEVER-MODEL-METADATA' in seen
        assert len(model_calls()) == int(mode == 'active')
        terminal.keys('Enter')
        if mode != 'idle':
            terminal.wait('CUSTOM-PHYSICAL-ANSWER')
        eventually(lambda: sum(is_custom(item) for item in canonical()) == 1)
        terminal.wait_editor(draft)
        actual = model_calls()
        assert bool(actual) is (mode != 'idle')
        assert 'NEVER-MODEL-METADATA' not in str(actual)
        if mode != 'idle':
            assert 'CANONICAL-CUSTOM-DELIVERY' in actual[-1]
        history = canonical()
        users = sum(isinstance(item, UserMessage) if kind == 'coding' else item.get('role') == 'user'
                    for item in history)
        assert users == int(mode == 'active')
        custom = next(item for item in history if is_custom(item))
        data = custom.data if kind == 'coding' else custom['data']
        assert data['delivery'] == delivery and data['details'] == {'opaque': 'NEVER-MODEL-METADATA'}
        assert draft not in str(history)
        (tmp_path / 'canonical-evidence.txt').write_text(str(history))
        (tmp_path / 'model-evidence.json').write_text(json.dumps(actual))
        terminal.finish()
    finally:
        (tmp_path / 'failure-model-evidence.json').write_text(json.dumps(model_calls()))
        if kind == 'claw' and 'runtime' in owned:
            (tmp_path / 'failure-state.txt').write_text(repr(owned['runtime']._active_session.agent.state))
        gate.touch()
        terminal.call('kill-server')
        if worker is not None:
            stop.set()
            worker.join(8)
            assert not worker.is_alive() and not errors
