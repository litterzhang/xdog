"""Actual wheel-only observer streaming/recovery; only model/network are scripted.

Run outside the checkout with the isolated Claw+Eyes+ACP interpreter. Never imports
repository tests or runtime source, uses user credentials, or calls a provider.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from xdog.agent.capabilities import CapabilitySet
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.channel import WeixinChannel, _TypingTicketCache
from xdog.claw.channels.weixin.types import MessageItem, MessageItemType, MessageType, TextItem, WeixinMessage
from xdog.claw.config import ClawConfig
from xdog.claw.core.outputs.delivery import DeliveryNotSent
from xdog.claw.core.runtime.application_ports import ApplicationPorts
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.eyes.hosting import manager as installed
from xdog.eyes.managed_scheduler import SchedulerClocks

assert Path(installed.__file__).is_relative_to(Path(sys.prefix))


class Clock:
    def __init__(self):
        self.wall = datetime(2026, 10, 19, tzinfo=UTC).timestamp()
        self.tick = asyncio.Event()

    async def sleep(self, _seconds):
        await self.tick.wait()

    def advance(self):
        self.wall += 60
        old, self.tick = self.tick, asyncio.Event()
        old.set()

    def clocks(self):
        return SchedulerClocks(wall=lambda: self.wall, monotonic=lambda: self.wall, sleep=self.sleep)


class API(WeixinApiClient):
    def __init__(self):
        super().__init__("https://example.invalid", "")
        self.sent = asyncio.Queue()
        self.reject = False

    async def send_message(self, request):
        if self.reject:
            raise DeliveryNotSent("explicit boundary: original recipient has not accepted output")
        self.sent.put_nowait(request.msg)

    async def get_config(self, *args, **kwargs):
        return {}

    async def send_typing(self, *args, **kwargs):
        pass


class Model:
    def __init__(self, clock):
        self.clock = clock
        self.phase = "create"
        self.creation_calls = 0
        self.callback_calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __call__(self, _model, context, options=None):
        blocked = False
        if self.phase == "create" and self.creation_calls == 0:
            source = {"type": "scheduler", "schedule": {
                "at": datetime.fromtimestamp(self.clock.wall + 60, UTC).isoformat()}}
            parts = (ToolCall(id="create", name="eyes", arguments={"action": "create", "name": "wheel-watch",
                     "source": source, "instruction": "Recall the original delegated check and report"}),)
            self.creation_calls += 1
        elif self.phase == "callback":
            first = self.callback_calls == 0
            self.callback_calls += 1
            parts = ((TextContent(text="first wheel observer message"),
                      ToolCall(id="inspect", name="eyes", arguments={"action": "list"})) if first else
                     (TextContent(text="second wheel observer message"),))
            blocked = not first
        else:
            parts = (TextContent(text="ordinary ready"),)
        message = AssistantMessage(content=parts,
            stop_reason="toolUse" if any(isinstance(part, ToolCall) for part in parts) else "stop")

        async def events():
            if blocked:
                self.entered.set()
                await self.release.wait()
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


def no_model(*args, **kwargs):
    raise AssertionError("observer output transport recovery reran the Agent")


@asynccontextmanager
async def application(root, clock, stream, *, peer=None):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = Orchestrator(ClawConfig(data_dir=str(root / "data"), enabled_tools=("eyes",)),
                       model="test/dummy", stream_fn=stream, defer_inputs=True)
    app.register_group(Group(id="main", workspace=str(root / "work"), enabled_tools=("eyes",)))
    channel = WeixinChannel(root / "channel", "account", "https://example.invalid", "")
    await channel._api_client.close()
    api = API()
    channel._api_client = api
    channel._typing_tickets = _TypingTicketCache(api)
    if peer is not None:
        channel._user_id_map["main"] = peer
    app.add_channel(channel)
    server = await ClawAcpServer.start(app, root / "acp.sock")
    capabilities = CapabilitySet('application')
    await capabilities.start(ApplicationPorts(app, root / "acp.sock"))
    app.application_capabilities = capabilities
    manager = capabilities.get('eyes')  # Acceptance diagnostics, never host execution policy.
    assert manager is not None
    manager.clocks = clock.clocks()
    await app.refresh_application_capabilities()
    (await app.input_service()).resume()
    try:
        yield app, manager, channel, api
    finally:
        await capabilities.aclose()
        await server.close()
        await app.stop()


async def speak(channel, identifier, sender):
    message = WeixinMessage(message_id=identifier, from_user_id=sender, context_token="ctx:" + sender,
        message_type=MessageType.USER,
        item_list=(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="Watch for me")),))
    await channel._accept_inbound(message)
    await channel._on_inbound(message)


async def until(operation):
    async with asyncio.timeout(5):
        while True:
            result = await operation()
            if result:
                return result
            await asyncio.sleep(0.005)


async def prepare(app, manager, channel, api, clock, model):
    await speak(channel, 1, "alice")
    (created,) = await manager.list("main")
    model.phase = "ordinary"
    await speak(channel, 2, "bob")
    while not api.sent.empty():
        api.sent.get_nowait()
    model.phase = "callback"
    return created["id"]


async def outputs_for(app, message_id):
    return [row for row in await (await app.output_service()).store.records()
            if row.envelope.message.id == message_id]


def assert_message(message, text, output_id=None):
    assert (message.to_user_id, message.context_token, message.item_list[0].text_item.text) == (
        "alice", "ctx:alice", text)
    if output_id is not None:
        assert message.client_id == output_id


async def streaming(root, stop):
    clock = Clock()
    model = Model(clock)
    async with application(root, clock, model) as (app, manager, channel, api):
        identifier = await prepare(app, manager, channel, api, clock, model)
        clock.advance()
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            first = await asyncio.wait_for(api.sent.get(), 5)
            assert_message(first, "first wheel observer message")
            current = await manager.status("main", identifier)
            (occurrence,) = current["occurrences"]
            assert occurrence["execution_status"] == "running" and not model.release.is_set()
            if stop == "notifications":
                await manager.cancel_notifications("main", identifier)
            elif stop == "source":
                await manager.cancel("main", identifier)
            model.release.set()

            async def completed():
                current = await manager.status("main", identifier)
                (row,) = current["occurrences"]
                wanted = "cancelled" if stop == "notifications" else "delivered"
                return row if row["execution_status"] == "succeeded" and row["delivery_status"] == wanted else None

            occurrence = await until(completed)
            rows = await outputs_for(app, occurrence["message_id"])
            expected = ["delivered", "cancelled" if stop == "notifications" else "delivered"]
            assert [row.status for row in rows] == expected
            assert_message(first, rows[0].text, rows[0].id)
            if stop != "notifications":
                second = await asyncio.wait_for(api.sent.get(), 5)
                assert_message(second, rows[1].text, rows[1].id)
                assert second.client_id != first.client_id
            assert api.sent.empty() and model.callback_calls == 2
        finally:
            model.release.set()


async def recovery(root):
    clock = Clock()
    model = Model(clock)
    async with application(root, clock, model) as (app, manager, channel, api):
        identifier = await prepare(app, manager, channel, api, clock, model)
        api.reject = True
        model.release.set()
        clock.advance()

        async def pending():
            current = await manager.status("main", identifier)
            rows = current["occurrences"]
            if rows and rows[0]["execution_status"] == "succeeded" and rows[0]["delivery_status"] == "pending":
                outputs = await outputs_for(app, rows[0]["message_id"])
                return outputs if len(outputs) == 2 and all(row.status == "pending" for row in outputs) else None
            return None

        originals = await until(pending)
        assert api.sent.empty() and model.callback_calls == 2
    async with application(root, clock, no_model, peer="new-peer-must-not-receive") as (app, manager, channel, api):
        for original in originals:
            received = await asyncio.wait_for(api.sent.get(), 5)
            assert_message(received, original.text, original.id)
        await (await app.output_service()).resume()
        rows = await outputs_for(app, originals[0].envelope.message.id)
        assert [row.id for row in rows] == [row.id for row in originals]
        assert all(row.status == "delivered" for row in rows)
        assert api.sent.empty() and model.callback_calls == 2


async def main():
    root = Path(tempfile.mkdtemp(prefix="installed-observer-output-"))
    for stop in ("none", "notifications", "source"):
        await streaming(root / stop, stop)
    await recovery(root / "restart")
    print(json.dumps({"installed_observer_outputs": "per-message before turn end, explicit cancel, source stop",
        "recovery": "original peer/context/output IDs, durable pending, no model replay", "root": str(root)}))


if __name__ == "__main__":
    asyncio.run(main())
