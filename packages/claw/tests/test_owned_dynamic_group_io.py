"""Message-triggered group resource construction is owned IO, never a second agent loop."""
from __future__ import annotations

import asyncio
import builtins
import io
import os
import threading
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


async def test_first_dynamic_group_input_builds_resources_off_loop_and_runs_one_model(tmp_path, monkeypatch):
    owner = threading.get_ident()
    opened, calls = [], []
    actual_open, actual_io_open = builtins.open, io.open

    def observe(actual, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(tmp_path):
            opened.append((Path(file), threading.get_ident()))
        return actual(file, *args, **kwargs)

    def model(*args):
        assert threading.get_ident() == owner
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual dynamic input"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dynamic", stream_fn=model)
    try:
        await app.start()
        monkeypatch.setattr(builtins, "open", lambda file, *a, **kw: observe(actual_open, file, *a, **kw))
        monkeypatch.setattr(io, "open", lambda file, *a, **kw: observe(actual_io_open, file, *a, **kw))
        result = await app.route_message(UserInput(group_id="weixin:fixture", content="actual first input"))
        assert result is not None and result.error is None and len(calls) == 1
        assert opened and all(thread != owner for _path, thread in opened), (
            "Dynamic construction blocked owner", opened
        )
        assert app._runtimes["weixin:fixture"]._active_session._host_loop is not None
    finally:
        await app.stop()


async def test_concurrent_dynamic_inputs_publish_one_runtime_after_actual_factory_settles(tmp_path, monkeypatch):
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dynamic",
        stream_fn=lambda *args: EventStream.empty(AssistantMessage(content=(TextContent(text="reply"),))))
    entered, release = threading.Event(), threading.Event()
    built = []
    original = app._build_group

    def actual_build(group):
        value = original(group)
        built.append(value)
        entered.set()
        assert release.wait(5)
        return value

    tasks = []
    try:
        await app.start()
        monkeypatch.setattr(app, "_build_group", actual_build)
        tasks.append(asyncio.create_task(app.route_message(UserInput(group_id="weixin:one", content="first"))))
        assert await asyncio.to_thread(entered.wait, 3)
        tasks.append(asyncio.create_task(app.route_message(UserInput(group_id="weixin:one", content="second"))))
        await asyncio.sleep(0)
        assert "weixin:one" not in app._runtimes and built[0]._active_session is None
        release.set()
        await asyncio.gather(*tasks)
        assert len(built) == 1 and app._runtimes["weixin:one"] is built[0]
        from xdog.claw.core.inputs.models import InputScope
        service = await app.input_service()
        scope = InputScope("weixin:one", app.get_group_workspace("weixin:one"))
        records = await service.store.records(scope)
        for row in records:
            assert (await service.wait(scope, row.envelope.message.id)).status == "succeeded"
        assert len(records) == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.stop()


async def test_cancelled_dynamic_factory_closes_unpublished_resources_and_never_runs_input(tmp_path, monkeypatch):
    calls = []

    def no_model(*args):
        calls.append(args)
        raise AssertionError("Unaccepted cancelled input must not run a model")

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dynamic", stream_fn=no_model)
    entered, release = threading.Event(), threading.Event()
    built = []
    original = app._build_group

    def actual_build(group):
        value = original(group)
        built.append(value)
        entered.set()
        assert release.wait(5)
        return value

    work = None
    try:
        await app.start()
        monkeypatch.setattr(app, "_build_group", actual_build)
        work = asyncio.create_task(app.route_message(UserInput(group_id="weixin:cancelled", content="no intake yet")))
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and "weixin:cancelled" not in app._runtimes and not calls
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert built[0].mcp._closed and built[0]._active_session is None
        assert "weixin:cancelled" not in app._runtimes and not app._group_initializers
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()


@pytest.mark.parametrize("invalid", [
    {"child_presets": {"bad": {"config": {"unknown_policy": True}}}},
    {"mcp_servers": {"bad": {"transport": "invalid"}}},
    {"mcp_credential_backend": "invalid"},
])
async def test_invalid_group_configuration_fails_before_memory_resource_allocation(tmp_path, monkeypatch, invalid):
    import xdog.claw.core.runtime.group as module

    created = []
    original = module.MemoryManager

    def actual_memory(*args, **kwargs):
        value = original(*args, **kwargs)
        created.append(value)
        return value

    monkeypatch.setattr(module, "MemoryManager", actual_memory)
    try:
        with pytest.raises(ValueError):
            await asyncio.to_thread(module.GroupRuntime.create, Group(id="main"), tmp_path, model="test/dynamic",
                                    **invalid)
        assert created == [], "Invalid descriptors allocated real memory resources before validation"
    finally:
        for value in created:
            await value.close()


async def test_shutdown_joins_late_dynamic_factory_and_closes_resources_without_publication(tmp_path, monkeypatch):
    def no_model(*args):
        raise AssertionError("No input was accepted before shutdown")

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dynamic", stream_fn=no_model)
    entered, release = threading.Event(), threading.Event()
    built = []
    original = app._build_group

    def actual_build(group):
        value = original(group)
        built.append(value)
        entered.set()
        assert release.wait(5)
        return value

    work = stopping = None
    try:
        await app.start()
        monkeypatch.setattr(app, "_build_group", actual_build)
        work = asyncio.create_task(app.route_message(UserInput(group_id="weixin:late", content="not accepted")))
        assert await asyncio.to_thread(entered.wait, 3)
        stopping = asyncio.create_task(app.stop())
        await asyncio.sleep(0)
        assert not stopping.done() and "weixin:late" not in app._runtimes
        release.set()
        with pytest.raises(RuntimeError, match="stopped before publication"):
            await work
        await stopping
        assert len(built) == 1 and built[0].mcp._closed and built[0]._active_session is None
        assert "weixin:late" not in app._runtimes and not app._group_initializers
    finally:
        release.set()
        await asyncio.gather(*(t for t in (work, stopping) if t is not None), return_exceptions=True)
        await app.stop()


@pytest.mark.parametrize("identifier", [
    "weixin:../../outside", "weixin:back\\slash", "weixin:null\x00", "weixin:" + "x" * 257,
])
async def test_unsafe_dynamic_identity_is_rejected_before_allocating_resources(tmp_path, monkeypatch, identifier):
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dynamic")
    try:
        await app.start()
        with pytest.raises(ValueError, match="dynamic group identity"):
            await app.route_message(UserInput(group_id=identifier, content="no allocation"))
        assert not app._runtimes and not app._group_initializers
        assert not (tmp_path / "data" / "groups").exists()
    finally:
        await app.stop()
