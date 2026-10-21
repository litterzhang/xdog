"""Core claw runtime.

Importing this package loads all domains, which registers their tools
with the tool registry. Installed business capabilities are composed separately
on their actual runtime owners. The boot order is:

1. ``tools/``    — registers agent builtins (filesystem, bash, current_time, todo)
2. ``runtime/``  — registers message tool
3. ``memory/``   — registers memory tool
4. ``skills/``   — registers skill tool
"""
import xdog.claw.core.memory  # noqa: F401 — memory
import xdog.claw.core.runtime  # noqa: F401 — message
import xdog.claw.core.skills  # noqa: F401 — skills
import xdog.claw.core.tools  # noqa: F401 — agent builtins
