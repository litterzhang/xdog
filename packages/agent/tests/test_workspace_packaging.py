"""Workspace distribution must not publish local harness worktrees/state."""
from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path


def test_umbrella_sdist_never_includes_local_claude_state(tmp_path):
    repository = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ['uv', 'build', '--package', 'xdog', '--sdist', '--out-dir', str(tmp_path)],
        cwd=repository, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    with tarfile.open(next(tmp_path.glob('xdog-*.tar.gz'))) as archive:
        leaked = [name for name in archive.getnames() if '.claude' in Path(name).parts]
    assert not leaked, f'Local Claude state leaked into umbrella sdist: {leaked[:5]}'
