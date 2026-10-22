"""Production-only Goal/host boundaries, including the original owner fences.

AST checks intentionally inspect imports and object access, not arbitrary source
strings: documentation, protocol labels and negative test fixtures are not code.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
GOAL = ROOT / "packages/skills/src/xdog/skills/goal"
HOSTS = tuple(ROOT / f"packages/{name}/src/xdog/{name}" for name in ("coding", "claw"))


def imports(source: str):
    """Actual imports and literal dynamic imports, never prose mentioning modules."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
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


def violations(roots, prefixes):
    result = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for line, module in imports(path.read_text()):
                if any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes):
                    result.append(f"{path.relative_to(ROOT)}:{line}: {module}")
    return result


def test_import_scanner_ignores_documentation_and_unexecuted_string_examples():
    source = '''
"""Do not import xdog.skills.goal or xdog.claw here."""
example = "from xdog.coding import main"
# import xdog.claw
from xdog import coding
import importlib
importlib.import_module("xdog.claw.core")
'''
    assert {name for _, name in imports(source) if name.startswith("xdog.")} == {
        "xdog.coding", "xdog.claw.core",
    }


def test_hosts_have_no_goal_source_import_or_goal_adapter():
    found = violations(HOSTS, ("xdog.skills.goal",))
    retired = [str(path.relative_to(ROOT)) for root in HOSTS for path in root.rglob("goal_adapter.py")]
    assert not found and not retired, (
        "Goal business must live in the capability package:\n" + "\n".join(found + retired)
    )


def test_goal_production_code_has_no_host_imports():
    found = violations((GOAL,), ("xdog.coding", "xdog.claw"))
    assert not found, "Goal may consume public generic ports, not host implementations:\n" + "\n".join(found)


