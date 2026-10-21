"""Installed capabilities are inert until one real owner starts and joins them.

Only fake installed metadata/providers are used: no optional business package,
model, observer, or application runtime is needed for these lifecycle tests.
"""
from __future__ import annotations

import asyncio
import importlib
from dataclasses import FrozenInstanceError, dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest


@dataclass
class Binding:
    tools: tuple[Any, ...] = ()
    parents: list[Any] = field(default_factory=list)
    closes: int = 0
    flushes: int = 0

    def attach(self, parent):
        self.parents.append(parent)

    async def flush(self):
        self.flushes += 1

    async def aclose(self):
        self.closes += 1


@dataclass
class Provider:
    name: str
    binding: Binding = field(default_factory=Binding)
    scopes: tuple[str, ...] = ("session", "group")
    tool_names: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    hosts: list[Any] = field(default_factory=list)

    async def open(self, host):
        self.hosts.append(host)
        return self.binding


@dataclass
class Entry:
    name: str
    provider: Provider
    loads: int = 0

    def load(self):
        self.loads += 1
        return self.provider


@pytest.fixture
def installed(monkeypatch):
    runtime = importlib.import_module("xdog.agent.capabilities.runtime")

    def install(*providers):
        entries = tuple(Entry(provider.name, provider) for provider in providers)

        def entry_points(*, group):
            assert group == "xdog.capabilities"
            return entries

        monkeypatch.setattr(runtime, "entry_points", entry_points)
        return runtime, entries

    return install


async def checkpoint():
    # Drain ready callbacks, not a product timeout or a timing race against IO.
    for _ in range(10):
        await asyncio.sleep(0)


