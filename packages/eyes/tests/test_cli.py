import json
import subprocess
import sys
from dataclasses import asdict


def test_validate_is_standalone_and_reports_invalid_definitions(tmp_path, observer_data, capsys):
    from xdog.eyes.cli import main

    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(observer_data))
    assert main(['validate', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['valid'] is True
    observer_data['unknown'] = True
    path.write_text(json.dumps(observer_data))
    assert main(['validate', str(path)]) == 2
    assert 'Unknown fields' in capsys.readouterr().err


def test_validate_imports_no_sdk_or_harness(tmp_path,observer_data):
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

    model = parse(observer_data,base_dir=tmp_path)
    data = definition_data(model)
    assert data['handlers'][0]['connection']['transport']=='stdio'
    assert 'command' not in data['handlers'][0]
    assert data['sources'][0]['root']==str(tmp_path)
    assert asdict(parse(data,base_dir=tmp_path))==asdict(model)
