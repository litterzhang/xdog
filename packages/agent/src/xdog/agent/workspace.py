"""Workspace policy — what a run may touch, and how each node kind is held to it.

One module because the two halves must agree, and because both of flow's engines
need them and a generated module cannot import flow.

The two halves are enforced very differently, and the difference is the design
rather than an accident of implementation:

* **Agent nodes are told.** :func:`workspace_briefing` puts the workspace and the
  granted directories in the system prompt. Nothing checks that the model obeys —
  a node's tools are its own, an agent can be handed any tool at all, and there is
  no chokepoint where every possible tool's file access could be inspected. So
  this is a *promise the agent keeps*, stated plainly, and worth having because a
  model that is told where its files go puts them there.

* **Script nodes are audited.** :func:`script_bound` installs a PEP 578 audit
  hook that refuses paths outside the bound. This is real enforcement against
  code that is not trying to escape.

**The audit hook is containment, not a sandbox.** It runs in the executor's own
interpreter, where the hook function is a reachable, mutable object; a script
that wants out can reassign the closure cell holding the roots, or overwrite
``_hook.__code__``, both in about two lines. Immutable roots and deleting the
module-level name do not help — this was tested, not assumed. What it does stop,
completely, is the realistic failure: a script that wanders, builds a path from a
bad input, or was written without knowing where it was allowed to write. Nothing
here defends against a script authored to defeat it; that needs a child process
and an OS bound (see ``docs/archive/flow/script-node-confinement.md``).
"""

from __future__ import annotations

import contextlib
import site
import sys
import sysconfig
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

__all__ = ["workspace_briefing", "script_bound", "UnauditableCall"]


class UnauditableCall(PermissionError):
    """A confined script reached for something the audit hook cannot follow.

    Distinct from an ordinary path refusal: the path checks *worked*, and the
    problem is that this call leaves the region where they apply at all.
    """


# ---------------------------------------------------------------------------
# Agent nodes: told, not checked
# ---------------------------------------------------------------------------


