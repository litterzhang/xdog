"""Verify captured output recovery with a wheel-only interpreter, never a provider.

Run outside the checkout with the isolated Claw environment's Python. This script
is also copied into the fresh Claw environment by verify_acp_artifacts.py.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.channel import WeixinChannel
from xdog.claw.channels.weixin.types import MessageItem, MessageItemType, MessageType, TextItem, WeixinMessage
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs import delivery as installed
from xdog.claw.core.outputs.delivery import DeliveryNotSent
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

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
    runtime = Orchestrator(ClawConfig(data_dir=str(root / "data")), model="test/dummy", stream_fn=stream)
    runtime.register_group(Group(id="main", workspace=str(root), enabled_tools=()))
    channel = WeixinChannel(root / "channel", "account", "https://example.invalid", "")
    await channel._api_client.close()
    api = API(reject)
    channel._api_client = api
    runtime.add_channel(channel)
    return runtime, channel, api


async def main():
    root = Path(tempfile.mkdtemp(prefix="installed-channel-output-"))
    runtime, channel, api = await app(root, model, True)
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
        await runtime.stop()
    runtime, channel, api = await app(root, no_model, False)
    channel._user_id_map["main"] = "different-person"
    try:
        outputs = await runtime.output_service()
        await outputs.resume()
        (message,) = api.accepted
        assert (message.to_user_id, message.context_token, message.client_id) == (
            "original",
            "original-context",
            identifier,
        )
        assert (await outputs.store.records(scope))[0].status == "delivered"
        await outputs.resume()
        assert len(api.accepted) == 1 and len(calls) == 1
        print(
            json.dumps(
                {
                    "installed_output": "same output ID, original peer/context, pending recovery, no model replay",
                    "model_calls": len(calls),
                    "root": str(root),
                }
            )
        )
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
