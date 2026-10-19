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
import asyncio,importlib.metadata as md,json,sys
from pathlib import Path
from xdog.goal.models import Goal,Notice,Owner
from xdog.goal import store as installed
from xdog.goal.store import GoalStore
assert Path(installed.__file__).is_relative_to(Path(sys.prefix))
names={d.metadata['Name'].lower().replace('_','-') for d in md.distributions()}
assert not names.intersection({'xdog-claw','xdog-coding','xdog-eyes','xdog-flow','xdog-tui','xdog-site'})
blocked=('xdog.claw','xdog.coding','xdog.eyes','xdog.flow','xdog.tui','xdog.site')
assert not any(n.startswith(blocked) for n in sys.modules)
async def main():
 root=Path.cwd();owner=Owner('goal-only-host-binding',root)
 s=await GoalStore.open(root/'private'/'goals.sqlite3',owner)
 goal=Goal('goal','Portable','Inspect real output',('Evidence is present',))
 await s.put(goal,expected_version=0,notices=(Notice('notice','goal','Inspect report'),),
             artifacts=(('original-evidence',{'files':['result.txt']}),))
 attempt=await s.notice_started('notice')
 await s.close()
 s=await GoalStore.open(s.path,owner)
 try:
  assert (await s.get('goal')).status=='active'
  assert (await s.notices())[0].status=='unknown'
  assert await s.artifact('goal','original-evidence')=={'files':['result.txt']}
  assert len(await s.history('goal'))==1
 finally:await s.close()
 print(json.dumps({'goal_only':'wheel-owned storage/history/notice recovery, no installed/imported hosts',
                   'model_calls':0,'path':str(root)}))
async def lifecycle():
 from agent_host import AgentHost
 from xdog.goal.host import OperationContext
 from xdog.goal.models import report_digest
 from xdog.goal.service import GoalService
 from xdog.agent.skills.manager import SkillManager
 from xdog.goal.resources import skill_directory,role_instructions
 root=Path.cwd();root.joinpath('result.txt').write_text('actual installed result')
 owner=Owner('goal-only-lifecycle',root);host=AgentHost(owner)
 service=GoalService(await GoalStore.open(root/'lifecycle-private'/'goal.sqlite3',owner),host)
 host.service=service
 try:
  goal=await service.create('Portable','Inspect actual result',('Actual result is present',),
                            context=OperationContext(True))
  goal=await service.add_task(goal.id,'Read actual result')
  goal=await service.assign(goal.id,goal.tasks[0].id,'Inspect and report actual file')
  await host.wait(goal.tasks[0].attempts[-1].worker)
  goal=await service.status(goal.id);task=goal.tasks[0];attempt=task.attempts[-1]
  assert task.state=='submitted' and goal.status!='completed'
  goal=await service.accept_task(goal.id,task.id,attempt.id,1,report_digest(attempt.reports[-1]),'Inspected')
  goal=await service.request_complete(goal.id,paths=('result.txt',))
  await host.wait(goal.review.worker)
  goal=await service.reconcile(goal.id)
  assert goal.status=='completed' and len(set(host.agent_ids))==2
  await service.drain_notifications()
  assert host.accepted and all(n.status=='accepted' for n in await service.store.notices())
  view=await service.export_views(goal.id)
  assert view.joinpath('status.json').is_file()
  skill=SkillManager(root/'not-created',packaged={'goals':skill_directory()}).load_skill('goals')
  assert skill is not None and skill.scope=='session' and not (root/'not-created').exists()
  assert await role_instructions('worker') and await role_instructions('reviewer')
  assert len(host.model_calls)==5
  print(json.dumps({'goal_only_lifecycle':'real Agent/Supervisor/tool IO, explicit acceptance/independent review',
                    'scripted_model_calls':len(host.model_calls),'distinct_agents':len(set(host.agent_ids))}))
 finally:
  await host.close();await service.close()
asyncio.run(main())
asyncio.run(lifecycle())
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
    wheels = [str(next(dist.glob("xdog_" + package + "-*.whl"))) for package in ("ai", "agent", "goal")]
    subprocess.run([uv, "pip", "install", "--python", str(python), *wheels], cwd=root, env=environment, check=True)
    work = root / "work"
    work.mkdir(mode=0o700)
    # This copies the external consuming host, never Goal runtime source.
    consumer_host = Path(__file__).parents[1] / "packages/goal/tests/agent_host.py"
    (work / "agent_host.py").write_text(consumer_host.read_text())
    probe = work / "consumer.py"
    probe.write_text(PROBE)
    subprocess.run([str(python), str(probe)], cwd=work, env=environment, check=True, timeout=30)
    result = {"root": str(root), "distribution": str(dist), "environment": str(target), "kernel_only": False}
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
