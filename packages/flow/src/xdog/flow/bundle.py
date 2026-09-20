"""flow.bundle — package a generated workflow into a self-contained directory.

``xdog-flow generate --portable`` emits a *bundle*: the generated ``workflow.py``
plus vendored copies of the ``ai`` and ``agent`` packages (the only two the
generated module imports at run time, now that flow-internal helpers are inlined),
a ``__main__.py`` that puts the vendored packages on ``sys.path``, a pinned
``requirements.txt`` for the remaining third-party deps (httpx, pydantic), and a
README.

The result runs without ``xdog-ai`` / ``xdog-agent`` installed::

    pip install -r <bundle>/requirements.txt
    python <bundle>            # or: python <bundle> --dry-run  (no auth needed)

With ``offline=True`` the third-party wheels are downloaded into
``_vendor/wheels/`` so the bundle installs with ``pip install --no-index``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

from xdog.flow.codegen import generate
from xdog.flow.models import WorkflowDef

# Packages the generated module imports at run time. flow itself is NOT needed —
# errors/coerce/runtime and the tool registry are inlined into workflow.py.
_VENDORED_PACKAGES = ("xdog.ai", "xdog.agent")

# Third-party runtime dependencies of the vendored packages (ai needs httpx,
# pydantic, and tomlkit; agent needs fastjsonschema for submit_result schema validation and
# pyyaml for skill frontmatter; the
# generated module imports jsonpath-ng for interpolation/conditions).
_THIRD_PARTY = ("httpx", "pydantic", "tomlkit", "fastjsonschema", "jsonpath-ng", "pyyaml")

# Directory names to skip when copying a package's source tree.
_SKIP_DIRS = {"__pycache__", "tests", ".mypy_cache", ".ruff_cache"}


def _package_source_dir(name: str) -> Path:
    """Return the on-disk source directory of an importable package.

    ``importlib.import_module`` rather than ``__import__``: the latter hands back
    the *top-level* package for a dotted name, and the top level here is the
    ``xdog`` namespace, which has no ``__file__`` of its own.
    """
    module = importlib.import_module(name)
    file = getattr(module, "__file__", None)
    if file is None:  # namespace package — no single dir
        raise RuntimeError(f"cannot locate source directory for package {name!r}")
    return Path(file).resolve().parent


def _copy_package(name: str, vendor_dir: Path) -> None:
    """Copy ``<name>``'s source tree into *vendor_dir*, skipping caches and tests.

    The package's licence travels with it.  Vendoring source into a bundle is
    redistribution, and a bundle that carries the code but not the terms is one
    its recipient cannot lawfully pass on.
    """
    src = _package_source_dir(name)
    # A dotted name becomes nested directories, so `xdog.ai` vendors to
    # `_vendor/xdog/ai` and the namespace stays importable — not a directory
    # literally named "xdog.ai", which nothing could import.
    dst = vendor_dir.joinpath(*name.split("."))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(*_SKIP_DIRS, "*.pyc"),
    )
    for licence in _licence_files_for(name, src):
        shutil.copy2(licence, dst / licence.name)


def _licence_files_for(name: str, package_dir: Path) -> list[Path]:
    """Licence files for the importable package *name*, or an empty list.

    Two layouts, and the difference matters: an installed distribution keeps its
    licences in ``*.dist-info/licenses`` — nowhere near the package directory —
    while a source checkout keeps them beside ``pyproject.toml``, two levels up
    from a ``src/<pkg>`` layout.  Walking up from the package directory finds
    them only in the second case, which is exactly the case a developer tests
    in and a pip-installed user is not.

    Missing licences are not fatal — a bundle without them is still runnable —
    but a bundle that vendors source without terms is one its recipient cannot
    lawfully pass on, so try the metadata first.
    """
    # `xdog.ai` ships as `xdog-ai`; deriving it beats packages_distributions(),
    # which maps the shared top-level `xdog` to every distribution at once.
    candidates = [name.replace(".", "-")]
    candidates += importlib.metadata.packages_distributions().get(name.split(".")[0], [])
    for dist in dict.fromkeys(candidates):
        try:
            files = importlib.metadata.files(dist) or []
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - defensive
            continue
        found = [
            resolved
            for entry in files
            if "licenses/" in str(entry).replace("\\", "/")
            and (resolved := Path(str(entry.locate()))).is_file()
        ]
        if found:
            return sorted(found)

    # Source checkout: the licences sit beside pyproject.toml.
    for parent in (package_dir, *package_dir.parents[:3]):
        found = sorted(path for path in parent.glob("LICENSE*") if path.is_file())
        if found:
            return found
    return []


def _pin(dist: str) -> str:
    """Return ``"dist==<installed version>"``, or a bare ``dist`` if not found."""
    try:
        return f"{dist}=={importlib.metadata.version(dist)}"
    except importlib.metadata.PackageNotFoundError:
        return dist


def _slug(name: str) -> str:
    """A PEP 508-safe project name derived from the workflow name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    return cleaned.lower() or "flow-workflow"


