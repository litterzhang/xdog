"""Selected packaged extension resources; discovery never starts a Python worker."""
from __future__ import annotations

import pkgutil
from importlib.resources import files
from pathlib import Path

from xdog.agent.extensions.api import identifier


def extension_directory(name: str, root: Path) -> Path | None:
    """Keep explicit local selection authoritative, otherwise resolve one package."""
    name = identifier(name)
    local = root / name
    try:
        local.lstat()
    except FileNotFoundError:
        pass
    else:
        # Corruption, wrong identity and dangling links fail in the normal loader;
        # they must not silently select a different installed implementation.
        return local
    import xdog
    candidates: dict[Path, Path] = {}
    for module in pkgutil.iter_modules(xdog.__path__, "xdog."):
        try:
            path = Path(str(files(module.name))) / "extensions" / name
        except (ImportError, TypeError):
            continue  # An unrelated optional distribution is not this selection.
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        # Deduplicate physical aliases without rewriting the selected basename;
        # the existing manifest identity check must still see the configured ID.
        candidates.setdefault(path.resolve(), path)
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous installed extension: {name}")
    return next(iter(candidates.values()), None)
