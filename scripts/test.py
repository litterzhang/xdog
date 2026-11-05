"""Run the entire configured pytest suite once with two workers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # Use the same testpaths as direct pytest. One process owns the worker budget;
    # never multiply package pools or select a smaller CI-only suite.
    return subprocess.call([
        sys.executable, '-m', 'pytest', '-c', str(ROOT / 'pyproject.toml'),
        '-q', '-n', '2', '--timeout=120', '--timeout-method=thread',
    ], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
