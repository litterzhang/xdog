"""Optional CLI enqueue never becomes an Eyes callback or an agent startup path."""

import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner


@pytest.fixture
def offline(tmp_path,monkeypatch):
    from xdog.coding.core.session_manager import SessionManager

    monkeypatch.setenv('CODING_DIR',str(tmp_path/'coding'))
    monkeypatch.chdir(tmp_path)
    manager = SessionManager(tmp_path/'coding'/'sessions')
    data = manager.create_session(working_dir=tmp_path)
    return manager,data


def test_offline_enqueue_receipt_is_durable_idempotent_and_not_execution(offline,tmp_path):
    from xdog.coding.cli.args import cli

    manager,data = offline
    argv = ['--session',data.session_id,'--enqueue','--message-id','stable','--prompt','Review tests']
    first = CliRunner().invoke(cli,argv)
    assert first.exit_code==0,first.output
    body = json.loads(first.output)
    assert body['status']=='queued' and body['message_state']=='pending'
    assert body['message_id']=='stable'
    second = CliRunner().invoke(cli,argv)
    assert second.exit_code==0,second.output
    assert json.loads(second.output)['status']=='duplicate'
    changed = CliRunner().invoke(cli,argv[:-1]+['Other instructions'])
    assert changed.exit_code!=0
    assert len(list((tmp_path/'coding'/'inbox').rglob('*.json')))==1
    assert manager.load_session(data.session_id).messages==[]


@pytest.mark.parametrize('extra',[
    ['--permission-mode','allow-all'],['--model','test'],['--resume'],['--resume-id','other'],
    ['--print'],['--rpc'],['--acp'],['--pick-session'],['--list-models'],
])
def test_enqueue_cannot_smuggle_runtime_configuration_or_other_modes(offline,extra):
    from xdog.coding.cli.args import cli

    manager,data = offline
    result = CliRunner().invoke(cli,[
        '--session',data.session_id,'--enqueue','--message-id','m1','--prompt','Review',*extra])
    assert result.exit_code==2


@pytest.mark.parametrize('argv',[
    ['--enqueue','--prompt','Review','--message-id','m'],
    ['--session','s','--enqueue','--prompt','Review'],
    ['--session','s','--enqueue','--message-id','m'],
    ['--message-id','m','--prompt','Review'],
])
def test_enqueue_requires_explicit_identity_id_and_instruction(argv):
    from xdog.coding.cli.args import cli

    assert CliRunner().invoke(cli,argv).exit_code==2


def test_cli_enqueue_imports_neither_agent_session_nor_acp(offline,tmp_path):
    manager,data = offline
    script = '''
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in {'xdog.coding.core.agent_session','xdog.coding.core.sdk','acp'} or fullname.startswith('acp.'):
   raise AssertionError('enqueue must not require ACP or AgentSession')
sys.meta_path.insert(0,Block())
from xdog.coding.main import main
main()
'''
    result = subprocess.run([sys.executable,'-c',script,'--session',data.session_id,'--enqueue',
                             '--message-id','offline','--prompt','Review tests'],
                            cwd=tmp_path,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='queued'


async def test_standard_stdio_adapter_can_enqueue_without_load_or_model(offline,tmp_path):
    from acp import PROTOCOL_VERSION, RequestError, spawn_agent_process

    from packages.coding.tests.test_acp_ingress import Client

    manager,data = offline
    script = '''
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in {'xdog.coding.core.agent_session','xdog.coding.core.sdk'}:
   raise AssertionError('ACP intake must not import model construction')
sys.meta_path.insert(0,Block())
from xdog.coding.main import main
main()
'''
    peer = spawn_agent_process(Client(),sys.executable,'-c',script,'--acp',cwd=tmp_path,env=dict(os.environ))
    async with peer as (conn,proc):
        hello = await conn.initialize(protocol_version=PROTOCOL_VERSION)
        assert hello.field_meta['xdog']['extensions']['enqueue']==1
        params = {'session_id':data.session_id,'cwd':str(tmp_path),'message_id':'stdio-offline',
                  'instruction':'Review tests','evidence':[],'source':'external client','provenance':{}}
        receipt = await conn.ext_method('xdog/enqueue',params)
        assert receipt['status']=='queued'
        status = await conn.ext_method('xdog/inbox_status',{'session_id':data.session_id,'cwd':str(tmp_path)})
        assert status['messages'][0]['status']=='pending'
        with pytest.raises(RequestError):
            await conn.ext_method('xdog/approve',{'session_id':data.session_id,'message_ids':['stdio-offline']})
    assert not manager.load_session(data.session_id).messages
