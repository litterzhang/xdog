"""Cold Goal-only storage smoke from wheels; never a provider or host runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = r"""
import asyncio,importlib.metadata as md,json,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from xdog.agent import Agent
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.skills import SkillManager,packaged_skills
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Goal,Owner
from xdog.skills.goal import store as installed
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.runtime import GoalRuntime
assert Path(installed.__file__).is_relative_to(Path(sys.prefix))
names={d.metadata['Name'].lower().replace('_','-') for d in md.distributions()}
assert 'xdog-skills' in names and 'xdog-goal' not in names
blocked=('xdog.claw','xdog.coding','xdog.eyes','xdog.flow','xdog.tui','xdog.site')
assert not names.intersection({'xdog-claw','xdog-coding','xdog-eyes','xdog-flow','xdog-tui','xdog-site'})
async def main():
 root=Path.cwd(); manager=SkillManager(root/'not-created-shared-skills')
 for slug in ('goal','plan'):
  item=manager.load_skill(slug)
  assert item is not None and item.packaged and item.content
  assert packaged_skills()[slug].is_relative_to(Path(sys.prefix))
 assert not (root/'not-created-shared-skills').exists()
 owner=Owner('goal-only-store',root)
 s=await GoalStore.open(root/'private'/'registry.sqlite3',owner)
 goal=Goal('goal','Portable','Inspect real output',('Evidence is present',))
 await s.put(goal,expected_version=0)
 await s.reminder(goal,{'id':'notice','instruction':'data only'})
 assert await s.claim_reminder('notice',goal)
 await s.close()
 s=await GoalStore.open(s.path,owner)
 try:
  assert (await s.current()).id=='goal'
  assert (await s.reminders())[0]['status']=='unknown'
  assert len(await s.history('goal'))==1
 finally: await s.close()
 async def no_endpoint(): return None
 host=SimpleNamespace(scope='group',owner_prefix='sdk',namespace='original',identity='main',branch='',
  workspace=root,storage_root=root/'host',valid=lambda:True,accepting=lambda:True,
  subscribe_events=lambda cb:lambda:None,acp_target=no_endpoint,
  execution_snapshot=lambda:{'state':'idle','user_pending':False,'children_active':False,'generation':'one'})
 runtime=await GoalRuntime.open(host)
 def forbidden_model(*args): raise AssertionError('Commands must not call another model')
 agent=Agent(forbidden_model); extensions=ExtensionRuntime((),root/'extensions',root)
 extensions.reserve_commands({'help'},skill_commands={'goal'})
 parent=ParentPort(agent,extensions,SimpleNamespace(),lambda:True,lambda:True)
 host.owns_parent=lambda selected:selected is parent
 runtime.attach(parent)
 try:
  assert extensions.command_owner('goal')=='goal'
  await extensions.command('goal','create Goal-only main Agent work')
  await runtime.flush()
  service=runtime.service; current=await service.current()
  report={'expected_version':0,'progress':'baseline','summary':'Installed initial assessment',
   'completed':[],'evidence':[],'blockers':[],'next_steps':['Inspect actual result']}
  result=await runtime.tools[0].execute('report',{'action':'report','report':report})
  assert not result.is_error
  summary=await service.summary()
  assert summary['latest_report']['version']==1 and 'plan' not in summary and 'tasks' not in summary
  child="from pathlib import Path; import sys; from xdog.skills.goal.files import TaskFiles; "
  child+="r=TaskFiles(Path(sys.argv[1]),sys.argv[2]).latest_report(); assert r['version']==1; "
  child+="assert r['progress']=='baseline'"
  subprocess.run([sys.executable,'-c',child,str(root),current.id],check=True)
  await service.plan('I will perform this task myself')
  await service.task({'id':'one','title':'Inspect actual output','expected_version':0})
  task_path=service.files(current).tasks_dir/'one.json'
  raw=json.loads(task_path.read_text()); raw.update(status='completed',summary='Main inspected',version=2)
  task_path.write_text(json.dumps(raw))
  assert (await service.status())['tasks'][0]['status']=='completed'
  await service.change('complete',summary='Main checked evidence; no reviewer')
  assert (await service.current()).status=='completed'
  assert (await service.list())[0].status=='completed'
  assert (await service.summary())['current']['id']==current.id
  assert (await service.summary())['last_transition']['action']=='complete'
  assert extensions.enabled==() and not agent.messages
  assert not any(n.startswith(blocked) for n in sys.modules)
 finally:
  await runtime.aclose();await extensions.aclose();await agent.aclose()
 print(json.dumps({'goal_only':'default native extension commands, skill, authoritative task files/history',
  'recovery':'unknown send retained, no model/worker replay','model_calls':0,'child_support_required':False}))
asyncio.run(main())
"""


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix="goal-only-installed-"))
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment["UV_NO_PROJECT"] = "1"
    environment["HOME"] = str(root / "home")
    environment["XDG_CONFIG_HOME"] = str(root / "home" / ".config")
    environment["CODEX_HOME"] = str(root / "home" / ".codex")
    Path(environment["HOME"]).mkdir(mode=0o700)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for isolated artifact verification")
    target = root / "venv"
    subprocess.run([uv, "venv", str(target), "--python", sys.executable], cwd=root, env=environment, check=True)
    python = target / "bin" / "python"
    wheels = [str(next(dist.glob("xdog_" + package + "-*.whl"))) for package in ("ai", "agent", "skills")]
    subprocess.run([uv, "pip", "install", "--python", str(python), *wheels], cwd=root, env=environment, check=True)
    work = root / "work"
    work.mkdir(mode=0o700)
    probe = work / "consumer.py"
    probe.write_text(PROBE)
    subprocess.run(
        [str(python), "-c", "import sys,xdog.skills; "
         "assert not any(n.startswith(('xdog.skills.goal','xdog.agent','xdog.ai','xdog.tui')) for n in sys.modules)"],
        cwd=work, env=environment, check=True, timeout=10,
    )
    subprocess.run([str(python), str(probe)], cwd=work, env=environment, check=True, timeout=30)
    result = {"root": str(root), "distribution": str(dist), "environment": str(target), "kernel_only": True}
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
