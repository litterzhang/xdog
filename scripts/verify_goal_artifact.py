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
asyncio.run(main())
"""


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix="goal-only-installed-"))
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment["UV_NO_PROJECT"] = "1"
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
    probe = work / "consumer.py"
    probe.write_text(PROBE)
    subprocess.run([str(python), str(probe)], cwd=work, env=environment, check=True, timeout=30)
    result = {"root": str(root), "distribution": str(dist), "environment": str(target), "kernel_only": True}
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
