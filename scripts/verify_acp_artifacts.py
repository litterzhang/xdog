"""Build-independent smoke checks in fresh wheel-only environments outside the workspace.

Usage: uv run python scripts/verify_acp_artifacts.py /absolute/distribution/directory
Creates fresh /tmp directories, never edits an existing environment or runs a provider.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EYES = r'''
import importlib.metadata as md,importlib.resources as resources,json,sys
from pathlib import Path
from xdog.eyes import loader
installed={d.metadata['Name'].lower().replace('_','-') for d in md.distributions()}
assert not installed.intersection({'xdog-ai','xdog-agent','xdog-coding','xdog-claw','xdog-flow','xdog-tui'})
assert Path(loader.__file__).is_relative_to(Path(sys.prefix))
example=resources.files('xdog.eyes')/'examples'/'source-test-review.json'
value=loader.load(Path(str(example)))
assert value.version==2 and value.handlers[0].delivery=='enqueue'
assert (resources.files('xdog.eyes')/'skills'/'eyes-observers'/'SKILL.md').is_file()
from xdog.eyes.cli import main
assert main(['validate',str(example)])==0
assert not any(n.startswith(('xdog.ai','xdog.agent','xdog.coding')) for n in sys.modules)
print(json.dumps({'eyes':'wheel validation/skill/example, no harness dependencies',
                  'sdk':md.version('agent-client-protocol')}))
'''

AGENT_PEER = r'''
import asyncio
from xdog.agent import Agent,AgentConfig
from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.backend import AgentBackend
from xdog.agent.acp.transport import serve
from xdog.ai.types import AssistantMessage,TextContent
from xdog.ai.utils.event_stream import EventStream
def factory(cwd):
 return Agent(lambda *_:EventStream.empty(AssistantMessage(content=(TextContent(text='wheel reviewed'),))),
              config=AgentConfig(model='test'))
asyncio.run(serve(AcpAdapter(AgentBackend(factory))))
'''

AGENT = r'''
import asyncio,importlib.metadata as md,json,sys
from pathlib import Path
from acp import PROTOCOL_VERSION,spawn_agent_process
from acp.schema import TextContentBlock
from xdog.agent.acp import adapter
assert Path(adapter.__file__).is_relative_to(Path(sys.prefix))
for distribution in md.distributions():
 assert distribution.metadata['Name'].lower() not in {'xdog-coding','xdog-eyes','xdog-claw','xdog-flow'}
class Client:
 def __init__(self):self.text=[]
 async def session_update(self,session_id,update,**kwargs):
  if hasattr(update,'content'):self.text.append(update.content.text)
async def main():
 client=Client()
 async with spawn_agent_process(client,sys.executable,sys.argv[1]) as (conn,process):
  await conn.initialize(protocol_version=PROTOCOL_VERSION)
  ref=await conn.new_session(cwd=str(Path.cwd()),mcp_servers=[])
  await conn.load_session(session_id=ref.session_id,cwd=str(Path.cwd()),mcp_servers=[])
  response=await conn.prompt(session_id=ref.session_id,prompt=[TextContentBlock(type='text',text='Review')])
  assert response.stop_reason=='end_turn' and ''.join(client.text)=='wheel reviewed'
 assert process.returncode is not None
 print(json.dumps({'agent':'wheel-only official SDK stdio initialize/new/load/prompt/update, no coding'}))
asyncio.run(main())
'''

CODING = r'''
import asyncio,importlib.metadata as md,json,sys
from pathlib import Path
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in {'xdog.coding.core.agent_session','xdog.coding.core.sdk'}:
   raise AssertionError('wheel intake cannot construct or import AgentSession/model bootstrap')
sys.meta_path.insert(0,Block())
from acp import PROTOCOL_VERSION,connect_to_agent
from xdog.coding.acp.ingress import IngressConfig,IngressServer
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.sessions.inbox_store import InboxStore
import xdog.coding.acp.ingress as module
assert Path(module.__file__).is_relative_to(Path(sys.prefix))
assert not any(d.metadata['Name'].lower() in {'xdog-eyes','xdog-claw','xdog-flow'} for d in md.distributions())
class Client:
 async def session_update(self,**kwargs):raise AssertionError('intake cannot execute or emit model output')
async def main():
 root=Path.cwd();manager=SessionManager(root/'sessions');data=manager.create_session(working_dir=root)
 address=root/'receiver'/'acp.sock';address.parent.mkdir(mode=0o700)
 server=await IngressServer.start(IngressConfig(manager._dir,root/'inbox',address))
 reader,writer=await asyncio.open_unix_connection(address)
 conn=connect_to_agent(Client(),writer,reader)
 try:
  hello=await conn.initialize(protocol_version=PROTOCOL_VERSION)
  assert hello.field_meta['xdog']['extensions']['enqueue']==1
  request={'session_id':data.session_id,'cwd':str(root),'message_id':'wheel-message','instruction':'Review',
           'source':'wheel consumer','evidence':[{'path':'src/a.py'}],'provenance':{}}
  assert (await conn.ext_method('xdog/enqueue',request))['status']=='queued'
  assert (await conn.ext_method('xdog/enqueue',request))['status']=='duplicate'
  status=await conn.ext_method('xdog/inbox_status',{'session_id':data.session_id,'cwd':str(root)})
  assert status['messages'][0]['status']=='pending_review' and status['inbox_id']
  assert not manager.load_session(data.session_id).messages
  assert not any(n in sys.modules for n in ('xdog.coding.core.agent_session','xdog.coding.core.sdk'))
 finally:
  await conn.close();writer.close();await writer.wait_closed();await server.aclose()
 assert len(InboxStore(manager._dir,root/'inbox').records(data.session_id,root))==1
 print(json.dumps({'coding':'wheel-only Unix ACP enqueue/status/dedup with model imports forbidden',
                   'session_id':data.session_id}))
asyncio.run(main())
'''


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix='xdog-acp-isolated.'))
    environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    environment['UV_NO_PROJECT'] = '1'
    uv = shutil.which('uv')
    if uv is None:
        raise RuntimeError('uv is required for artifact verification')
    result = {'distributions': str(dist), 'root': str(root), 'environments': {}}
    for name, packages, script in (
        ('eyes', ('eyes',), EYES),
        ('agent', ('ai', 'agent'), AGENT),
        ('coding', ('ai', 'agent', 'tui', 'coding'), CODING),
    ):
        env = root / (name + '-venv')
        work = root / (name + '-work')
        work.mkdir(mode=0o700)
        subprocess.run([uv, 'venv', str(env), '--python', sys.executable], env=environment, cwd=root, check=True)
        python = env / 'bin' / 'python'
        wheels = [str(next(dist.glob('xdog_' + package + '-*.whl'))) for package in packages]
        subprocess.run([uv, 'pip', 'install', '--python', str(python), 'agent-client-protocol==0.12.1', *wheels],
                       env=environment, cwd=root, check=True)
        file = work / 'consumer.py'
        file.write_text(script)
        args = [str(python), str(file)]
        if name == 'agent':
            peer = work / 'peer.py'
            peer.write_text(AGENT_PEER)
            args.append(str(peer))
        subprocess.run(args, env=environment, cwd=work, check=True, timeout=30)
        if name == 'eyes':
            subprocess.run([str(env / 'bin' / 'xdog-eyes'), '--help'],
                           env=environment, cwd=work, check=True, stdout=subprocess.DEVNULL)
        result['environments'][name] = str(env)
    (root / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
