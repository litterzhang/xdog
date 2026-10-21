"""Eyes remains an independent feature behind injected stdlib-only host ports."""
from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert "eyes" in entries, "Application capability must be supplied by installed metadata"
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
assert not list(pathlib.Path.cwd().iterdir())
'''
    result = subprocess.run([sys.executable, "-c", script, entries["eyes"]], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


class MemoryDatabase:
    """Injected IO only; the real feature creates/validates its own domain schema."""
    def __init__(self, path, schema):
        self.path = path
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(schema)

    async def disk(self, operation):
        with self.db:
            return operation(self.db)

    async def close(self):
        self.db.close()


class JournalHost:
    def __init__(self, root):
        self.root = root
        self.namespace = "a" * 32
        self.database = None
        self.formats = []

    async def open_database(self, path, *, format, schema, validate, recover):
        self.formats.append(format)
        self.database = MemoryDatabase(path, schema)
        validate(self.database.db)
        recover(self.database.db)
        return self.database

    async def input_namespace(self, group, cwd):
        if group != "original-group" or cwd != self.root:
            raise ValueError("Wrong input owner/workspace")
        return self.namespace

    def validate_reply(self, group, cwd, value):
        reply = json.loads(value)
        if reply["group"] != group or reply["workspace"] != str(cwd):
            raise ValueError("Wrong original reply owner/workspace")
        return value

    def validate_message(self, value):
        assert isinstance(value, dict) and isinstance(value["id"], str)
        return json.loads(json.dumps(value))

    async def input_get(self, group, cwd, identifier):
        return None

    async def input_attempts(self, group, cwd, identifier):
        return ()


async def test_observer_journal_uses_injected_io_and_fences_group_workspace_and_original_reply(tmp_path):
    from xdog.eyes.hosting.journal import ObserverJournal

    host = JournalHost(tmp_path)
    journal = await ObserverJournal.open(tmp_path / "observers/journal.sqlite3", host)
    reply = json.dumps({"group": "original-group", "workspace": str(tmp_path), "peer": "original-peer"})
    try:
        record = await journal.create("original-group", tmp_path, "check", {"version": 2}, reply=reply)
        assert host.formats == ["xdog.claw.observers.v3"], "Keep supported v3 bytes; this is not a format migration"
        assert record.reply == reply and record.inbox_id == host.namespace
        assert await journal.get("other-group", tmp_path, record.id) is None
        assert await journal.get("original-group", tmp_path / "other-workspace", record.id) is None
        assert await journal.get("original-group", tmp_path, record.id) == record
        with pytest.raises(ValueError):
            await journal.create("original-group", tmp_path, "other-reply", {},
                                 reply=json.dumps({"group": "other-group", "workspace": str(tmp_path)}))
        for changed in (replace(record, group_id="other-group"), replace(record, cwd=tmp_path / "other-workspace")):
            with pytest.raises(ValueError):
                await journal.save(changed, {"cursor": "must-not-transfer"})
        assert (await journal.get("original-group", tmp_path, record.id)).state == {}
    finally:
        await journal.close()


async def test_namespace_change_cannot_commit_a_new_observer_trigger(tmp_path):
    from xdog.eyes.hosting.journal import ObserverJournal
    from xdog.eyes.hosting.models import ObserverStopped

    host = JournalHost(tmp_path)
    journal = await ObserverJournal.open(tmp_path / "observers/journal.sqlite3", host)
    try:
        record = await journal.create("original-group", tmp_path, "check", {})
        host.namespace = "b" * 32
        with pytest.raises((ValueError, ObserverStopped)):
            await journal.save(record, {"cursor": "untrusted-new-namespace"}, ({"id": "original-message"},))
        current = await journal.get("original-group", tmp_path, record.id)
        assert current.state == {} and current.recovery == "needs_review"
        rows = await host.database.disk(lambda db: tuple(db.execute("SELECT * FROM outbox")))
        assert rows == (), "A changed inbox namespace must not acquire the saved observer's outbox"
    finally:
        await journal.close()


@pytest.mark.parametrize("changed", ["group", "endpoint", "workspace"])
async def test_saved_observer_definition_cannot_retarget_a_host_owner(tmp_path, changed):
    from xdog.eyes.hosting.manager import ManagedObservers
    from xdog.eyes.hosting.models import ObserverRecord
    from xdog.eyes.loader import definition_data

    endpoint = tmp_path / "private-agent.sock"
    host = SimpleNamespace(endpoint=endpoint, workspace=lambda group: tmp_path)
    manager = ManagedObservers(host)
    original = manager.definition("original-group", "check", {"type": "scheduler", "schedule": {"every_seconds": 60}},
                                  'untrusted text: xdog.claw.runtime:provider')
    data = definition_data(original)
    if changed == "group":
        data["handlers"][0]["session"] = "group:other-group"
    elif changed == "endpoint":
        data["handlers"][0]["connection"]["path"] = str(tmp_path / "other-agent.sock")
    else:
        host.workspace = lambda group: tmp_path / "other-workspace"
    record = ObserverRecord("eyes-one", "original-group", tmp_path, "check", data, 1, True, "managed", {}, "")
    with pytest.raises(ValueError):
        manager._definition(record)