def _render_pyproject(wf: WorkflowDef, requirements: list[str]) -> str:
    """The bundle's ``pyproject.toml`` — the canonical dependency declaration.

    With this the bundle is an ordinary uv project: ``uv sync --project <bundle>``
    provisions ``.venv`` (fetching a matching CPython if the host has none), which
    is exactly what the scheduler installer does.  ``requirements.txt`` stays
    alongside it for anyone driving the bundle with plain pip.

    ``package = false`` because a bundle is a runnable directory, not a
    distribution — there is nothing to build or install into the environment.
    """
    deps = ",\n".join(f'    "{r}"' for r in requirements)
    return (
        "[project]\n"
        f'name = "{_slug(wf.name)}"\n'
        'version = "0.0.0"\n'
        f'description = "Generated flow bundle: {wf.name}"\n'
        f'requires-python = ">={sys.version_info.major}.{sys.version_info.minor}"\n'
        "dependencies = [\n"
        f"{deps}\n"
        "]\n"
        "\n"
        "[tool.uv]\n"
        "package = false\n"
    )


def _render_main() -> str:
    """The bundle's ``__main__.py`` — prepend ``_vendor`` to sys.path, run main()."""
    return (
        '"""Entry point: run the bundled workflow with vendored ai/agent on sys.path."""\n'
        "\n"
        "import asyncio\n"
        "import logging\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "_HERE = Path(__file__).resolve().parent\n"
        "# Vendored ai/agent take precedence over any system install.\n"
        "sys.path.insert(0, str(_HERE / '_vendor'))\n"
        "sys.path.insert(0, str(_HERE))\n"
        "\n"
        "from workflow import main  # noqa: E402  (after sys.path setup)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    if '-v' in sys.argv or '--verbose' in sys.argv:\n"
        "        # Surface the P3 node lifecycle events the workflow logs to\n"
        "        # 'flow.generated.events'; off by default (JSON output only).\n"
        "        logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "    asyncio.run(main())\n"
    )


def _render_readme(wf: WorkflowDef, requirements: list[str], offline: bool) -> str:
    """Human-facing run instructions for the bundle."""
    reqs = "\n".join(f"  - {r}" for r in requirements)
    if offline:
        install = (
            "pip install --no-index --find-links _vendor/wheels -r requirements.txt"
        )
    else:
        install = "uv sync            # or: pip install -r requirements.txt"
    return (
        f"# {wf.name} — portable workflow bundle\n"
        "\n"
        "A self-contained copy of this workflow. It vendors the `ai` and `agent`\n"
        "packages under `_vendor/`, so it runs without `xdog-ai` / `xdog-agent`\n"
        "installed.\n"
        "\n"
        "## Layout\n"
        "\n"
        "- `workflow.py` — the generated workflow module.\n"
        "- `__main__.py` — entry point (puts `_vendor/` on `sys.path`, runs `main()`).\n"
        "- `_vendor/ai`, `_vendor/agent` — vendored package sources.\n"
        "- `pyproject.toml` — the same dependencies as a uv project (`uv sync`).\n"
        "- `requirements.txt` — pinned third-party runtime dependencies (pip).\n"
        + ("- `_vendor/wheels/` — downloaded wheels for offline install.\n" if offline else "")
        + "\n"
        "## Run\n"
        "\n"
        "```sh\n"
        f"{install}\n"
        "python .              # real run (needs provider auth)\n"
        "python . -v           # also print node lifecycle events (with port previews)\n"
        "```\n"
        "\n"
        "## Run-time overrides (env vars)\n"
        "\n"
        "```sh\n"
        "# Override $in inputs (a JSON object, merged per-key) and/or the provider:\n"
        "FLOW_INPUTS='{\"days\": 2}' FLOW_PROVIDER=openai python .\n"
        "FLOW_MAX_TOKENS=100000 python .   # abort if agent tokens exceed the budget\n"
        "```\n"
        "\n"
        "## Licence\n"
        "\n"
        "This bundle was generated by flow (AGPL-3.0-or-later). Parts of flow's\n"
        "runtime are inlined into `workflow.py`, and are covered by the flow\n"
        "Generated Output Exception — you may convey this bundle under terms of\n"
        "your choice. The vendored packages under `_vendor/` keep their own\n"
        "licences; see the LICENSE files beside them.\n"
        "\n"
        "## Requirements\n"
        "\n"
        f"{reqs}\n"
    )


