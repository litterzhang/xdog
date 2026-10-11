"""Every third-party import must be declared or guarded.

`xdog-coding` 0.57.7 could not start: it imports `click` and `pydantic` and
declared neither, so it only worked when something else in the environment
happened to pull them in. On a machine with just `pip install xdog-coding` the
command died with ModuleNotFoundError before printing anything.

Nothing catches this by running the code, because the development venv has
every package's dependencies installed at once — the one environment where the
bug cannot reproduce is the one the tests run in. So check the metadata against
the imports directly.

`claw` was missing `httpx` and `qrcode` outright and bare-imported `croniter`
from an optional group; `site` was missing `python-frontmatter` and
`markupsafe`. Three packages, one mistake, no failing test between them.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
_PACKAGES = ("ai", "agent", "flow", "tui", "site", "claw", "coding")

#: Distribution name -> the name you import it by, where they differ.
_IMPORT_NAME = {
    "pyyaml": "yaml",
    "python_frontmatter": "frontmatter",
    "pillow": "PIL",
}

#: Imports that may go undeclared, with the reason. Empty is the right state:
#: the one entry this had was pytest inside `examples_gen`, an orphan that
#: shipped in the wheel and that nothing imported — deleted rather than excused.
_ALLOWED: set[tuple[str, str]] = set()


def _declared(pyproject: dict) -> set[str]:
    project = pyproject["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    names = set()
    for spec in specs:
        dist = re.split(r"[><=!~\[ ;]", spec)[0].strip().lower().replace("-", "_")
        names.add(_IMPORT_NAME.get(dist, dist))
    return names


def _required_only(pyproject: dict) -> set[str]:
    names = set()
    for spec in pyproject["project"].get("dependencies", []):
        dist = re.split(r"[><=!~\[ ;]", spec)[0].strip().lower().replace("-", "_")
        names.add(_IMPORT_NAME.get(dist, dist))
    return names


def _imports(source_root: pathlib.Path) -> dict[str, list[tuple[pathlib.Path, bool]]]:
    """Top-level module name -> [(file, guarded)]. Guarded means inside a `try`."""
    found: dict[str, list[tuple[pathlib.Path, bool]]] = {}
    for path in sorted(source_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - generated samples may be partial
            continue
        guarded_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for child in ast.walk(node):
                    guarded_nodes.add(id(child))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                found.setdefault(name, []).append((path, id(node) in guarded_nodes))
    return found


@pytest.mark.parametrize("package", _PACKAGES)
def test_every_third_party_import_is_declared(package: str) -> None:
    root = _REPO / "packages" / package
    declared = _declared(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")))

    undeclared = []
    for module, sites in _imports(root / "src").items():
        if module in sys.stdlib_module_names or module == "xdog" or module in declared:
            continue
        if (package, module) in _ALLOWED:
            continue
        # This optional transport runs under fixed /usr/bin/python3 -I, not
        # the package interpreter. GI/libsecret are externally installed OS
        # prerequisites, not pip dependencies; require defensive imports ONLY
        # in that exact standalone worker, never exempt application imports.
        if (package == 'agent' and module == 'gi' and all(
                path == root / 'src/xdog/agent/mcp/native_store_worker.py' and guarded
                for path, guarded in sites)):
            continue
        undeclared.append(f"{module} (e.g. {sites[0][0].relative_to(root)})")

    assert not undeclared, (
        f"xdog-{package} imports without declaring: {sorted(undeclared)}. "
        "It works here only because another package pulled them in."
    )


@pytest.mark.parametrize("package", _PACKAGES)
def test_optional_dependencies_are_imported_defensively(package: str) -> None:
    """An extra you did not install must degrade, not crash.

    `croniter` sat in claw's `search` extra and was imported bare at the top of
    the task scheduler, so a default install raised ModuleNotFoundError on a
    core path — the extra bought nothing but a way to break.
    """
    root = _REPO / "packages" / package
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    optional_only = _declared(pyproject) - _required_only(pyproject)

    unguarded = [
        f"{module} in {path.relative_to(root)}"
        for module, sites in _imports(root / "src").items()
        if module in optional_only
        for path, guarded in sites
        if not guarded
    ]

    assert not unguarded, (
        f"xdog-{package} imports optional dependencies unconditionally: {sorted(unguarded)}. "
        "Either move them to `dependencies`, or wrap the import in try/except ImportError."
    )
