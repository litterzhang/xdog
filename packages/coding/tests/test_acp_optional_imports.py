"""Non-ACP coding modes remain useful without the optional version-bounded SDK."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize('future',[False,True])
def test_native_navigation_and_print_do_not_import_absent_acp_sdk(tmp_path,future):
    script = '''
import asyncio,sys
from pathlib import Path
from types import SimpleNamespace
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='acp' or fullname.startswith('acp.'):
   raise ImportError('ACP deliberately absent')
sys.meta_path.insert(0,Block())
from xdog.agent import Agent,AgentConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.core.sessions import availability
if sys.argv[2]=='True':
 availability.sys=SimpleNamespace(version_info=(3,15))
root=Path(sys.argv[1]);manager=SessionManager(root/'sessions')
def make():
 return AgentSession(agent=Agent(lambda *_:EventStream.empty(AssistantMessage()),
  config=AgentConfig(model='test'),skills=SkillManager(root/'skills')),
  session_data=manager.create_session(model='test',working_dir=root),session_manager=manager,
  settings=SettingsManager(),tool_registry=None,bash=None,working_dir=root)
session=make();session._resolve_selected_model=lambda _:None
ui=InteractiveMode(session);ui._session_events.start()
async def navigate():
 return await asyncio.to_thread(session.new_session)
try:
 session.runtime.controller.control(navigate,name='new').result(timeout=5)
 assert session.runtime.server is None
finally:
 ui._request_exit();ui._session_events.close()
from xdog.coding.modes.print_mode import run_print_mode
owner=make()
try:
 assert asyncio.run(run_print_mode(owner,'offline input'))==0
finally:
 owner.dispose()
assert 'acp' not in sys.modules
'''
    result = subprocess.run([sys.executable,'-c',script,str(tmp_path),str(future)],
                            capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_explicit_acp_reports_a_useful_sdk_requirement_without_starting_session(tmp_path):
    script='''
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='acp' or fullname.startswith('acp.'):
   raise ImportError('ACP deliberately absent')
sys.meta_path.insert(0,Block())
from xdog.coding.main import run_agent
try:
 run_agent(acp=True)
except RuntimeError as e:
 assert 'ACP' in str(e) and '3.12' in str(e)
else:
 raise AssertionError('explicit ACP cannot start without SDK')
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
