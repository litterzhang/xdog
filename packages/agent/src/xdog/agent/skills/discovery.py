"""Discovering skills that ship inside installed packages.

A distribution can carry a skill by putting a ``skills/<slug>/SKILL.md`` inside its
package directory — ``xdog-flow`` does, teaching an agent to write flow
workflows. Nothing has to be registered: this walks the ``xdog`` namespace and
picks up whatever is installed, so ``pip install xdog-flow`` makes the skill
appear and ``pip uninstall`` makes it go away.

That matters more than it sounds. The alternative — copying SKILL.md into a
user-level skills directory — was how this was done before, and the copy
silently drifted from the package it documented, teaching the agent an API that
had already changed underneath it. Reading it out of the installed package is
the only version that cannot go stale.
"""
from __future__ import annotations

import logging
import pkgutil
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.agent.skills.types import Skill

logger = logging.getLogger(__name__)

#: Directory a package puts its skills in. Each subdirectory is one skill,
#: named after it — the standard requires a skill's `name` to match its parent
#: directory, so the directory cannot be a fixed word like `skill`.
SKILLS_DIRNAME = "skills"


def packaged_skills() -> dict[str, Path]:
    """Map slug → skill directory for every installed ``xdog.*`` package.

    A package ships ``skills/<name>/SKILL.md``, and the slug is ``<name>``.
    Addressing them by directory rather than by package is what the standard
    requires, and it means one distribution can carry several skills — which a
    single fixed ``skill/`` directory could not.

    Unreadable optional packages are skipped with a log line. Conflicting
    packaged skill names fail explicitly rather than depending on import order.
    """
    found: dict[str, Path] = {}
    try:
        import xdog
    except ImportError:  # pragma: no cover - xdog is this package's own namespace
        return found

    for module in pkgutil.iter_modules(xdog.__path__, "xdog."):
        try:
            container = Path(str(files(module.name))) / SKILLS_DIRNAME
            if not container.is_dir():
                continue
            for skill_dir in sorted(container.iterdir()):
                if (skill_dir / "SKILL.md").is_file():
                    previous = found.get(skill_dir.name)
                    if previous is not None and previous.resolve() != skill_dir.resolve():
                        raise ValueError(f'Duplicate packaged skill: {skill_dir.name}')
                    found[skill_dir.name] = skill_dir
        except (ImportError, TypeError, OSError, ModuleNotFoundError) as exc:
            logger.debug("skipping %s while looking for skills: %s", module.name, exc)

    return found


def load_packaged_skill(slug: str) -> "Skill | None":
    """Load one skill by slug, from installed packages only.

    Deliberately narrower than :meth:`SkillRepo.load_skill`, which also reads
    user and group directories. A flow workflow is a shareable artifact: if its
    agent nodes picked up whatever skills happened to be on the machine, the same
    workflow would behave differently for two people and neither could tell from
    the file. Packaged skills are versioned with the code that installs them, so
    naming one in a workflow names a specific thing.

    Returns None when nothing is installed under that slug, which the caller
    should treat as an authoring error rather than a missing nicety -- an agent
    told to write a format it was never shown will write something plausible and
    wrong.
    """
    from xdog.agent.skills.repo import _load_skill_from_dir

    directory = packaged_skills().get(slug)
    if directory is None:
        return None
    return _load_skill_from_dir(directory, slug=slug, packaged=True)
