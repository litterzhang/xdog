"""Cold wheel-only actual Claw+Goal lifecycle; never a provider or user service."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix="claw-goal-installed-"))
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
    wheels = [str(next(dist.glob("xdog_" + name + "-*.whl"))) for name in ("ai", "agent", "tui", "claw", "skills")]
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--find-links", str(dist), *wheels],
        cwd=root, env=environment, check=True,
    )
    work = root / "work"
    work.mkdir(mode=0o700)
    # External test consumer only. Runtime always imports from installed wheels;
    # no project source path, tests package import or editable installation.
    source = Path(__file__).parents[1] / "packages/claw/tests/goal_installed_consumer.py"
    consumer = work / "consumer.py"
    consumer.write_text(source.read_text())
    base_work = root / "base-work"
    base_work.mkdir(mode=0o700)
    subprocess.run([str(python), str(consumer), "--base-only"], cwd=base_work, env=environment, check=True, timeout=40)
    subprocess.run([str(python), str(consumer)], cwd=work, env=environment, check=True, timeout=40)
    result = {"root": str(root), "distribution": str(dist), "environment": str(target), "real_host": "claw"}
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