def _download_wheels(requirements: list[str], wheels_dir: Path) -> None:
    """Download the third-party wheels into *wheels_dir* for offline install.

    Uses ``python -m pip download``. Raises a clear error if pip is unavailable
    (e.g. a uv-managed venv ships no pip) — run ``--offline`` from an environment
    that has pip, or drop ``--offline`` and let the target ``pip install`` fetch
    the (few, pure-Python) deps at install time.
    """
    if importlib.util.find_spec("pip") is None:
        raise RuntimeError(
            "--offline needs pip in the current environment to download wheels, "
            "but pip is not importable here. Re-run --offline from an environment "
            "with pip installed, or omit --offline (the bundle's requirements.txt "
            "still pins httpx/pydantic for a normal `pip install`)."
        )
    wheels_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--dest", str(wheels_dir), *requirements],
        check=True,
    )


def _copy_workflow_modules(wf: WorkflowDef, base_dir: Path, out_dir: Path) -> list[str]:
    """Copy the workflow directory's own ``*.py`` modules into the bundle root.

    A ``run: "module:func"`` script node compiles to a real import
    (``from module import func``), and the interpreter satisfies it by putting the
    workflow's own directory on ``sys.path``.  A bundle runs from somewhere else
    entirely, so it has to carry that directory with it or the generated module
    fails at import time.

    Copying the whole sibling set rather than just the named modules is
    deliberate: those modules routinely reach for peers that flow cannot see —
    a helper import, or a subprocess spawned as ``Path(__file__).parent /
    "other.py"`` — and a bundle missing one of them breaks only at run time, on a
    timer, at 4am.
    """
    if not any(n.type == "script" and n.run for n in wf.nodes) and not wf.tool_refs:
        return []
    copied: list[str] = []
    for path in sorted(base_dir.glob("*.py")):
        if path.name == "workflow.py":  # never shadow the generated module
            continue
        shutil.copy2(path, out_dir / path.name)
        copied.append(path.name)
    return copied


def build_bundle(
    wf: WorkflowDef, out_dir: Path, *, base_dir: Path | None = None, offline: bool = False
) -> Path:
    """Write a self-contained bundle for *wf* into *out_dir*; return *out_dir*.

    Overwrites *out_dir* if it exists. With *offline* the third-party wheels are
    downloaded into ``_vendor/wheels/`` for a no-network install.  *base_dir* is
    the workflow file's own directory; its ``*.py`` modules travel with the bundle
    so ``run:`` script references still resolve (see
    :func:`_copy_workflow_modules`).
    """
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    vendor_dir = out_dir / "_vendor"
    vendor_dir.mkdir(parents=True)

    # 1) The generated workflow module.
    (out_dir / "workflow.py").write_text(generate(wf), encoding="utf-8")

    # 1b) The workflow's own sibling modules, for `run:` script references.
    if base_dir is not None:
        _copy_workflow_modules(wf, base_dir.resolve(), out_dir)
        # A workflow's own skills travel with it, like its sibling modules. Left
        # behind, an installed bundle would silently render a shorter system
        # prompt than the same workflow run from its directory.
        _skills = base_dir.resolve() / "skills"
        if _skills.is_dir():
            shutil.copytree(_skills, out_dir / "skills", dirs_exist_ok=True)

    # 2) Vendored package sources.  ai/agent are vendored ONLY when the generated
    # module imports them — i.e. the workflow has an SDK agent node (or a tool
    # manifest).  A pure-CLI/script workflow drops them entirely.  A subflow-using
    # workflow additionally vendors ``flow`` (its module calls execute() on the
    # embedded child).
    _needs_sdk = any(n.type == "agent" and n.backend is None for n in wf.nodes) or bool(wf.tool_refs)
    vendored: tuple[str, ...] = _VENDORED_PACKAGES if _needs_sdk else ()
    if any(n.type == "subflow" for n in wf.nodes):
        vendored = (*vendored, "xdog.flow")
    for name in vendored:
        _copy_package(name, vendor_dir)

    # 3) Entry point.
    (out_dir / "__main__.py").write_text(_render_main(), encoding="utf-8")

    # 4) Pinned requirements.  jsonpath-ng is always needed (interpolation); the
    # ai/agent third-party deps (httpx/pydantic/fastjsonschema) only when SDK.
    _third_party = _THIRD_PARTY if _needs_sdk else ("jsonpath-ng",)
    requirements = [_pin(d) for d in _third_party]
    (out_dir / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
    (out_dir / "pyproject.toml").write_text(_render_pyproject(wf, requirements), encoding="utf-8")

    # 5) Optional offline wheels.
    if offline:
        _download_wheels(requirements, vendor_dir / "wheels")

    # 6) README.
    (out_dir / "README.md").write_text(_render_readme(wf, requirements, offline), encoding="utf-8")

    return out_dir
