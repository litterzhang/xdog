"""The effective-argument boundary for silent managed-memory maintenance."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.agent.async_work import run_owned


async def allowed(workspace: Path, params: dict[str, Any]) -> bool:
    if params.get('action') == 'write':
        return params.get('target') in {'daily', 'memory'}
    if params.get('action') != 'get' or not isinstance(params.get('filename'), str):
        return False

    def managed() -> bool:
        root = workspace.resolve()
        path = (root / params['filename']).resolve()
        return bool(path == root / 'MEMORY.md' or path.is_relative_to(root / 'memory'))
    return await run_owned(managed)
