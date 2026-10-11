"""Owned Linux native terminals observe releases without turning them into edits."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, StartEvent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Current Linux owned native release evidence')


@pytest.fixture
def terminal(tmp_path):
    owned = Terminal(tmp_path)
    try:
        yield owned
    finally:
        owned.call('kill-server')


def wait_file(path):
    deadline = time.monotonic() + 8
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
def test_native_release_observation_cancel_and_termios_restore(terminal, tmp_path, kind, child):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/terminal', root / 'terminal')
    source = root / 'terminal/main.py'
    log, held = tmp_path / 'release.jsonl', tmp_path / 'held-release'
    source.write_text(source.read_text() + f'''
    async def observe_release(data, metadata, context):
        import json
        from pathlib import Path
        with Path({str(log)!r}).open('a') as output:
            output.write(json.dumps({{'data': data, **metadata, 'child': context.get('child_id', '')}}) + '\\n')
        if data == '\\x1b[126;1:3u':
            Path({str(held)!r}).touch()
            await asyncio.Event().wait()
    api.on_terminal_input(observe_release, events=('release',))
''')
    calls, errors, owned, ready = [], [], {}, threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'release-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'release.sock'), pid_file=str(tmp_path / 'release.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('terminal',), extension_dir=str(root))
        def stream(model, context, options):
            calls.append(context)
            async def generate():
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            return EventStream(generate())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
                    loop.run_until_complete(server.start())
                ready.set()
                loop.run_forever()
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                loop.run_until_complete(server.stop())
                loop.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait_json_field('terminal_input', True)
        terminal.wait_hidden('Esc cancel command')
        if child:
            terminal.keys('M-i')
            terminal.text('/agents start child-hold')
            terminal.keys('Enter')
            terminal.wait('started')
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if calls if kind == 'claw' else (tmp_path / 'fixture-model-count').exists():
                    break
                time.sleep(0.01)
            terminal.text('/agents')
            terminal.keys('Enter')
            terminal.wait('Child agents')
            terminal.wait('child-hold')
            terminal.text('s')
            terminal.wait('Child steer')
            terminal.keys('M-i')
        terminal.text('ab')
        terminal.wait_editor('[a][b]')
        terminal.text('\x1b[97;1:3u')
        wait_file(log)
        terminal.wait_editor('[a][b]')
        first = json.loads(log.read_text().splitlines()[0])
        assert first == {'data': '\x1b[97;1:3u', 'kind': 'release',
                         'surface': 'child-prompt' if child else 'prompt', 'child': first['child']}
        assert bool(first['child']) is child
        terminal.text('\x1b[126;1:3u')
        wait_file(held)
        terminal.keys('Escape')
        terminal.wait('Terminal input interrupted')
        if child:
            terminal.wait('Child agents')
            terminal.keys('Escape')
            terminal.wait_hidden('Child agents')
            terminal.wait_hidden('Children focused')
            terminal.wait_hidden('Child browser')
        terminal.keys('C-c')
        terminal.text('native safe recovery')
        terminal.wait_editor('native safe recovery')
        if kind == 'coding':
            marker = tmp_path / 'fixture-model-count'
            assert (marker.read_text() if marker.exists() else '0') == str(int(child))
        else:
            assert len(calls) == int(child)
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors
