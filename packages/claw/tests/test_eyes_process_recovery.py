"""Data-only managed time state survives real interpreter/process replacement."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROGRAM = r'''
import asyncio,json,sys
from datetime import UTC,datetime
from pathlib import Path
from xdog.ai.types import AssistantMessage,TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.backend import ClawBackend
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group,GroupConfig,UserInput
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.eyes.loader import definition_data
from xdog.eyes.managed_scheduler import SchedulerClocks,initial_managed_state
root,endpoint,phase=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
clock=2_000_000_000.0+(0 if phase=='create' else 100)
count=[]
class ProcessSink(TuiChannel):
 def accepts_saved_reply(self,original):
  return original.channel=='tui' and original.metadata.get('account_id')=='process-test-sink'
def model(*args):
 count.append(1)
 with (root/'calls').open('a') as f:f.write('model\n')
 return EventStream.empty(AssistantMessage(content=(TextContent(text='cross-process report'),)))
async def main():
 config=ClawConfig(data_dir=str(root/'data'),eyes_enabled=True,enabled_tools=('eyes',))
 orch=Orchestrator(config,model='test/process',stream_fn=model)
 orch.register_group(Group(id='main',workspace=str(root),enabled_tools=('eyes',),config=GroupConfig(debounce_ms=0)))
 clocks=SchedulerClocks(lambda:clock,lambda:clock)
 path=root/'private'/'inbox.sqlite3'
 if phase=='create':
  service=ManagedObservers(orch,endpoint,clocks=clocks)
  await service._ensure_store()
  definition=service.definition('main','persisted',{'type':'scheduler','schedule':{
   'at':datetime.fromtimestamp(clock+10,UTC).isoformat()}},'Report after restart')
  state=initial_managed_state(definition,'eyes-process',clocks)
  target=ReplyReference(InputScope('main',root),'old-process-binding',UserInput(group_id='main',
   sender='test-sink',channel='tui',metadata={'account_id':'process-test-sink'}))
  await service.store.create('main',root,'persisted',definition_data(definition),identifier='eyes-process',state=state,
   reply=target)
  await service.close()
 else:
  channel=ProcessSink()
  async def send(group,text):
   assert group=='main' and text=='cross-process report'
   with (root/'reports').open('a') as f:f.write('delivered\n')
  channel.send_message=send
  orch.add_channel(channel)
  server=await ClawAcpServer.start(orch,endpoint)
  service=ManagedObservers(orch,endpoint,clocks=clocks)
  orch.set_observers(service)
  try:
   await service.start()
   async with asyncio.timeout(8):
    while (await service.status('main','eyes-process'))['state']!='completed':await asyncio.sleep(.01)
   assert len(count)==(1 if phase=='execute' else 0)
  finally:
   await service.close()
   await server.close()
 await orch.stop()
 print(json.dumps({'phase':phase,'model_calls':len(count)}))
asyncio.run(main())
'''


def test_three_processes_restore_expired_once_and_do_not_replay(tmp_path):
    with tempfile.TemporaryDirectory(prefix='eyes-process-') as private:
        endpoint = Path(private) / 'acp.sock'
        results = []
        for phase in ('create', 'execute', 'verify'):
            result = subprocess.run([sys.executable, '-c', PROGRAM, str(tmp_path), str(endpoint), phase],
                                    capture_output=True, text=True, timeout=20, env=os.environ.copy())
            assert result.returncode == 0, result.stdout + result.stderr
            results.append(result.stdout)
        assert (tmp_path / 'calls').read_text().splitlines() == ['model']
        assert (tmp_path / 'reports').read_text().splitlines() == ['delivered']
        assert all(f'"phase": "{phase}"' in value for phase, value in zip(('create', 'execute', 'verify'), results))
