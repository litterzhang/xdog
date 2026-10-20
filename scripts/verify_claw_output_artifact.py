"""Verify captured output recovery with a wheel-only interpreter, never a provider.

Run outside the checkout with the isolated Claw environment's Python. This script
is also copied into the fresh Claw environment by verify_acp_artifacts.py.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.types import MessageItem, MessageItemType, MessageType, TextItem, WeixinMessage
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs import delivery as installed
from xdog.claw.core.outputs.delivery import DeliveryNotSent
from xdog.claw.core.runtime.gateway import GatewayServer

assert Path(installed.__file__).is_relative_to(Path(sys.prefix))


class API(WeixinApiClient):
    def __init__(self, reject):
        super().__init__("https://example.invalid", "")
        self.reject = reject
        self.accepted = []

    async def send_message(self, request):
        if self.reject:
            raise DeliveryNotSent("explicit network-boundary rejection")
        self.accepted.append(request.msg)

    async def get_updates(self, *args, **kwargs):
        await asyncio.Event().wait()  # No real network polling, including startup.

    async def get_config(self, *args, **kwargs):
        return {}

    async def send_typing(self, *args, **kwargs):
        pass


calls = []


def model(_model, context, options=None):
    calls.append(context)
    message = AssistantMessage(content=(TextContent(text="wheel output"),))

    async def events():
        yield StartEvent(partial=message)
        yield DoneEvent(message=message)

    future = asyncio.get_running_loop().create_future()
    future.set_result(message)
    return EventStream.from_async_generator(events(), result_future=future)


def no_model(*args, **kwargs):
    raise AssertionError("output recovery reran the Agent")


async def app(root, stream, reject):
    config = ClawConfig(
        data_dir=str(root / "data"), socket_path=str(root / "gateway.sock"), pid_file=str(root / "gateway.pid"),
        weixin_enabled=True, weixin_account_id="account", weixin_token="fixture-only",
        weixin_base_url="https://example.invalid", eyes_enabled=False,
        groups=(GroupDef(id="main", workspace=str(root), is_main=True),),
    )
    server, api = GatewayServer(config), API(reject)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", stream)), \
         patch("xdog.claw.channels.weixin.channel.WeixinApiClient", return_value=api):
        await server.start()
    return server, server._orchestrator, server._orchestrator._channels[0], api


async def main():
    root = Path(tempfile.mkdtemp(prefix="installed-channel-output-"))
    server, runtime, channel, api = await app(root, model, True)
    try:
        inbound = WeixinMessage(
            message_id=1,
            from_user_id="original",
            context_token="original-context",
            message_type=MessageType.USER,
            item_list=(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="work")),),
        )
        await channel._accept_inbound(inbound)
        service = await runtime.input_service()
        scope = InputScope("main", root)
        record = (await service.store.records(scope))[0]
        result = await asyncio.wait_for(service.wait(scope, record.envelope.message.id), 5)
        assert result.status == "succeeded" and result.response == "wheel output"
        output = (await (await runtime.output_service()).store.records(scope))[0]
        assert output.status == "pending"
        identifier = output.id
    finally:
        await server.stop()
    server, runtime, channel, api = await app(root, no_model, False)
    channel._user_id_map["main"] = "different-person"
    try:
        outputs = await runtime.output_service()
        async with asyncio.timeout(5):
            while not api.accepted or (await outputs.store.records(scope))[0].status != "delivered":
                await asyncio.sleep(0.005)
        (message,) = api.accepted
        assert (message.to_user_id, message.context_token, message.client_id) == (
            "original",
            "original-context",
            identifier,
        )
        assert (await outputs.store.records(scope))[0].status == "delivered"
        runtime.channels_ready()
        runtime.channels_ready()
        await asyncio.sleep(0.03)
        assert len(api.accepted) == 1 and len(calls) == 1
        print(
            json.dumps(
                {
                    "installed_output": (
                        "real Gateway startup, Eyes disabled, same output ID, original peer/context, "
                        "no manual resume/model replay"
                    ),
                    "model_calls": len(calls),
                    "root": str(root),
                }
            )
        )
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
