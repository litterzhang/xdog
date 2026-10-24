"""Explicit read-only skill access; never register writable Claw tools implicitly."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.skills.render import render_skill_body
from xdog.agent.skills.repo import SkillRepo, _skill_from_text, is_valid_skill_name
from xdog.agent.skills.types import Skill
from xdog.ai.types import TextContent

T = TypeVar("T")


async def _read_owned(read: Callable[[threading.Event], T], cancel: asyncio.Event | None) -> T:
    """Do not orphan the reader thread on cancellation, including repeated cancel.

    Cancellation is cooperative: bounded regular-file reads normally finish quickly,
    but a stalled filesystem/native call cannot be forcibly terminated by asyncio.
    """
    if cancel is not None and cancel.is_set():
        raise asyncio.CancelledError
    stopped = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(read, stopped))
    watcher = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if watcher is not None:
            done, _ = await asyncio.wait({worker, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                raise asyncio.CancelledError
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stopped.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


class _Reader:
    def __init__(self, manager: SkillRepo, allowed: frozenset[str] | None, maximum: int, count: int) -> None:
        # Capture application-selected roots, not model-supplied paths. These private
        # manager fields are shared implementation, not a new public configuration API.
        self.roots = tuple(root.resolve() for root in manager._directories.values())
        self.packaged = {slug: root.resolve() for slug, root in manager._packaged.items()}
        self.fixed = dict(manager._fixed)
        self.references = dict(manager._references)
        selected = manager._selection
        self.allowed = (allowed if selected is None else selected if allowed is None else allowed & selected)
        self.maximum = maximum
        self.count = count

    def permitted(self, slug: str) -> bool:
        return is_valid_skill_name(slug) and (self.allowed is None or slug in self.allowed)

    def load(self, slug: str, stopped: threading.Event) -> Skill:
        if not self.permitted(slug):
            raise ValueError("Skill slug is invalid or not allowed")
        if slug in self.fixed:
            return self.fixed[slug]
        candidates: list[tuple[Path, str | None, bool]] = [(root, slug, False) for root in self.roots]
        if slug in self.packaged:
            candidates.append((self.packaged[slug], None, True))
        if slug in self.references:
            directory, packaged = self.references[slug]
            candidates.append((directory, None, packaged))
        loaded: Skill | None = None
        for root, child, packaged in candidates:
            if stopped.is_set():
                raise InterruptedError("Skill read cancelled")
            directory = root / child if child is not None else root
            try:
                with ExitStack() as stack:
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                    if os.open in os.supports_dir_fd:
                        parent = os.open(root, flags | os.O_DIRECTORY)
                        stack.callback(os.close, parent)
                        if child is not None:
                            parent = os.open(child, flags | os.O_DIRECTORY, dir_fd=parent)
                            stack.callback(os.close, parent)
                        fd = os.open("SKILL.md", flags, dir_fd=parent)
                    else:
                        # Portable fallback: reject links/escapes and pin file identity
                        # before reading. These are application guards, not an OS sandbox.
                        path = directory / "SKILL.md"
                        if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
                            raise ValueError("Unsafe skill path")
                        before = path.stat()
                        fd = os.open(path, flags)
                        stack.callback(os.close, fd)
                        after = os.fstat(fd)
                        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                            raise ValueError("Skill changed during open")
                        fd = os.dup(fd)
                    stream = stack.enter_context(os.fdopen(fd, "rb"))
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > self.maximum:
                        raise ValueError("Skill must be a bounded regular file without hardlinks")
                    if stopped.is_set():
                        raise InterruptedError("Skill read cancelled")
                    data = stream.read(self.maximum + 1)
                    if len(data) > self.maximum:
                        raise ValueError("Skill exceeds the configured byte limit")
                    current = _skill_from_text(directory, data.decode("utf-8"), slug=slug, packaged=packaged)
                    if loaded is not None and loaded != current:
                        raise ValueError(f'Duplicate skill: {slug}')
                    loaded = current
            except FileNotFoundError:
                continue  # A missing tier may resolve below; an unsafe tier may not.
        if loaded is not None:
            return loaded
        raise ValueError("Skill is not available in the configured catalog")

    def listing(self, stopped: threading.Event) -> dict[str, Any]:
        slugs = set(self.packaged) | set(self.fixed) | set(self.references)
        scanned = 0
        for root in self.roots:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        if stopped.is_set():
                            raise InterruptedError("Skill read cancelled")
                        scanned += 1
                        if scanned > 4096:
                            raise ValueError("Skill directory exceeds the bounded catalog scan")
                        if entry.is_dir(follow_symlinks=False) and self.permitted(entry.name):
                            slugs.add(entry.name)
            except FileNotFoundError:
                continue
        rows, unavailable = [], []
        eligible = sorted(slug for slug in slugs if self.permitted(slug))
        for slug in eligible[: self.count]:
            try:
                skill = self.load(slug, stopped)
            except (ValueError, OSError, UnicodeError):
                if stopped.is_set():
                    raise InterruptedError("Skill read cancelled") from None
                unavailable.append(slug)
                continue
            rows.append(
                {
                    "slug": slug,
                    "name": skill.name[:128],
                    "description": skill.description[:512],
                    "scope": skill.scope,
                    "packaged": skill.packaged,
                }
            )
        return {"skills": rows, "unavailable": unavailable, "truncated": len(eligible) > self.count}


def create_skill_tool(
    manager: SkillRepo,
    *,
    allowed_slugs: Sequence[str] | None = None,
    max_skill_bytes: int = 256 * 1024,
    max_listed_skills: int = 128,
) -> AgentTool:
    """Create an opt-in ``skill`` tool with list/load only.

    ``load`` returns instructions as a normal tool result; it does not activate,
    unload, execute, or change permission policy. Source conflicts fail closed.
    Applications can restrict names with ``allowed_slugs`` (empty denies
    all). Roots are captured at construction; create a new tool to reconfigure them.
    File bytes, catalog scan/count and rendered output are bounded. The host must
    trust the configured roots and skill text: this is not a hostile-code sandbox.
    Cancellation waits for the owned reader; it cannot interrupt a stuck filesystem.
    ``allowed_slugs`` filters this tool, not a separately configured Agent skill index.
    """
    if type(max_skill_bytes) is not int or not 1 <= max_skill_bytes <= 1024 * 1024:
        raise ValueError("max_skill_bytes must be between 1 and 1048576")
    if type(max_listed_skills) is not int or not 1 <= max_listed_skills <= 512:
        raise ValueError("max_listed_skills must be between 1 and 512")
    if allowed_slugs is not None and (
        isinstance(allowed_slugs, str)
        or len(allowed_slugs) > 512
        or any(not isinstance(slug, str) or not is_valid_skill_name(slug) for slug in allowed_slugs)
    ):
        raise ValueError("allowed_slugs must contain bounded valid skill names")
    reader = _Reader(
        manager, None if allowed_slugs is None else frozenset(allowed_slugs), max_skill_bytes, max_listed_skills
    )

    def validate(args: dict[str, Any]) -> None:
        action = args.get("action")
        if action not in ("list", "load") or set(args) - {"action", "slug"}:
            raise ValueError("Only read-only list/load skill actions are supported")
        if action == "load" and (not isinstance(args.get("slug"), str) or not reader.permitted(args["slug"])):
            raise ValueError("A valid allowed skill slug is required")
        if action == "list" and "slug" in args:
            raise ValueError("list does not accept a slug")

    async def execute(
        call_id: str, args: dict[str, Any], cancel: asyncio.Event | None = None, on_update: Any = None, **kwargs: Any
    ) -> AgentToolResult:
        try:
            validate(args)
            if args["action"] == "list":
                text = json.dumps(await _read_owned(reader.listing, cancel), ensure_ascii=False)
                details: Any = None
            else:
                skill = await _read_owned(lambda stopped: reader.load(args["slug"], stopped), cancel)
                text = f"# Skill: {skill.name} [{skill.slug}]\n\n{render_skill_body(skill)}"
                details = {
                    "slug": skill.slug,
                    "scope": skill.scope,
                    "packaged": skill.packaged,
                    "activation": "tool-result-only",
                }
            if len(text.encode("utf-8")) > max(128 * 1024, max_skill_bytes + 8192):
                raise ValueError("Skill result exceeds the configured output limit")
            return AgentToolResult(content=(TextContent(text=text),), details=details)
        except (OSError, UnicodeError):
            return AgentToolResult(
                content=(TextContent(text="Skill file unavailable or unsafe; no fallback read."),), is_error=True
            )
        except ValueError as exc:
            return AgentToolResult(content=(TextContent(text=str(exc)),), is_error=True)

    return AgentTool(
        name="skill",
        label="Read skills",
        description=(
            "Read available procedural skills. action=list lists metadata; action=load with a slug reads its full "
            "instructions and resource directory. Read-only: no activation, writes, script execution "
            "or permission grant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "load"]},
                "slug": {"type": "string", "maxLength": 64},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        execute=execute,
        validate_arguments=validate,
    )
