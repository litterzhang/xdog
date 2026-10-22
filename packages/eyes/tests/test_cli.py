import json
import subprocess
import sys
from dataclasses import asdict

import pytest


def test_validate_is_standalone_and_reports_invalid_definitions(tmp_path, observer_data, capsys):
    from xdog.eyes.cli import main

    observer_data['handlers'][0]['delivery'] = 'enqueue'
    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(observer_data))
    assert main(['validate', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['valid'] is True
    observer_data['unknown'] = True
    path.write_text(json.dumps(observer_data))
    assert main(['validate', str(path)]) == 2
    assert 'Unknown fields' in capsys.readouterr().err


@pytest.mark.parametrize('scheduled', [False, True])
def test_validate_imports_no_sdk_or_harness(tmp_path,observer_data,scheduled):
    observer_data['handlers'][0]['delivery'] = 'enqueue'
    if scheduled:
        observer_data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': {
            'cron': '0 9 * * *', 'timezone': 'Asia/Shanghai'}}]
        observer_data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
        observer_data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    path = tmp_path/'eyes.json'
    path.write_text(json.dumps(observer_data))
    script = '''
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='acp' or fullname.startswith(('acp.','xdog.coding','xdog.agent','xdog.ai','xdog.claw','xdog.flow')):
   raise AssertionError('validation must not load a protocol SDK or harness')
sys.meta_path.insert(0,Block())
from xdog.eyes.cli import main
raise SystemExit(main(['validate',sys.argv[1]]))
'''
    result = subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['valid'] is True


def test_definition_data_roundtrips_connection_defaults_and_absolute_roots(tmp_path,observer_data):
    from xdog.eyes.loader import definition_data, parse

    observer_data['handlers'][0]['delivery'] = 'enqueue'
    model = parse(observer_data,base_dir=tmp_path)
    data = definition_data(model)
    assert data['handlers'][0]['connection']['transport']=='stdio'
    assert 'command' not in data['handlers'][0]
    assert data['sources'][0]['root']==str(tmp_path)
    assert asdict(parse(data,base_dir=tmp_path))==asdict(model)


def test_list_is_status_inventory_alias_and_never_starts_an_absent_daemon(tmp_path, capsys):
    from xdog.eyes.cli import main

    for command in ('list', 'status'):
        assert main([command, '--directory', str(tmp_path / 'isolated')]) == 0
        assert json.loads(capsys.readouterr().out) == {'registrations': [], 'state': 'not-running'}
    assert not (tmp_path / 'isolated' / 'owner.json').exists()


def test_list_uses_exact_same_owner_client_inventory_as_status(tmp_path, capsys, monkeypatch):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.cli import main

    calls = []

    async def status(client, identifier=None):
        calls.append((client.owner, identifier, client.directory))
        return {'registrations': [{'registration_id': 'original', 'key': 'tasks', 'state': 'watching'}]}

    monkeypatch.setattr(DaemonControl, 'status', status)
    output = []
    for command in ('list', 'status'):
        assert main([command, '--directory', str(tmp_path / 'isolation'),
                     '--owner', 'goal:original']) == 0
        output.append(json.loads(capsys.readouterr().out))
    assert output[0] == output[1]
    assert calls == [('goal:original', None, tmp_path / 'isolation')] * 2


@pytest.mark.parametrize('command', [['--workspace', '/tmp', 'list'], ['list', '--workspace', '/tmp']])
def test_management_workspace_flag_is_not_an_alias(command):
    from xdog.eyes.cli import main

    with pytest.raises(SystemExit) as exc:
        main(command)
    assert exc.value.code == 2