async def settle(*tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_discovery_only_loads_installed_metadata_and_filters_scope(installed, tmp_path):
    session = Provider("session-only", scopes=("session",), tool_names=("inspect",), requirements=("children",))
    application = Provider("application-only", scopes=("application",))
    runtime, entries = installed(session, application)
    assert runtime.discover("group") == ()
    assert runtime.discover("session") == (session,)
    assert runtime.discover("application") == (application,)
    capabilities = runtime.CapabilitySet("session")
    assert capabilities.tools == () and capabilities.get("session-only") is None
    assert capabilities.declared_tools == frozenset({"inspect"})
    assert capabilities.requirements == frozenset({"children"})
    assert not session.hosts and not application.hosts
    assert all(entry.loads > 0 for entry in entries)
    assert list(tmp_path.iterdir()) == []


def test_duplicate_installed_identity_is_rejected_before_open(installed):
    first, second = Provider("same"), Provider("same")
    runtime, _ = installed(first, second)
    with pytest.raises(ValueError):
        runtime.CapabilitySet("session")
    assert not first.hosts and not second.hosts


def test_entry_name_cannot_impersonate_another_provider(installed):
    provider = Provider("actual")
    runtime, entries = installed(provider)
    entries[0].name = "claimed"
    with pytest.raises(ValueError):
        runtime.discover("session")
    assert not provider.hosts


async def test_no_installed_capability_is_a_noop_even_with_opaque_host(installed):
    runtime, _ = installed()
    capabilities = runtime.CapabilitySet("session")
    await capabilities.start(object())
    assert capabilities.tools == () and capabilities.requirements == frozenset()
    await capabilities.flush()
    await capabilities.aclose()


async def test_start_and_close_are_idempotent_for_same_owner(installed):
    provider = Provider("sample")
    runtime, _ = installed(provider)
    capabilities, host = runtime.CapabilitySet("session"), object()
    try:
        await capabilities.start(host)
        await capabilities.start(host)
        assert provider.hosts == [host]
        assert capabilities.get("sample") is provider.binding
        await capabilities.flush()
        assert provider.binding.flushes == 1
    finally:
        await capabilities.aclose()
    await capabilities.aclose()
    assert provider.binding.closes == 1
    assert capabilities.tools == () and capabilities.get("sample") is None
    with pytest.raises(RuntimeError):
        await capabilities.start(host)


async def test_same_owner_concurrent_start_opens_once(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(Provider):
        async def open(self, host):
            self.hosts.append(host)
            entered.set()
            await release.wait()
            return self.binding

    provider = Slow("sample")
    runtime, _ = installed(provider)
    capabilities, host = runtime.CapabilitySet("session"), object()
    first = asyncio.create_task(capabilities.start(host))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(capabilities.start(host))
        await checkpoint()
        assert provider.hosts == [host], "Concurrent starts must join the same owner startup"
        release.set()
        await asyncio.gather(first, second)
        assert capabilities.get("sample") is provider.binding
    finally:
        release.set()
        await settle(*(task for task in (first, second) if task is not None))
        await capabilities.aclose()


async def test_one_set_cannot_be_rebound_to_a_different_host(installed):
    provider = Provider("sample")
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet("group")
    first, second = object(), object()
    try:
        await capabilities.start(first)
        with pytest.raises((ValueError, RuntimeError)):
            await capabilities.start(second)
        assert provider.hosts == [first]
    finally:
        await capabilities.aclose()


async def test_failed_start_rolls_back_all_already_opened_bindings(installed):
    class Broken(Provider):
        async def open(self, host):
            self.hosts.append(host)
            raise ValueError("provider-open-failed")

    first, broken = Provider("a-first"), Broken("z-broken")
    runtime, _ = installed(first, broken)
    capabilities = runtime.CapabilitySet("session")
    try:
        with pytest.raises(ValueError, match="provider-open-failed"):
            await capabilities.start(object())
        assert first.binding.closes == 1, "Failed startup must not leave an acquired owner lease live"
        assert capabilities.get("a-first") is None
        assert capabilities.tools == ()
    finally:
        await capabilities.aclose()
    assert first.binding.closes == 1


async def test_cancelled_start_waiter_detaches_but_set_keeps_actual_owner(installed):
    entered = asyncio.Event()
    provider_cleanup = asyncio.Event()
    release = asyncio.Event()

    class Waiting(Provider):
        async def open(self, host):
            entered.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return self.binding
            finally:
                provider_cleanup.set()

    first, waiting = Provider("a-first"), Waiting("z-waiting")
    runtime, _ = installed(first, waiting)
    capabilities = runtime.CapabilitySet("session")
    task = asyncio.create_task(capabilities.start(object()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await checkpoint()
        assert not task.done(), "Cancelled startup must settle its actual provider work"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider_cleanup.is_set()
        assert first.binding.closes == 0 and waiting.binding.closes == 0
        assert capabilities.get("a-first") is first.binding
        assert capabilities.get("z-waiting") is waiting.binding
    finally:
        release.set()
        await settle(task)
        await capabilities.aclose()
    assert first.binding.closes == waiting.binding.closes == 1


async def test_close_joins_inflight_start_and_never_publishes_after_close(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(Provider):
        async def open(self, host):
            entered.set()
            # Model a provider finishing an owned commit even after cancellation.
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return self.binding

    provider = Slow("sample")
    provider.default_skills = ("sample-skill",)
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet("session")
    start = asyncio.create_task(capabilities.start(object()))
    close = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        close = asyncio.create_task(capabilities.aclose())
        await checkpoint()
        assert not close.done(), "Close must join startup before releasing this capability owner"
        assert capabilities.default_skills == frozenset()
        release.set()
        await asyncio.gather(start, close, return_exceptions=True)
        assert provider.binding.closes == 1
        assert capabilities.default_skills == frozenset()
        assert capabilities.get("sample") is None and capabilities.tools == ()
    finally:
        release.set()
        await settle(*(task for task in (start, close) if task is not None))
        await capabilities.aclose()


async def test_concurrent_close_joins_one_actual_cleanup(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowBinding(Binding):
        async def aclose(self):
            self.closes += 1
            entered.set()
            await release.wait()

    binding = SlowBinding()
    runtime, _ = installed(Provider("sample", binding))
    capabilities = runtime.CapabilitySet("group")
    await capabilities.start(object())
    first = asyncio.create_task(capabilities.aclose())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(capabilities.aclose())
        await checkpoint()
        assert binding.closes == 1
        assert not first.done() and not second.done()
        release.set()
        await asyncio.gather(first, second)
    finally:
        release.set()
        await settle(*(task for task in (first, second) if task is not None))
        await capabilities.aclose()


async def test_cancelled_close_still_joins_real_cleanup(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowBinding(Binding):
        async def aclose(self):
            self.closes += 1
            entered.set()
            await release.wait()

    binding = SlowBinding()
    runtime, _ = installed(Provider("sample", binding))
    capabilities = runtime.CapabilitySet("session")
    await capabilities.start(object())
    task = asyncio.create_task(capabilities.aclose())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await checkpoint()
        task.cancel()
        await checkpoint()
        assert not task.done(), "Caller cancellation cannot abandon actual capability cleanup"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert binding.closes == 1
        assert capabilities.get("sample") is None and capabilities.tools == ()
    finally:
        release.set()
        await settle(task)
        await capabilities.aclose()
    assert binding.closes == 1


async def test_failed_close_settles_other_bindings_and_retires_all_tools(installed):
    class BrokenBinding(Binding):
        async def aclose(self):
            self.closes += 1
            raise ValueError("close-failed")

    broken, healthy = BrokenBinding(), Binding()
    first, second = Provider("a-broken", broken), Provider("z-healthy", healthy)
    first.default_skills = ("broken-skill",)
    second.default_skills = ("healthy-skill",)
    runtime, _ = installed(first, second)
    capabilities = runtime.CapabilitySet("session")
    await capabilities.start(object())
    assert capabilities.default_skills == frozenset({"broken-skill", "healthy-skill"})
    with pytest.raises(ValueError, match="close-failed"):
        await capabilities.aclose()
    assert broken.closes == healthy.closes == 1
    assert capabilities.get("a-broken") is None and capabilities.get("z-healthy") is None
    assert capabilities.tools == ()

    assert capabilities.default_skills == frozenset()

async def test_parent_port_is_frozen_and_forwarded_without_private_host_objects(installed):
    provider = Provider("sample")
    runtime, _ = installed(provider)
    parent = runtime.ParentPort(
        agent=object(), extensions=object(), children=object(), valid=lambda: True, user_authorized=lambda: False,
    )
    with pytest.raises(FrozenInstanceError):
        parent.children = object()
    capabilities = runtime.CapabilitySet("group")
    try:
        await capabilities.start(object())
        capabilities.attach(parent)
        assert provider.binding.parents == [parent]
        assert provider.binding.parents[0] is parent
        assert parent.valid() and not parent.user_authorized()
    finally:
        await capabilities.aclose()
    with pytest.raises(RuntimeError):
        capabilities.attach(parent)


async def test_descriptor_tools_bind_their_own_execute_and_preserve_arguments(installed):
    from xdog.agent.core import AgentToolResult

    calls = []

    def descriptor(name):
        async def execute(*args, **kwargs):
            calls.append((name, args, kwargs))
            return name
        return SimpleNamespace(name=name, description=f"Tool {name}", parameters={"type": "object"}, execute=execute)

    first, second = descriptor("first"), descriptor("second")
    binding = Binding(tools=(first, second))
    runtime, _ = installed(Provider("sample", binding, tool_names=("first", "second")))
    capabilities = runtime.CapabilitySet("session")
    try:
        await capabilities.start(object())
        tools = capabilities.tools
        assert len(tools) == 2 and all(a is b for a, b in zip(tools, capabilities.tools, strict=True))
        cancel, ctx, update = asyncio.Event(), {"opaque": object()}, object()
        for tool in tools:
            result = await tool.execute("call-id", {"x": 1}, cancel=cancel, on_update=update, ctx=ctx)
            assert isinstance(result, AgentToolResult)
            assert result.content[0].text == tool.name
            assert tool.description == f"Tool {tool.name}" and tool.parameters == {"type": "object"}
        assert [call[0] for call in calls] == ["first", "second"]
        for _, args, kwargs in calls:
            assert args == ("call-id", {"x": 1})
            assert kwargs == {"cancel": cancel, "on_update": update, "ctx": ctx}
    finally:
        await capabilities.aclose()


async def test_actual_agent_tool_and_structured_result_keep_identity(installed):
    from xdog.agent.core import AgentTool, AgentToolResult

    result = AgentToolResult.from_text("blocked", is_error=True, details={"owner": "original"})

    async def execute(*args, **kwargs):
        return result

    native = AgentTool(name="native", execute=execute)
    descriptor = SimpleNamespace(name="descriptor", description="", parameters={}, execute=execute)
    runtime, _ = installed(Provider("sample", Binding(tools=(native, descriptor)),
                                    tool_names=("native", "descriptor")))
    capabilities = runtime.CapabilitySet("session")
    try:
        await capabilities.start(object())
        assert capabilities.tools[0] is native
        assert await capabilities.tools[1].execute("call", {}) is result
    finally:
        await capabilities.aclose()


async def test_duplicate_tool_names_across_bindings_are_rejected(installed):
    from xdog.agent.core import AgentTool

    runtime, _ = installed(
        Provider("first", Binding(tools=(AgentTool(name="collision"),))),
        Provider("second", Binding(tools=(AgentTool(name="collision"),))),
    )
    capabilities = runtime.CapabilitySet("session")
    try:
        # Rejection may occur at startup or at first inventory materialization.
        with pytest.raises(ValueError):
            await capabilities.start(object())
            _ = capabilities.tools
    finally:
        await capabilities.aclose()


async def test_saved_data_never_selects_code_or_additional_providers(installed, tmp_path, monkeypatch):
    import builtins

    marker = tmp_path / "executed"
    payload = tmp_path / "untrusted_capability.py"
    payload.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("untrusted_capability"), "Saved data is not an installed code authority"
        return original(name, *args, **kwargs)

    provider = Provider("installed")
    runtime, entries = installed(provider)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    host = SimpleNamespace(state={
        "provider": "untrusted_capability:provider", "module": "untrusted_capability",
        "entry_points": {"xdog.capabilities": "untrusted_capability:provider"}, "code": str(payload),
    })
    capabilities = runtime.CapabilitySet("session")
    try:
        await capabilities.start(host)
        assert provider.hosts == [host] and capabilities.get("installed") is provider.binding
        assert entries[0].loads == 1 and not marker.exists()
    finally:
        await capabilities.aclose()


async def test_one_cancelled_start_waiter_does_not_revoke_another_owner_waiter(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(Provider):
        async def open(self, host):
            self.hosts.append(host)
            entered.set()
            await release.wait()
            return self.binding

    provider = Slow("sample")
    runtime, _ = installed(provider)
    capabilities, host = runtime.CapabilitySet("session"), object()
    detached = asyncio.create_task(capabilities.start(host))
    accepted = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        accepted = asyncio.create_task(capabilities.start(host))
        await checkpoint()
        detached.cancel()
        await checkpoint()
        assert not detached.done() and not accepted.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await detached
        await accepted
        assert provider.hosts == [host]
        assert provider.binding.closes == 0 and capabilities.get("sample") is provider.binding
        await capabilities.start(host)
        assert provider.hosts == [host]
    finally:
        release.set()
        await settle(*(task for task in (detached, accepted) if task is not None))
        await capabilities.aclose()
    assert provider.binding.closes == 1


async def test_close_racing_open_propagates_actual_late_binding_close_failure(installed):
    entered, release = asyncio.Event(), asyncio.Event()

    class BrokenBinding(Binding):
        async def aclose(self):
            self.closes += 1
            raise ValueError("late-binding-close-failed")

    class Slow(Provider):
        async def open(self, host):
            entered.set()
            await release.wait()
            return self.binding

    binding = BrokenBinding()
    runtime, _ = installed(Slow("sample", binding))
    capabilities = runtime.CapabilitySet("session")
    start = asyncio.create_task(capabilities.start(object()))
    close = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        close = asyncio.create_task(capabilities.aclose())
        await checkpoint()
        assert not close.done()
        release.set()
        with pytest.raises(ValueError, match="late-binding-close-failed"):
            await close
        assert binding.closes == 1
        assert capabilities.get("sample") is None and capabilities.tools == ()
    finally:
        release.set()
        await settle(*(task for task in (start, close) if task is not None))


async def test_stale_parent_is_rejected_before_any_binding_is_attached(installed):
    provider = Provider("sample")
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet("session")
    parent = runtime.ParentPort(
        agent=object(), extensions=object(), children=object(), valid=lambda: False, user_authorized=lambda: True,
    )
    try:
        await capabilities.start(object())
        with pytest.raises((ValueError, RuntimeError)):
            capabilities.attach(parent)
        assert provider.binding.parents == []
    finally:
        await capabilities.aclose()


def test_duplicate_declared_tool_names_are_rejected_before_open(installed):
    first = Provider("first", tool_names=("collision",))
    second = Provider("second", tool_names=("collision",))
    runtime, _ = installed(first, second)
    with pytest.raises(ValueError):
        runtime.CapabilitySet("session")
    assert not first.hosts and not second.hosts


def test_optional_integrations_are_metadata_not_an_execution_or_storage_start(installed):
    provider = Provider('metadata_only', scopes=('group',))
    provider.optional_requirements = ('acp',)
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet('group')
    assert capabilities.requirements == frozenset()
    assert capabilities.optional_requirements == frozenset({'acp'})
    assert not provider.hosts and not capabilities.tools
    provider.optional_requirements = ('acp', None)
    with pytest.raises(ValueError, match='metadata'):
        runtime.CapabilitySet('group')


def test_declared_native_commands_are_inert_and_do_not_claim_builtin_controls(installed):
    provider = Provider('contribution', scopes=('session',))
    provider.command_names = ('native-command',)
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet('session')
    assert capabilities.declared_commands == frozenset({'native-command'})
    assert not provider.hosts and not capabilities.tools


@pytest.mark.parametrize("value", [
    None, "sample", 1, {"sample"}, {"sample": True}, (None,), (1,), ("",),
    ("two words",), ("../sample",), ("sample.md",), ("-sample",), ("1sample",),
    ("a" * 65,), ("sample", "sample"), ["sample", "sample"],
])
def test_invalid_default_skills_are_rejected_during_discovery(installed, value):
    provider = Provider("sample")
    provider.default_skills = value
    runtime, _ = installed(provider)
    with pytest.raises(ValueError, match="metadata"):
        runtime.discover("session")
    assert not provider.hosts


@pytest.mark.parametrize("value", [(), ("Sample-1", "other_skill"), ["sample"], frozenset({"sample"})])
async def test_default_skills_use_existing_metadata_name_rules(installed, value):
    provider = Provider("sample")
    provider.default_skills = value
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet("session")
    assert capabilities.default_skills == frozenset()
    try:
        await capabilities.start(object())
        assert capabilities.default_skills == frozenset(value)
    finally:
        await capabilities.aclose()
    assert capabilities.default_skills == frozenset()


async def test_default_skills_are_inert_then_union_only_live_providers_in_scope(installed, monkeypatch):
    first, second = Provider("first"), Provider("second")
    first.default_skills = ("shared", "first-skill")
    second.default_skills = ("shared", "second-skill")
    legacy = Provider("legacy")  # No optional metadata field remains supported.
    application = Provider("application", scopes=("application",))
    application.default_skills = ("application-skill",)
    runtime, entries = installed(first, second, legacy, application)

    assert runtime.discover("session") == (first, legacy, second)
    capabilities = runtime.CapabilitySet("session")
    assert capabilities.default_skills == frozenset()
    assert all(not provider.hosts for provider in (first, second, legacy, application))
    assert all(entry.loads == 2 for entry in entries)

    # Metadata access must not open skill files or perform another discovery.
    def unexpected(*args, **kwargs):
        pytest.fail("Reading default skill metadata must not do IO or discover providers")

    with monkeypatch.context() as guarded:
        guarded.setattr("builtins.open", unexpected)
        guarded.setattr("pathlib.Path.open", unexpected)
        guarded.setattr(runtime, "entry_points", unexpected)
        assert capabilities.default_skills == frozenset()
    try:
        await capabilities.start(object())
        with monkeypatch.context() as guarded:
            guarded.setattr("builtins.open", unexpected)
            guarded.setattr("pathlib.Path.open", unexpected)
            guarded.setattr(runtime, "entry_points", unexpected)
            defaults = capabilities.default_skills
            assert isinstance(defaults, frozenset)
            assert defaults == frozenset({"shared", "first-skill", "second-skill"})
        assert not application.hosts
    finally:
        await capabilities.aclose()
    assert capabilities.default_skills == frozenset()


async def test_provider_without_default_skills_remains_compatible(installed):
    provider = Provider("legacy")
    runtime, _ = installed(provider)
    capabilities = runtime.CapabilitySet("session")
    try:
        await capabilities.start(object())
        assert capabilities.get("legacy") is provider.binding
        assert capabilities.default_skills == frozenset()
    finally:
        await capabilities.aclose()
    assert capabilities.default_skills == frozenset()


async def test_default_skills_stay_empty_during_start_and_close(installed):
    opening, finish_open = asyncio.Event(), asyncio.Event()
    closing, finish_close = asyncio.Event(), asyncio.Event()

    class SlowBinding(Binding):
        async def aclose(self):
            self.closes += 1
            closing.set()
            await finish_close.wait()

    class Slow(Provider):
        async def open(self, host):
            opening.set()
            await finish_open.wait()
            return await super().open(host)

    first, slow = Provider("a-first"), Slow("z-slow", SlowBinding())
    first.default_skills = ("first-skill",)
    slow.default_skills = ("slow-skill",)
    runtime, _ = installed(first, slow)
    capabilities = runtime.CapabilitySet("session")
    start = asyncio.create_task(capabilities.start(object()))
    close = None
    try:
        await asyncio.wait_for(opening.wait(), 2)
        assert capabilities.get("a-first") is first.binding
        assert capabilities.default_skills == frozenset()
        finish_open.set()
        await start
        assert capabilities.default_skills == frozenset({"first-skill", "slow-skill"})
        close = asyncio.create_task(capabilities.aclose())
        await asyncio.wait_for(closing.wait(), 2)
        assert capabilities.get("z-slow") is slow.binding
        assert capabilities.default_skills == frozenset()
        finish_close.set()
        await close
        assert capabilities.default_skills == frozenset()
    finally:
        finish_open.set()
        finish_close.set()
        await settle(*(task for task in (start, close) if task is not None))
        await capabilities.aclose()


@pytest.mark.parametrize("fail_inventory", [False, True])
@pytest.mark.parametrize("fail_cleanup", [False, True])
async def test_default_skills_do_not_leak_during_or_after_failed_start(installed, fail_inventory, fail_cleanup):
    from xdog.agent.core import AgentTool

    closing, release = asyncio.Event(), asyncio.Event()

    class Cleanup(Binding):
        async def aclose(self):
            self.closes += 1
            closing.set()
            await release.wait()
            if fail_cleanup:
                raise ValueError("cleanup-failed")

    class Broken(Provider):
        async def open(self, host):
            if not fail_inventory:
                raise ValueError("open-failed")
            return await super().open(host)

    first = Provider("a-first", Cleanup(tools=(AgentTool(name="collision"),)))
    broken = Broken("z-broken", Binding(tools=(AgentTool(name="collision"),)))
    first.default_skills = ("first-skill",)
    broken.default_skills = ("broken-skill",)
    runtime, _ = installed(first, broken)
    capabilities = runtime.CapabilitySet("session")
    start = asyncio.create_task(capabilities.start(object()))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        assert capabilities.get("a-first") is first.binding
        assert capabilities.default_skills == frozenset()
        release.set()
        with pytest.raises(BaseExceptionGroup if fail_cleanup else ValueError):
            await start
        assert capabilities.default_skills == frozenset()
        assert capabilities.get("a-first") is None
        assert first.binding.closes == 1
    finally:
        release.set()
        await settle(start)
        await capabilities.aclose()
    assert capabilities.default_skills == frozenset()