def test_goal_cannot_reach_back_through_private_session_or_application_objects():
    found = []
    forbidden = {"session", "runtime", "orchestrator", "controller", "group_runtime", "session_manager"}
    for path in sorted(GOAL.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            port = (isinstance(owner, ast.Name) and owner.id in {"host", "parent"}) or (
                isinstance(owner, ast.Attribute) and owner.attr in {"host", "_host", "parent", "_parent"}
            )
            if port and (node.attr.startswith("_") or node.attr in forbidden):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {ast.unparse(node)}")
    assert not found, "Public ports must not hide a reverse raw-host reference:\n" + "\n".join(found)


def test_goal_provider_is_metadata_registered_and_inert_on_import(tmp_path):
    config = tomllib.loads((ROOT / "packages/skills/pyproject.toml").read_text())
    entries = config["project"].get("entry-points", {}).get("xdog.capabilities", {})
    assert "goal" in entries, "Installed metadata, not a host-specific find_spec/import branch, supplies Goal"
    script = r'''
import importlib.metadata, pathlib, sqlite3, sys, threading
class NoHost:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in ('xdog.coding', 'xdog.claw')):
            raise AssertionError('Goal imported a host: ' + fullname)
sys.meta_path.insert(0, NoHost())
def reject(*args, **kwargs):
    raise AssertionError('Provider import must not open storage/start execution')
sqlite3.connect = reject
threading.Thread.start = reject
ep = importlib.metadata.EntryPoint(name='goal', value=sys.argv[1], group='xdog.capabilities')
provider = ep.load()
assert provider.name == 'goal'
assert set(provider.scopes) == {'session', 'group'}
assert 'goal' in provider.tool_names
assert callable(provider.open)
assert not list(pathlib.Path.cwd().iterdir())
'''
    result = subprocess.run([sys.executable, "-c", script, entries["goal"]], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", ["xdog.coding.core.sdk", "xdog.claw.core.runtime.bootstrap"])
def test_ordinary_host_import_does_not_load_goal_business(module, tmp_path):
    script = r'''
import importlib, sys
class NoBusiness:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'xdog.skills.goal' or fullname.startswith('xdog.skills.goal.'):
            raise AssertionError('Ordinary host import loaded Goal business: ' + fullname)
sys.meta_path.insert(0, NoBusiness())
importlib.import_module(sys.argv[1])
'''
    result = subprocess.run([sys.executable, "-c", script, module], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("changed", ["owner", "workspace"])
async def test_goal_store_refuses_cross_owner_rebind_byte_preserving(tmp_path, changed):
    from xdog.skills.goal.models import Goal, Owner
    from xdog.skills.goal.store import GoalStore

    path = tmp_path / "goals/journal.sqlite3"
    owner = Owner("coding:namespace:original-session:original-branch", tmp_path)
    store = await GoalStore.open(path, owner)
    try:
        await store.put(Goal("goal-one", "Outcome", "Original owner", ("Reviewed",)), expected_version=0)
    finally:
        await store.close()
    before = path.read_bytes()
    impostor = Owner(owner.key + ":different" if changed == "owner" else owner.key,
                     tmp_path if changed == "owner" else tmp_path / "different-workspace")
    with pytest.raises(ValueError, match="owner"):
        await GoalStore.open(path, impostor)
    assert path.read_bytes() == before
    reopened = await GoalStore.open(path, owner)
    try:
        assert (await reopened.get("goal-one")).outcome == "Original owner"
    finally:
        await reopened.close()


async def test_goal_service_rejects_live_owner_drift_without_moving_the_goal(tmp_path):
    from xdog.skills.goal.models import Goal, Owner
    from xdog.skills.goal.service import GoalService
    from xdog.skills.goal.store import GoalStore

    original = Owner("group:namespace:original-group", tmp_path)
    store = await GoalStore.open(tmp_path / "goals/journal.sqlite3", original)
    host = SimpleNamespace(owner=original)
    service = GoalService(store, valid=lambda: host.owner == store.owner)
    try:
        await store.put(Goal("goal-one", "Outcome", "Do not transfer", ("Reviewed",)), expected_version=0)
        host.owner = Owner("group:namespace:other-group", tmp_path)
        with pytest.raises(ValueError, match="owner"):
            await service.list()
        host.owner = original
        assert [goal.id for goal in await service.list()] == ["goal-one"]
    finally:
        await service.close()


async def test_feature_source_labels_do_not_supply_user_authority():
    from xdog.skills.goal.host import OperationContext, require_user

    for label in (None, {"user_authorized": True, "source": "user"}, SimpleNamespace(user_authorized=True),
                  OperationContext(False)):
        with pytest.raises(ValueError):
            require_user(label)
    with pytest.raises(ValueError):
        OperationContext(1)
    require_user(OperationContext(True))


def test_goal_json_payload_is_data_not_a_python_provider():
    from xdog.skills.goal.codec import decode_goal, encode_goal
    from xdog.skills.goal.models import Goal

    payload = 'xdog.skills.goal.runtime:provider; __import__("xdog.claw")'
    goal = Goal("goal-one", payload, payload, (payload,))
    encoded = encode_goal(goal)
    assert payload in json.loads(encoded).values()
    assert decode_goal(encoded) == goal


def test_hosts_do_not_branch_execution_on_named_capability_bindings():
    found = []
    for root in HOSTS:
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.If, ast.IfExp, ast.While)):
                    continue
                for expression in ast.walk(node.test):
                    if not isinstance(expression, ast.Call) or not expression.args:
                        continue
                    function = expression.func
                    if not isinstance(function, ast.Attribute) or function.attr != "get":
                        continue
                    if "capabilit" not in ast.unparse(function.value).lower():
                        continue
                    key = expression.args[0]
                    if isinstance(key, ast.Constant) and key.value in {"goal", "eyes"}:
                        found.append(f"{path.relative_to(ROOT)}:{expression.lineno}: {ast.unparse(expression)}")
    assert not found, "Feature names are diagnostic IDs, not host execution policy:\n" + "\n".join(found)


@pytest.mark.parametrize("scope", ["session", "group"])
async def test_goal_runtime_preserves_storage_layout_and_actual_owner_scope(tmp_path, scope):
    import hashlib

    from xdog.skills.goal.host import OperationContext
    from xdog.skills.goal.models import Owner
    from xdog.skills.goal.runtime import provider

    subscriptions = []

    def subscribe(callback):
        subscriptions.append(callback)
        return lambda: subscriptions.remove(callback)

    host = SimpleNamespace(
        scope=scope, owner_prefix="coding" if scope == "session" else "claw",
        identity="original-owner", namespace="original-inbox", branch="original-branch",
        workspace=tmp_path, storage_root=tmp_path / "data", valid=lambda: True, accepting=lambda: True,
        subscribe_events=subscribe, acp_target=AsyncMock(return_value=None),
    )
    suffix = ":original-branch" if scope == "session" else ""
    owner = Owner(f"{host.owner_prefix}:original-inbox:original-owner{suffix}", tmp_path)
    directory = host.storage_root / "goals"
    if scope == "session":
        directory /= hashlib.sha256(owner.key.encode()).hexdigest()
    runtime = await provider.open(host)
    try:
        assert runtime.owner == owner and runtime.service.store.path == directory / "registry.sqlite3"
        assert await runtime.service.list() == () and len(subscriptions) == 1
        # No parent/children port is available: installed startup cannot create a
        # worker, poll a model, or claim user authorization just by being present.
        assert runtime.tools == ()  # No tool can borrow authorization from a future parent.
        with pytest.raises(ValueError, match="user operation"):
            await runtime.service.create("Not delegated", "Do not start", ("Safe",),
                                         context=OperationContext(False))
        assert await runtime.service.list() == ()
        goal = await runtime.service.create(
            "Delegated", "Original owner", ("Reviewed",), context=OperationContext(True),
        )
        host.branch = "other-branch"
        if scope == "session":
            with pytest.raises(ValueError, match="owner"):
                await runtime.service.list()
        else:
            assert [item.id for item in await runtime.service.list()] == [goal.id]
        host.branch = "original-branch"
        host.identity = "other-owner"
        with pytest.raises(ValueError, match="owner"):
            await runtime.service.list()
        host.identity = "original-owner"
        assert [item.id for item in await runtime.service.list()] == [goal.id]
    finally:
        await runtime.aclose()
    assert subscriptions == []
    # The journal lease actually settled; a fresh feature instance can re-open
    # the original owner without replaying or manufacturing a new owner scope.
    reopened = await provider.open(host)
    try:
        assert [item.id for item in await reopened.service.list()] == [goal.id]
    finally:
        await reopened.aclose()


async def test_group_goal_legacy_data_is_preserved_and_reported_without_execution(tmp_path):
    from unittest.mock import AsyncMock

    from xdog.skills.goal.runtime import provider

    legacy = tmp_path / "goals.json"
    original = b'{"legacy": "leave exactly as found"}\n'
    legacy.write_bytes(original)
    host = SimpleNamespace(
        scope="group", owner_prefix="claw", identity="original-group", namespace="original-inbox",
        branch="original-branch", workspace=tmp_path, storage_root=tmp_path, valid=lambda: True, accepting=lambda: True,
        subscribe_events=lambda callback: lambda: None, acp_target=AsyncMock(return_value=None),
    )
    runtime = await provider.open(host)
    try:
        assert runtime._legacy_paths == (str(legacy),)
        assert await runtime.service.current() is None and runtime.tools == ()
        assert legacy.read_bytes() == original
        assert not (tmp_path / "goals/journal.sqlite3").exists()
    finally:
        await runtime.aclose()


async def test_goal_close_settles_observer_display_and_store_after_detach_failure():
    import asyncio

    from xdog.skills.goal.runtime import GoalRuntime

    runtime = GoalRuntime(SimpleNamespace())
    order = []
    entered, release = asyncio.Event(), asyncio.Event()

    def broken_unsubscribe():
        order.append("unsubscribe")
        raise ValueError("unsubscribe failed")

    class Observer:
        async def aclose(self, *, suspend=True):
            assert suspend is False
            entered.set()
            await release.wait()
            order.append("observer joined")

    class Display:
        async def aclose(self):
            order.append("display closed")
            raise RuntimeError("display close failed")

    async def close_store():
        assert "observer joined" in order
        order.append("store")

    runtime._unsubscribe = broken_unsubscribe
    runtime._remove_commands = lambda: order.append("command detached")
    runtime._observer = Observer()
    runtime._display = Display()
    runtime.service = SimpleNamespace(close=close_store)
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.wait_for(entered.wait(), 2)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done() and "store" not in order
    release.set()
    with pytest.raises(BaseExceptionGroup) as failure:
        await closing
    def messages(error):
        if isinstance(error, BaseExceptionGroup):
            return [text for item in error.exceptions for text in messages(item)]
        return [str(error)]
    assert set(messages(failure.value)) == {"unsubscribe failed", "display close failed"}
    assert {"command detached", "display closed", "store"} <= set(order)
    with pytest.raises(BaseExceptionGroup):
        await runtime.aclose()
    assert order.count("store") == 1 and runtime._parent is None
