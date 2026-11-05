"""Actual owner-bound readonly view grants and the real Python worker protocol."""
from __future__ import annotations

import asyncio
import json
import threading

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
