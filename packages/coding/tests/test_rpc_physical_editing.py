"""Physical terminal applies returned RPC edit data through actual prompt arbitration."""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Current Linux owned terminal editing evidence')


@pytest.fixture
def edit_terminal(tmp_path):
    terminal = Terminal(tmp_path)
    try:
        yield terminal
    finally:
        terminal.call('kill-server')


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
@pytest.mark.parametrize('observe_release', [False, True], ids=['legacy', 'observe-release'])
def test_actual_rpc_edit_data_paints_unicode_repeat_release_cancel_and_restore(
        edit_terminal, tmp_path, child, observe_release):
    terminal = edit_terminal
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/terminal', root / 'terminal')
    source = root / 'terminal/main.py'
    entered = tmp_path / 'held-hook'
    log = tmp_path / 'actual-hook-inputs.jsonl'
    source.write_text(source.read_text().replace('        calls += 1',
        '        calls += 1\n        import json\n        from pathlib import Path\n'
        f'        with Path({str(log)!r}).open("a") as output:\n'
        '            output.write(json.dumps({"data": data, "kind": metadata["kind"]}) + "\\n")\n'
        "        if data == '\\x1b[97;1:2u':\n            return {'data': data}\n"
        "        if data == '~':\n"
        f"            Path({str(entered)!r}).write_text('entered')\n"
        '            await asyncio.Event().wait()'))
    if observe_release:
        from packages.agent.tests.test_extension_terminal_input import add_release_observer
        add_release_observer(source, log)
    home = tmp_path / 'rpc-home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
    client = Path(__file__).parent / 'fixtures/rpc_terminal_edit_client.py'
    terminal.text(shlex.join(['env', f'XDOG_RPC_RELEASE={int(observe_release)}',
                              sys.executable, str(client), 'child' if child else 'parent']))
    terminal.keys('Enter')
    terminal.wait('RPC EDIT READY')
    terminal.text('ab')
    terminal.wait_editor('[a][b]')
    terminal.text('\x1b[97;1:3u')
    terminal.text('\x1b[97;1:2u')
    terminal.wait_editor('[a][b]a')
    terminal.text('\x1b[200~开发🙂\x1b[201~')
    terminal.wait_editor('[a][b]a[开发🙂]')
    before = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(row['kind'] == 'release' and row['data'] == '\x1b[97;1:3u'
               for row in before) is observe_release
    assert any(row['data'] == '\x1b[97;1:2u' for row in before)
    assert any(row == {'kind': 'paste', 'data': '开发🙂'} for row in before)
    terminal.text('~')
    import time
    deadline = time.monotonic() + 8
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()
    terminal.keys('Escape')
    terminal.wait('hooks paused')
    terminal.keys('C-c')
    terminal.text('safe recovery after RPC cancel')
    terminal.wait_editor('safe recovery after RPC cancel')
    terminal.finish()
    packets = [json.loads(line) for line in (tmp_path / 'rpc-edit-receipts.jsonl').read_text().splitlines()]
    assert any(row['type'] == ('child_control_cancel' if child else 'terminal_cancel') for row in packets)
    if child:
        assert (tmp_path / 'rpc-model.settled').exists()
    else:
        assert not (tmp_path / 'rpc-model').exists()
