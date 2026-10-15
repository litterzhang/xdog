import asyncio
import json
import tempfile
import time
from pathlib import Path


def test_cli_runtime_observes_files_and_stops_with_last_registration(tmp_path, observer_data, monkeypatch, capsys):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox
    from xdog.eyes.cli import main
    from xdog.eyes.control import request
    from xdog.eyes.transport.protocol import load_context

    observer_data["sources"][0].update(poll_interval_ms=50, include=["src/**"])
    observer_data["detectors"][0].update(debounce_ms=50, max_batch_delay_ms=200)
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    definition = root / "eyes.json"
    definition.write_text(json.dumps(observer_data))
    inbox = ExternalEventInbox()
    inbox.human_input()
    with tempfile.TemporaryDirectory(prefix="eyes-host-") as short:
        host = ExternalEventHost(inbox, scope=lambda: ("session", "main"), directory=Path(short) / "private")
        host.start()
        monkeypatch.setenv("XDOG_EVENT_CONTEXT", str(host.descriptor))
        context = load_context(host.descriptor)
        try:
            assert main(["start", str(definition)]) == 0
            first = json.loads(capsys.readouterr().out)
            identifier = first["registration_id"]
            assert main(["start", str(definition)]) == 0
            assert json.loads(capsys.readouterr().out)["registration_id"] == identifier
            deadline = time.monotonic() + 5
            while True:
                response = asyncio.run(request(context, "status", {"registration_id": identifier}))
                state = response["body"]["registrations"][0]
                if state["sources"].get("files") == "watching":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.03)
            (root / "src" / "a.py").write_text("abc")
            while inbox.snapshot()["pending_count"] == 0:
                assert time.monotonic() < deadline
                time.sleep(0.03)
            job = inbox.take(busy=False, human_pending=False)
            assert job is not None and job.instruction == "Review tests"
            assert job.events[0]["type"] == "file.created"
            assert job.events[0]["evidence"][0]["root"] == str(root)
            assert main(["stop", identifier]) == 0
            assert json.loads(capsys.readouterr().out)["stopped"] is True
            while (context.directory / "eyes.sock").exists():
                assert time.monotonic() < deadline
                time.sleep(0.03)
        finally:
            host.close()


async def test_unauthorized_stop_does_not_terminate_idle_runtime(tmp_path):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox
    from xdog.eyes.control import request
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.transport.protocol import HostContext, load_context

    with tempfile.TemporaryDirectory(prefix="eyes-control-") as short:
        host = ExternalEventHost(ExternalEventInbox(), scope=lambda: ("session", "main"),
                                 directory=Path(short) / "private")
        host.start()
        context = load_context(host.descriptor)
        runtime = ObserverRuntime(context)
        task = asyncio.create_task(runtime.serve())
        try:
            deadline = time.monotonic() + 2
            while not (context.directory / "eyes.sock").exists():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            invalid = HostContext(context.address, context.owner, "wrong-capability", context.descriptor)
            response = await request(invalid, "stop", {"registration_id": "absent"})
            assert response["status"] == "rejected"
            await asyncio.sleep(0.05)
            assert not task.done()
            assert (await request(context, "hello", {}))["status"] == "ok"
        finally:
            runtime.stop.set()
            await task
            host.close()


def test_concurrent_starts_share_runtime_and_registration(tmp_path, observer_data):
    from concurrent.futures import ThreadPoolExecutor

    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox
    from xdog.eyes.control import request
    from xdog.eyes.loader import parse
    from xdog.eyes.process import ensure_runtime
    from xdog.eyes.runtime import definition_data
    from xdog.eyes.transport.protocol import load_context

    with tempfile.TemporaryDirectory(prefix="eyes-concurrent-") as short:
        host = ExternalEventHost(ExternalEventInbox(), scope=lambda: ("session", "main"),
                                 directory=Path(short) / "private")
        host.start()
        context = load_context(host.descriptor)
        normalized = definition_data(parse(observer_data, base_dir=tmp_path))

        def start(_):
            ensure_runtime(context)
            return asyncio.run(request(context, "start", {"definition": normalized}))["body"]["registration_id"]

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                identifiers = list(pool.map(start, range(4)))
            assert len(set(identifiers)) == 1
            response = asyncio.run(request(context, "status", {}))
            assert len(response["body"]["registrations"]) == 1
            asyncio.run(request(context, "stop", {"registration_id": identifiers[0]}))
        finally:
            host.close()


async def test_context_revocation_stops_runtime_without_replay(tmp_path, observer_data):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox
    from xdog.eyes.control import request
    from xdog.eyes.loader import parse
    from xdog.eyes.runtime import ObserverRuntime, definition_data
    from xdog.eyes.transport.protocol import load_context

    with tempfile.TemporaryDirectory(prefix="eyes-revoke-") as short:
        host = ExternalEventHost(ExternalEventInbox(), scope=lambda: ("session", "main"),
                                 directory=Path(short) / "private")
        host.start()
        context = load_context(host.descriptor)
        runtime = ObserverRuntime(context)
        task = asyncio.create_task(runtime.serve())
        try:
            deadline = time.monotonic() + 2
            while not (context.directory / "eyes.sock").exists():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            response = await request(context, "start", {"definition": definition_data(
                parse(observer_data, base_dir=tmp_path))})
            assert response["status"] == "accepted"
            host.close()
            await asyncio.wait_for(task, 2)
            assert not (context.directory / "eyes.sock").exists()
            assert runtime.entries == {}
        finally:
            runtime.stop.set()
            await task
            host.close()


async def test_failed_heartbeats_end_registered_activity_at_fifteen_seconds(tmp_path, observer_data):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox
    from xdog.eyes.loader import parse
    from xdog.eyes.runtime import ObserverRuntime, definition_data
    from xdog.eyes.testing import FakeClock
    from xdog.eyes.transport.protocol import load_context

    clock, calls = FakeClock(limit=16), []
    with tempfile.TemporaryDirectory(prefix="eyes-heartbeat-") as short:
        host = ExternalEventHost(ExternalEventInbox(), scope=lambda: ("session", "main"),
                                 directory=Path(short) / "private")
        host.start()
        runtime = ObserverRuntime(load_context(host.descriptor), clock=clock.now, sleep=clock.sleep)
        original = runtime.client

        class FailedHost:
            async def request(self, message):
                assert message == {"action": "ping", "body": {}}
                calls.append(clock.now())
                raise OSError("host unavailable")

        try:
            await runtime.start(definition_data(parse(observer_data, base_dir=tmp_path)))
            runtime.client = FailedHost()
            await runtime.monitor()
            assert runtime.stop.is_set()
            assert 15 <= clock.now() <= 15.3
            assert len(calls) == 3
            assert calls[0] == 0 and 5 <= calls[1] <= 5.3 and 10 <= calls[2] <= 10.6
        finally:
            runtime.client = original
            for identifier in tuple(runtime.entries):
                await runtime.stop_entry(identifier)
            await original.aclose()
            host.close()
