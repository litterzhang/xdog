"""Actual owner-bound readonly view grants and the real Python worker protocol."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime


def worker(root, name):
    path = root / name
    path.mkdir()
    (path / "extension.yaml").write_text(f"name: {name}\nentry_point: main\n")
    (path / "main.py").write_text('''import json

def setup(api):
    async def read(args, context):
        if args == "forge":
            api._has_views.set(True)  # Server identity, not worker cooperation, enforces the grant.
        value = await api.read_view("work", query={"limit": 1})
        if args == "wait":
            await api.read_view("gate")
            api.status("value", json.dumps(value))
        return json.dumps({"value": value, "has_ui": api.has_ui})
    api.command("read", "Read bound data without UI", read)
    async def changed(context):
        value = await api.read_view("work")
        api.status("read", json.dumps(value))
    api.on("view_changed", changed)
''')


async def test_registry_revocation_during_await_refuses_old_data_and_cannot_revoke_successor():
    from xdog.agent.extensions.views import ViewRegistry

    entered, release = asyncio.Event(), asyncio.Event()

    async def read(query):
        entered.set()
        await release.wait()
        return {"revision": "old", "items": ["private"]}

    registry = ViewRegistry()
    remove = registry.bind("display", "work", read, valid=lambda: True)
    pending = asyncio.create_task(registry.read("display", "work", {}))
    await entered.wait()
    remove()
    registry.bind("display", "work", read, valid=lambda: True)
    remove()  # The old revoker cannot remove a replacement binding.
    release.set()
    assert await pending is None
    assert await registry.read("other", "work", {}) is None
    assert await registry.read("display", "work", {}) == {"revision": "old", "items": ["private"]}
    registry.close()
    assert await registry.read("display", "work", {}) is None


async def test_registry_checks_real_owner_after_read_and_clones_query_and_result():
    from xdog.agent.extensions.views import ViewRegistry

    owner = ["original"]
    source = {"items": ["private"]}

    async def read(query):
        query["nested"].append("provider mutation")
        return source

    registry = ViewRegistry()
    registry.bind("display", "work", read, valid=lambda: owner[0] == "original")
    query = {"nested": []}
    result = await registry.read("display", "work", query)
    assert query == {"nested": []}
    source["items"].append("later mutation")
    assert result == {"items": ["private"]}
    owner[0] = "successor"
    assert await registry.read("display", "work", query) is None


@pytest.mark.parametrize("extra", ["owner", "extension_id", "method", "session_id", "provider"])
def test_view_request_cannot_select_a_host_owner_or_method(extra):
    from xdog.agent.extensions.views import request

    with pytest.raises(ValueError):
        request({"name": "work", "query": {}, extra: "forged"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "海" * 2000], ids=["nan", "inf", "utf8-oversize"])
def test_view_queries_reject_nonfinite_and_encoded_oversize(value):
    from xdog.agent.extensions.views import request

    with pytest.raises(ValueError):
        request({"name": "work", "query": {"data": value}})


def test_view_answers_reject_deep_or_unbounded_data_without_coercing_objects():
    from xdog.agent.extensions.views import answer

    nested = {}
    for _ in range(40):
        nested = {"nested": nested}
    for value in (nested, {"text": "x" * 65536}, {"n": float("nan")}, {1: "not a JSON key"}, ["not snapshot"]):
        with pytest.raises(ValueError):
            answer(value)
    assert answer(None) is None
    assert answer({"items": []}) == {"items": []}


async def test_actual_worker_reads_only_its_grant_without_tui_or_dialog_approval(tmp_path):
    worker(tmp_path, "display")
    worker(tmp_path, "other")
    runtime = ExtensionRuntime(("display", "other"), tmp_path, tmp_path,
                               context=lambda: {"session_id": "original", "host_views": True})
    reads = []

    async def read(query):
        reads.append(query)
        return {"revision": "1", "items": ["private"]}

    try:
        runtime.views.bind("display", "work", read, valid=lambda: True)
        assert not runtime._manager._clients  # Registering a read port starts no process.
        actual = json.loads(await runtime.command("ext-display-read", ""))
        assert actual == {"value": {"revision": "1", "items": ["private"]}, "has_ui": False}
        denied = json.loads(await runtime.command("ext-other-read", "forge"))
        assert denied == {"value": None, "has_ui": False}
        assert reads == [{"limit": 1}] and runtime.dialog_handler is None
    finally:
        await runtime.aclose()


async def test_real_worker_cannot_keep_data_after_actual_owner_changes_while_provider_waits(tmp_path):
    worker(tmp_path, "display")
    scope = {"session_id": "original"}
    entered, release = threading.Event(), threading.Event()
    runtime = ExtensionRuntime(("display",), tmp_path, tmp_path, context=lambda: dict(scope))

    async def read(query):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.001)
        return {"private": "original only"}

    task = None
    try:
        runtime.views.bind("display", "work", read, valid=lambda: scope["session_id"] == "original")
        task = asyncio.create_task(runtime.command("ext-display-read", ""))
        assert await asyncio.to_thread(entered.wait, 3)
        scope["session_id"] = "successor"
        release.set()
        result = str(await task)
        assert "unavailable" in result.lower() and "original only" not in result
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_view_reads_cannot_be_forwarded_to_ui_approval_broker():
    from xdog.agent.extensions.dialogs import DialogBroker

    broker = DialogBroker()
    with pytest.raises(ValueError, match="view|View"):
        await broker.ask("display", {"kind": "view", "id": "read", "title": "Read data",
                                     "name": "work", "query": {}})
    assert not broker.has_pending


async def test_changed_hints_coalesce_on_actual_worker_and_do_not_start_or_reload_it(tmp_path):
    worker(tmp_path, "display")
    runtime = ExtensionRuntime(("display",), tmp_path, tmp_path, context=lambda: {"session_id": "original"})
    entered, release = threading.Event(), threading.Event()
    reads = []

    async def read(query):
        reads.append(query)
        if len(reads) == 1:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.001)
        return {"reads": len(reads), "items": ["actual data, never a hint"]}

    try:
        runtime.views.bind("display", "work", read, valid=lambda: True)
        runtime.notify_view_changed("work", "before-start")
        assert not runtime._manager._clients and runtime._manager._loop is None
        await runtime.initialize()
        runtime.notify_view_changed("work", "first")
        assert await asyncio.to_thread(entered.wait, 3)
        for i in range(64):
            runtime.notify_view_changed("work", str(i))
        # Real owner barrier: all pending hints are admitted before releasing the read.
        await runtime._manager._bridge(asyncio.sleep(0))
        release.set()
        async with asyncio.timeout(5):
            while True:
                row = runtime.snapshot()["ui"].get("display:read")
                if row and json.loads(row["lines"][0])["reads"] == 2:
                    break
                await asyncio.sleep(.005)
        assert len(reads) == 2
        await runtime.manage("unload", "display")
        runtime.notify_view_changed("work", "after-unload")
        await runtime._manager._bridge(asyncio.sleep(0))
        assert len(reads) == 2 and not runtime.snapshot()["ui"]
        assert not runtime._manager._clients
    finally:
        release.set()
        await runtime.aclose()


async def test_callback_cannot_publish_an_earlier_view_after_its_grant_was_replaced(tmp_path):
    worker(tmp_path, "display")
    runtime = ExtensionRuntime(("display",), tmp_path, tmp_path, context=lambda: {"session_id": "same-session"})
    entered, release = threading.Event(), threading.Event()

    async def private(query):
        return {"private": "old branch only"}

    async def gate(query):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.001)
        return {"ok": True}

    pending = None
    try:
        remove = runtime.views.bind("display", "work", private, valid=lambda: True)
        runtime.views.bind("display", "gate", gate, valid=lambda: True)
        pending = asyncio.create_task(runtime.command("ext-display-read", "wait"))
        assert await asyncio.to_thread(entered.wait, 3)
        remove()
        runtime.views.bind("display", "work", private, valid=lambda: True)
        release.set()
        result = await pending
        assert "unavailable" in str(result).lower() and "private" not in str(result)
        assert "display:value" not in runtime.snapshot()["ui"]
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.aclose()


async def test_named_host_ports_register_and_read_the_same_scoped_provider():
    from xdog.agent.extensions.views import ViewRegistry

    async def read(query):
        return {"query": query, "items": ["actual provider"]}

    registry = ViewRegistry()
    registry.bind(extension_id="display", name="work", read=read, valid=lambda: True)
    assert await registry.read(extension_id="display", name="work", query={"limit": 1}) == {
        "query": {"limit": 1}, "items": ["actual provider"]}
