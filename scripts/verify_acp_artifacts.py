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
scheduled=resources.files('xdog.eyes')/'examples'/'scheduled-review.json'
timed=loader.load(Path(str(scheduled)))
from xdog.eyes.sources.scheduler import SchedulerSource
timer=SchedulerSource(timed.sources[0],wall_now=1893456000,monotonic_now=0)
occurrence,=timer.poll(wall_now=1893459600,monotonic_now=3600).occurrences
assert occurrence.scheduled_at==1893459600 and occurrence.timezone=='Asia/Shanghai'
assert 'acp' not in sys.modules
assert (resources.files('xdog.eyes')/'skills'/'eyes'/'SKILL.md').is_file()
from xdog.eyes.cli import main
assert main(['validate',str(example)])==0
assert not any(n.startswith(('xdog.ai','xdog.agent','xdog.coding')) for n in sys.modules)
print(json.dumps({'eyes':'wheel file/scheduler validation, cron occurrence, skills/examples, no harness dependencies',
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
  assert status['messages'][0]['status']=='pending' and status['inbox_id']
  assert not manager.load_session(data.session_id).messages
  assert not any(n in sys.modules for n in ('xdog.coding.core.agent_session','xdog.coding.core.sdk'))
 finally:
  await conn.close();writer.close();await writer.wait_closed();await server.aclose()
 assert len(InboxStore(manager._dir,root/'inbox').records(data.session_id,root))==1
 print(json.dumps({'coding':'wheel-only Unix ACP enqueue/status/dedup with model imports forbidden',
                   'session_id':data.session_id}))
asyncio.run(main())
'''


CODING_OWNER = r'''
import asyncio,json,sys
from pathlib import Path
from dataclasses import replace
from xdog.agent import Agent,AgentConfig
from xdog.agent.skills import SkillManager
from xdog.agent.session_input import TYPE,decode_input
from xdog.ai.types import AssistantMessage,TextContent,UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.sessions.inbox_models import InboxMessage,MAX_MESSAGE_BYTES,encoded,message_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.ownership import SessionLease,lease_directory
from xdog.coding.core.sessions.store import InputStore
from xdog.coding.core.sessions.runtime import SessionRuntime
from xdog.coding.core.settings_manager import SettingsManager
import xdog.coding.core.agent_session as module
assert Path(module.__file__).is_relative_to(Path(sys.prefix))
async def maximum_input(manager,root):
 data=manager.create_session(model='test',working_dir=root)
 store=InboxStore(manager._dir,root/'inbox');calls=[]
 value=InboxMessage(data.session_id,root,'wheel-maximum','',(),'wheel observer',{})
 overhead=len(encoded(message_data(value),MAX_MESSAGE_BYTES))
 value=replace(value,instruction='x'*(MAX_MESSAGE_BYTES-overhead))
 assert len(encoded(message_data(value),MAX_MESSAGE_BYTES))==MAX_MESSAGE_BYTES
 store.enqueue(value)
 store.enqueue(replace(value,message_id='wheel-following',instruction='After the maximum message'))
 def model(*args):
  calls.append(args)
  return EventStream.empty(AssistantMessage(content=(TextContent(text='maximum handled'),)))
 session=AgentSession(agent=Agent(model,config=AgentConfig(model='test'),skills=SkillManager(root/'skills')),
  session_data=manager.load_session(data.session_id),session_manager=manager,
  settings=SettingsManager(global_path=root/'settings.json'),working_dir=root,tool_registry=None,bash=None)
 runtime=SessionRuntime(session,bound=True,inbox_store=store)
 runtime.start(setup_pending=True)
 try:
  await runtime.activate_inputs()
  async with asyncio.timeout(15):
   while True:
    rows=store.records(data.session_id,root)
    assert runtime.controller._broken is None
    assert not any(row.status=='failed' for row in rows),[(r.status,r.error) for r in rows]
    if all(row.status=='succeeded' for row in rows) and not runtime.controller._jobs:break
    await asyncio.sleep(.01)
  assert len(calls)==2 and len(runtime.controller.store.records())==2
  assert runtime.controller.store.records()[0].input.instruction==value.instruction
 finally:
  await asyncio.wrap_future(runtime.close())
  session.dispose()
 return {'message_bytes':MAX_MESSAGE_BYTES,'model_calls':len(calls),'owner_healthy':True}

async def main():
 root=Path.cwd();manager=SessionManager(root/'sessions');data=manager.create_session(model='test',working_dir=root)
 store=InboxStore(manager._dir,root/'inbox');calls=[]
 for i in range(5):
  store.enqueue(InboxMessage(data.session_id,root,f'wheel-callback-{i}',f'Evidence message {i}',
                            ({'path':f'src/{i}.py'},),'wheel observer',{}))
 def model(_,context,options):
  calls.append(context)
  return EventStream.empty(AssistantMessage(content=(TextContent(text='wheel batch answer'),)))
 session=AgentSession(agent=Agent(model,config=AgentConfig(model='test'),skills=SkillManager(root/'skills')),
  session_data=manager.load_session(data.session_id),session_manager=manager,
  settings=SettingsManager(global_path=root/'settings.json'),working_dir=root,tool_registry=None,bash=None)
 try:
  response=await session.send_message('Process all retained evidence')
  assert response.content==(TextContent(text='wheel batch answer'),)
  assert len(calls)==1 and len(calls[0].messages)==6
  assert all(isinstance(message,UserMessage) for message in calls[0].messages)
  rows=store.records(data.session_id,root)
  assert len(rows)==5 and {row.status for row in rows}=={'succeeded'}
  assert len({row.batch_id for row in rows})==1 and rows[0].batch_id
  canonical=[decode_input(message.data) for message in session.messages if getattr(message,'type','')==TYPE]
  assert len(canonical)==5 and {item.id for item in canonical}=={row.request_id for row in rows}
  history=InputStore(session).records()
  assert len(history)==6 and {row.status for row in history}=={'succeeded'}
  assert len({row.batch_id for row in history})==1
  assert session.runtime is None
  controller=session._sdk_controller
  assert controller is not None and controller._loop.is_running()
  capability=session.capabilities.get('goal')
  assert capability is not None and capability.loop is controller._loop
  async def empty_goal():return await capability.service.current()
  assert await asyncio.wrap_future(controller.control(empty_goal,name='verify-default-goal')) is None
  assert session.permissions.mode=='ask' and not session.permissions.has_pending_requests
 finally:
  await session.aclose()
 assert session._lease is None
 assert controller._thread is not None and not controller._thread.is_alive()
 replacement=SessionLease.acquire(lease_directory(manager._dir),data.session_id)
 replacement.close()
 boundary=await maximum_input(manager,root)
 print(json.dumps({'coding_owner':'wheel-only SDK owner, five durable callbacks plus independent human input',
                   'model_calls':len(calls),'inputs':len(calls[0].messages),'message_approval':False,
                   'lease_reusable_after_dispose':True,'maximum_intake':boundary}))
asyncio.run(main())
'''


CLAW = r'''
import asyncio,json,os,sys,tempfile
from pathlib import Path
from acp import PROTOCOL_VERSION,connect_to_agent
from xdog.ai.types import AssistantMessage,TextContent,DoneEvent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.acp.server import ClawAcpServer
import xdog.claw.core.inputs.store as storage
assert Path(storage.__file__).is_relative_to(Path(sys.prefix))
class Client:
 async def session_update(self,**kwargs):raise AssertionError('enqueue has no model response stream')
async def connect(endpoint):
 reader,writer=await asyncio.open_unix_connection(endpoint)
 connection=connect_to_agent(Client(),writer,reader)
 await connection.initialize(protocol_version=PROTOCOL_VERSION)
 return connection,writer
async def main():
 root=Path.cwd();entered,release=asyncio.Event(),asyncio.Event();calls=[]
 def model(_,context,options=None):
  calls.append(context);response=AssistantMessage(content=(TextContent(text='wheel Claw reply'),))
  async def events():
   entered.set();await release.wait();yield DoneEvent(message=response)
  future=asyncio.get_running_loop().create_future();future.set_result(response)
  return EventStream.from_async_generator(events(),result_future=future)
 app=Orchestrator(ClawConfig(data_dir=str(root/'app')),model='test/dummy',stream_fn=model)
 app.register_group(Group(id='main',workspace=str(root),enabled_tools=()))
 await app.start();inputs=await app.input_service();scope=InputScope('main',root)
 namespace=await inputs.store.namespace(scope)
 with tempfile.TemporaryDirectory(prefix='claw-wheel-') as private:
  endpoint=Path(private)/'acp.sock';server=await ClawAcpServer.start(app,endpoint)
  conn,writer=await connect(endpoint)
  params={'session_id':'group:main','message_id':'wheel-event','instruction':'Inspect the delegated evidence.',
          'source':'Eyes','evidence':[{'type':'scheduler.due'}],'provenance':{}}
  try:
   status=await conn.ext_method('xdog/inbox_status',{'session_id':'group:main'})
   assert status['inbox_id']==namespace and status['cwd']==str(root)
   assert (await conn.ext_method('xdog/enqueue',params))['status']=='queued'
   await asyncio.wait_for(entered.wait(),10)
   await conn.close();writer.close();await writer.wait_closed();await server.close()
   assert (await inputs.store.get(scope,'wheel-event')).status=='running'
   server=await ClawAcpServer.start(app,endpoint);conn,writer=await connect(endpoint)
   status=await conn.ext_method('xdog/inbox_status',{'session_id':'group:main','message_id':'wheel-event'})
   assert status['inbox_id']==namespace and status['messages'][0]['status']=='running'
   release.set();result=await asyncio.wait_for(inputs.wait(scope,'wheel-event'),10)
   assert result.status=='succeeded' and result.response=='wheel Claw reply'
   assert (await conn.ext_method('xdog/enqueue',params))['status']=='duplicate' and len(calls)==1
   assert result.envelope.reply is None
   assert not (root/'app'/'observers').exists()
   print(json.dumps({'claw':'ordinary Unix ACP intake, cwdless enqueue, exact Core receipt, endpoint restart',
                     'model_calls':len(calls),'implicit_chat_reply':False,'managed_observers':False}))
  finally:
   release.set();await conn.close();writer.close();await writer.wait_closed();await server.close();await app.stop()
asyncio.run(main())
'''


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix='xdog-acp-isolated.'))
    environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    environment['UV_NO_PROJECT'] = '1'
    home = root / 'home'
    home.mkdir(mode=0o700)
    environment.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), CODEX_HOME=str(home / '.codex'))
    uv = shutil.which('uv')
    if uv is None:
        raise RuntimeError('uv is required for artifact verification')
    # Credentials/config/runtime are isolated; sharing only uv's inert package
    # cache lets offline wheel/dependency verification use already cached artifacts.
    environment['UV_CACHE_DIR'] = subprocess.check_output([uv, 'cache', 'dir'], text=True).strip()
    environment.update(XDG_DATA_HOME=str(home / '.local/share'), XDG_STATE_HOME=str(home / '.local/state'),
                       CODING_DIR=str(home / 'coding'))
    result = {'distributions': str(dist), 'root': str(root), 'environments': {}}
    for name, packages, script in (
        ('eyes', ('eyes',), EYES),
        ('agent', ('ai', 'agent'), AGENT),
        ('coding', ('ai', 'agent', 'tui', 'coding'), CODING),
        ('coding-owner', ('ai', 'agent', 'tui', 'coding'), CODING_OWNER),
        ('claw', ('ai', 'agent', 'tui', 'eyes', 'claw'), CLAW),
    ):
        env = root / (name + '-venv')
        work = root / (name + '-work')
        work.mkdir(mode=0o700)
        subprocess.run([uv, 'venv', str(env), '--python', sys.executable], env=environment, cwd=root, check=True)
        python = env / 'bin' / 'python'
        if {'coding', 'claw'}.intersection(packages):
            packages = (*packages, 'skills')
        wheels = [str(next(dist.glob('xdog_' + package + '-*.whl'))) for package in packages]
        subprocess.run([uv, 'pip', 'install', '--python', str(python), 'agent-client-protocol==0.12.1', *wheels],
                       env=environment, cwd=root, check=True)
        environment['XDOG_EYES_RUNTIME_DIR'] = str(work / 'eyes-daemon')
        file = work / 'consumer.py'
        file.write_text(script)
        args = [str(python), str(file)]
        if name == 'agent':
            peer = work / 'peer.py'
            peer.write_text(AGENT_PEER)
            args.append(str(peer))
        try:
            subprocess.run(args, env=environment, cwd=work, check=True, timeout=30)
            if name == 'claw':
                output_probe = work / 'output_consumer.py'
                output_probe.write_text(Path(__file__).with_name('verify_claw_output_artifact.py').read_text())
                subprocess.run([str(python), str(output_probe)], env=environment, cwd=work, check=True, timeout=30)
                native_probe = work / 'native_consumer.py'
                native_probe.write_text(Path(__file__).with_name('verify_claw_native_artifact.py').read_text())
                subprocess.run([str(python), str(native_probe)], env=environment, cwd=work, check=True, timeout=30)
                observer_probe = work / 'observer_output_consumer.py'
                observer_probe.write_text(Path(__file__).with_name('verify_claw_observer_output_artifact.py').read_text())
                subprocess.run([str(python), str(observer_probe)], env=environment, cwd=work, check=True, timeout=40)
            if name == 'eyes':
                subprocess.run([str(env / 'bin' / 'xdog-eyes'), '--help'],
                               env=environment, cwd=work, check=True, stdout=subprocess.DEVNULL)
        finally:
            if 'eyes' in packages:
                # Only this probe's explicit temporary instance, never a user daemon.
                subprocess.run([str(python), '-m', 'xdog.eyes', 'daemon', 'stop'],
                               env=environment, cwd=work, check=True, timeout=35)
        result['environments'][name] = str(env)
    (root / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
