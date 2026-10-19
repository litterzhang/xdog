"""Explicit resource lookup; install/import never registers a host or starts work."""

from __future__ import annotations

import asyncio
from importlib import resources
from pathlib import Path


def skill_directory() -> Path:
    return Path(str(resources.files("xdog.goal") / "skills" / "goals"))


async def role_instructions(role: str) -> str:
    if role not in {"worker", "reviewer"}:
        raise ValueError("Unknown Goal role resource")
    return await asyncio.to_thread(
        (resources.files("xdog.goal") / "roles" / (role + ".md")).read_text, encoding="utf-8"
    )