def workspace_briefing(
    workspace: object,
    allow_paths: Sequence[object] = (),
    *,
    confined: bool = False,
) -> str:
    """The lines appended to an agent node's system prompt.

    Added for **every** agent node, confined or not, and regardless of which
    tools it declares. Keying this off the tool list would be a guess: a custom
    tool can touch the filesystem, an MCP server can, and a model can describe a
    path for a downstream node to use. The workspace is where this run's files
    belong, which is true of a node whether or not we can see how it writes them.

    A bound the model cannot see is one it can only discover by tripping over it,
    which costs a turn and reads as a malfunction rather than a rule.
    """
    if workspace is None:
        return ""
    allowed = [str(workspace), *(str(p) for p in allow_paths if str(p) != str(workspace))]
    lines = [
        "",
        "",
        f"Your workspace is {workspace}. Relative paths resolve there, and that "
        "is where files you produce belong.",
    ]
    if len(allowed) > 1:
        lines.append("You may also read and write these: " + ", ".join(allowed[1:]) + ".")
    lines.append(
        "Do not read or write anything outside "
        + ("those directories" if len(allowed) > 1 else "that directory")
        + ". If the task seems to need a path you have not been given, say so "
        "rather than trying variations of it."
    )
    if confined:
        lines.append(
            "This run is confined: a file tool call outside those directories "
            "returns an error and touches nothing."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Script nodes: audited
# ---------------------------------------------------------------------------

#: Events carrying a path this run may not write outside the bound.  Each entry
#: maps an event name to the indices of its path arguments.  Derived from
#: CPython's audit table; an event missing here is an event not enforced, which
#: is why the list is written out rather than pattern-matched.
_WRITE_EVENTS: dict[str, tuple[int, ...]] = {
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.symlink": (0, 1),
    "os.link": (0, 1),
    "os.utime": (0,),
    "shutil.copyfile": (0, 1),
    "shutil.copymode": (0, 1),
    "shutil.copystat": (0, 1),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
}

#: Suffixes that make a file *code* rather than data.  A code root grants reads
#: of these and nothing else, so a module can import its siblings while a data
#: file sitting in the same directory stays outside the bound.
_CODE_SUFFIXES: frozenset[str] = frozenset({".py", ".pyc", ".pyd", ".so"})

#: Events that leave the region the hook can see at all.  Refused only when the
#: run is confined: without that flag a script node is unrestricted today, and
#: silently breaking a workflow that shells out would be a worse surprise than
#: the hole it closes.
_UNAUDITABLE: frozenset[str] = frozenset({
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "pty.spawn",
    "ctypes.dlopen",
    "ctypes.dlsym",
    "ctypes.dlsym/handle",
    "ctypes.call_function",
    "ctypes.addressof",
    "cpython.run_file",
})


class _Bound:
    __slots__ = ("write_roots", "read_roots", "code_roots", "confined")

    def __init__(
        self,
        write_roots: tuple[Path, ...],
        read_roots: tuple[Path, ...],
        code_roots: tuple[Path, ...],
        confined: bool,
    ):
        self.write_roots = write_roots
        self.read_roots = read_roots
        self.code_roots = code_roots
        self.confined = confined


_BOUND: ContextVar[_Bound | None] = ContextVar("xdog_script_bound", default=None)
_INSTALLED = False


def _import_roots() -> tuple[Path, ...]:
    """Directories a script must be able to *read* or it cannot import anything.

    `import textwrap` raises an `open` audit event for the module's file, so a
    hook that policed reads against the workspace alone would break the first
    import in every script — which would look like the feature simply not
    working. The interpreter's own trees are therefore always readable.
    """
    candidates = [sys.prefix, sys.base_prefix, sysconfig.get_path("stdlib") or ""]
    with contextlib.suppress(Exception):
        candidates.extend(site.getsitepackages())
    with contextlib.suppress(Exception):
        candidates.append(site.getusersitepackages())
    # Deliberately NOT all of `sys.path`. That is environment-dependent -- a
    # bundle run as `python <dir>` has its own directory at sys.path[0], so
    # reading it granted the compiled engine a root the interpreter never had,
    # and the same workflow behaved differently depending on how it was run.
    # Workflow-local code is granted explicitly instead, via `code_roots`.
    roots: list[Path] = []
    for c in candidates:
        with contextlib.suppress(OSError, ValueError):
            roots.append(Path(c).resolve())
    return tuple(dict.fromkeys(roots))


def _within(path: object, roots: Sequence[Path]) -> bool:
    if not isinstance(path, (str, bytes)):
        return True  # an fd or a buffer: not a path this hook can judge
    text = path.decode("utf-8", "replace") if isinstance(path, bytes) else path
    if not text:
        return True
    try:
        resolved = Path(text).resolve()
    except (OSError, ValueError):
        return False
    return any(resolved == r or r in resolved.parents for r in roots)


def _is_code(path: object, code_roots: Sequence[Path]) -> bool:
    """True when *path* is an importable file inside a code root.

    Loading a module from beside the workflow is trust the run already extends:
    that is how the script node itself was resolved. Granting the whole directory
    would be a different thing -- `base_dir` is wherever the .json happens to
    live, so a workflow in a home directory would make ~/.ssh readable.
    """
    if not code_roots or not isinstance(path, str):
        return False
    return Path(path).suffix in _CODE_SUFFIXES and _within(path, code_roots)


def _hook(event: str, args: tuple[Any, ...]) -> None:
    bound = _BOUND.get()
    if bound is None:
        return  # not inside a script node: the executor's own I/O is untouched
    if event in _UNAUDITABLE:
        if bound.confined:
            raise UnauditableCall(
                f"{event} cannot be audited, and this run is confined. "
                f"Remove it, or run without --confined."
            )
        return
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else ""
        writing = isinstance(mode, str) and any(c in mode for c in "wxa+")
        roots = bound.write_roots if writing else bound.read_roots
        if not writing and not _within(path, roots) and _is_code(path, bound.code_roots):
            return  # importing a sibling module, which resolution already trusts
        if not _within(path, roots):
            verb = "write" if writing else "read"
            allowed = ", ".join(str(r) for r in bound.write_roots) or "<nothing>"
            raise PermissionError(
                f"Cannot {verb} {path}: outside this run's workspace. Allowed: {allowed}"
            )
        return
    indices = _WRITE_EVENTS.get(event)
    if indices is None:
        return
    for i in indices:
        if i < len(args) and not _within(args[i], bound.write_roots):
            allowed = ", ".join(str(r) for r in bound.write_roots) or "<nothing>"
            raise PermissionError(
                f"Cannot {event} {args[i]}: outside this run's workspace. Allowed: {allowed}"
            )


@contextlib.contextmanager
def script_bound(
    workspace: Path | None,
    allow_paths: Sequence[Path] = (),
    *,
    confined: bool = False,
    code_roots: Sequence[Path] = (),
) -> Iterator[None]:
    """Hold the code inside this block to *workspace* plus *allow_paths*.

    Reads are additionally permitted from the interpreter's own trees, without
    which no script could import. A ``None`` workspace is a no-op, so callers
    that have no workspace keep exactly the behaviour they had.

    The hook is installed once per process and can never be removed — CPython
    provides no way to — but it is inert outside this block, so a host embedding
    flow keeps its own unrestricted file access.
    """
    if workspace is None:
        yield
        return
    # A script is handed `ctx.workspace` and told that is where its files go, so
    # it has to exist: `Path.write_text` does not create parents, and a first run
    # would otherwise fail on the very thing we told it to do. Created here
    # rather than per-run, so a workflow with no script node still leaves nothing
    # behind.
    workspace.mkdir(parents=True, exist_ok=True)
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_hook)
        _INSTALLED = True
    write_roots = tuple(dict.fromkeys([workspace.resolve(), *(p.resolve() for p in allow_paths)]))
    token = _BOUND.set(
        _Bound(
            write_roots,
            write_roots + _import_roots(),
            tuple(p.resolve() for p in code_roots),
            confined,
        )
    )
    try:
        yield
    finally:
        _BOUND.reset(token)
