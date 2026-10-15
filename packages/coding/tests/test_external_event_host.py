import asyncio
import json
import time
from pathlib import Path


def fixture_message():
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "docs/fixtures/eyes-host-v1.json").read_text())


async def request(descriptor, action, body, *, owner=None, token=None):
    context = json.loads(descriptor.read_text())
    message = {"version": 1, "action": action, "body": body,
               "owner": owner if owner is not None else context["owner"],
               "token": token if token is not None else context["token"]}
    reader, writer = await asyncio.open_unix_connection(context["address"])
    try:
        writer.write(json.dumps(message).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


async def test_host_authenticates_then_admits_without_running_model(tmp_path):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox

    inbox = ExternalEventInbox()
    host = ExternalEventHost(inbox, scope=lambda: ("session", "main"), directory=tmp_path / "host")
    host.start()
    try:
        assert host.descriptor.stat().st_mode & 0o777 == 0o600
        assert host.descriptor.parent.stat().st_mode & 0o777 == 0o700
        bad = await request(host.descriptor, "ping", {}, token="wrong-capability")
        assert bad["status"] == "rejected"
        register = {"registration_id": "fixture-observer", "route_id": "fixture-route", "instruction": "Review tests"}
        assert (await request(host.descriptor, "register", register))["status"] == "accepted"
        response = await request(host.descriptor, "deliver", fixture_message()["body"])
        assert response["status"] == "accepted"
        assert inbox.take(busy=False, human_pending=False) is None
        assert (await request(host.descriptor, "deliver", fixture_message()["body"]))["status"] == "duplicate"
    finally:
        host.close()
    assert not host.descriptor.exists()


async def test_generation_change_refuses_old_capability(tmp_path):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox

    scope = ["session", "main"]
    host = ExternalEventHost(ExternalEventInbox(), scope=lambda: tuple(scope), directory=tmp_path / "host")
    host.start()
    try:
        old = json.loads(host.descriptor.read_text())
        scope[1] = "branch"
        assert (await request(host.descriptor, "ping", {}, owner=old["owner"]))["status"] == "stale-owner"
        host.rotate()
        assert (await request(host.descriptor, "ping", {}, owner=old["owner"], token=old["token"]))["status"] != "ok"
        assert (await request(host.descriptor, "ping", {}))["status"] == "ok"
    finally:
        host.close()


async def test_resume_needs_approval_and_rechecks_owner(tmp_path):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox

    scope, allowed = ["session", "main"], [False]

    async def approve(registration):
        assert registration == "fixture-observer"
        if allowed[0]:
            scope[1] = "changed-during-review"
        return allowed[0]

    inbox = ExternalEventInbox()
    inbox.register("fixture-observer", "fixture-route", "Review tests")
    host = ExternalEventHost(inbox, scope=lambda: tuple(scope), directory=tmp_path / "host", approve=approve)
    host.start()
    try:
        result = await request(host.descriptor, "resume", {"registration_id": "fixture-observer"})
        assert result["status"] == "rejected"
        allowed[0] = True
        result = await request(host.descriptor, "resume", {"registration_id": "fixture-observer"})
        assert result["status"] == "stale-owner"
    finally:
        host.close()


async def test_close_cancels_incomplete_connections(tmp_path):
    from xdog.coding.core.external_events.host import ExternalEventHost
    from xdog.coding.core.external_events.inbox import ExternalEventInbox

    host = ExternalEventHost(ExternalEventInbox(), scope=lambda: ("session", "main"), directory=tmp_path / "host")
    host.start()
    context = json.loads(host.descriptor.read_text())
    reader, writer = await asyncio.open_unix_connection(context["address"])
    writer.write(b'{"unfinished":')
    await writer.drain()
    started = time.monotonic()
    host.close()
    assert time.monotonic() - started < 2
    try:
        assert await asyncio.wait_for(reader.read(), 2) == b""
    except ConnectionResetError:
        pass  # Closing an unread Unix stream may reset rather than send a clean EOF.
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionResetError:
            pass
