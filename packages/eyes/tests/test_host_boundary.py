"""Eyes remains an independent feature behind injected stdlib-only host ports."""
from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EYES = ROOT / "packages/eyes/src/xdog/eyes"
HOSTS = tuple(ROOT / f"packages/{name}/src/xdog/{name}" for name in ("coding", "claw"))


def imports(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in {"import_module", "find_spec", "__import__"}:
                value = node.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield node.lineno, value.value


def test_eyes_production_imports_no_other_xdog_runtime_package():
    found = []
    for path in sorted(EYES.rglob("*.py")):
        for line, module in imports(path.read_text()):
            if module.startswith("xdog.") and module != "xdog.eyes" and not module.startswith("xdog.eyes."):
                found.append(f"{path.relative_to(ROOT)}:{line}: {module}")
    assert not found, "Eyes must remain installable without other XDOG runtimes:\n" + "\n".join(found)


def test_hosts_use_installed_metadata_not_eyes_source_imports_or_observer_modules():
    found = []
    for root in HOSTS:
        for path in sorted(root.rglob("*.py")):
            for line, module in imports(path.read_text()):
                if module == "xdog.eyes" or module.startswith("xdog.eyes."):
                    found.append(f"{path.relative_to(ROOT)}:{line}: {module}")
    retired = ROOT / "packages/claw/src/xdog/claw/core/observers"
    found.extend(str(path.relative_to(ROOT)) for path in sorted(retired.rglob("*.py")))
    tool = ROOT / "packages/claw/src/xdog/claw/core/tools/tool_eyes.py"
    if tool.exists():
        found.append(str(tool.relative_to(ROOT)))
    assert not found, "Observer business must move to Eyes, without host compatibility imports:\n" + "\n".join(found)


def test_eyes_distribution_has_no_xdog_runtime_dependency():
    config = tomllib.loads((ROOT / "packages/eyes/pyproject.toml").read_text())
    dependencies = list(config["project"]["dependencies"])
    for values in config["project"].get("optional-dependencies", {}).values():
        dependencies.extend(values)
    assert not [value for value in dependencies if value.lower().replace("_", "-").startswith("xdog-")]


def test_eyes_all_production_modules_import_without_harness_or_storage(tmp_path):
    script = r'''
import importlib, pathlib, pkgutil, sqlite3, sys, threading
class NoRuntime:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('xdog.') and fullname != 'xdog.eyes' and not fullname.startswith('xdog.eyes.'):
            raise AssertionError('Eyes imported another XDOG runtime: ' + fullname)
sys.meta_path.insert(0, NoRuntime())
def reject(*args, **kwargs):
    raise AssertionError('Module import must not open storage or start workers')
sqlite3.connect = reject
threading.Thread.start = reject
import xdog.eyes
for module in pkgutil.walk_packages(xdog.eyes.__path__, 'xdog.eyes.'):
    if module.name.rsplit('.', 1)[-1] != '__main__':
        importlib.import_module(module.name)
assert not list(pathlib.Path.cwd().iterdir())
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_eyes_provider_is_application_only_and_entrypoint_import_is_inert(tmp_path):
    config = tomllib.loads((ROOT / "packages/eyes/pyproject.toml").read_text())
    entries = config["project"].get("entry-points", {}).get("xdog.capabilities", {})
    assert "eyes" in entries, "Tool capability must be supplied by installed metadata"
    script = r'''
import importlib.metadata, pathlib, sqlite3, sys, threading
class NoRuntime:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('xdog.') and fullname != 'xdog.eyes' and not fullname.startswith('xdog.eyes.'):
            raise AssertionError('Eyes provider imported another XDOG runtime: ' + fullname)
sys.meta_path.insert(0, NoRuntime())
def reject(*args, **kwargs):
    raise AssertionError('Provider import is discovery, not activation')
sqlite3.connect = reject
threading.Thread.start = reject
provider = importlib.metadata.EntryPoint(name='eyes', value=sys.argv[1], group='xdog.capabilities').load()
assert provider.name == 'eyes'
assert set(provider.scopes) == {'application'}
assert 'eyes' in provider.tool_names and callable(provider.open)
assert provider.skill_names == ('eyes',)
assert not getattr(provider, 'default_skills', ())
assert not list(pathlib.Path.cwd().iterdir())
'''
    result = subprocess.run([sys.executable, "-c", script, entries["eyes"]], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
