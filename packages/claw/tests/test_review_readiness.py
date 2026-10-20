"""Production readiness/capacity recovery, not manual transport resumption."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs.delivery import DeliveryNotSent
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput
from xdog.skills.goal.host import OperationContext

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_goal_host import activate, application
from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts, text


async def until(check):
    async with asyncio.timeout(5):
        while not await check():
            await asyncio.sleep(0.005)


async def test_gateway_restart_recovers_original_weixin_output_after_channel_ready_without_eyes(tmp_path):
    class API(ChannelApi):
        def __init__(self, reject):
            super().__init__()
            self.reject = reject

        async def send_message(self, request):
            if self.reject:
                raise DeliveryNotSent("Known not sent at the network boundary")
            await super().send_message(request)

    config = ClawConfig(
        data_dir=str(tmp_path / "data"), socket_path=str(tmp_path / "gateway.sock"),
        pid_file=str(tmp_path / "gateway.pid"), weixin_enabled=True, weixin_token="fixture-only",
        weixin_account_id="readiness-fixture", weixin_base_url="https://example.invalid", eyes_enabled=False,
        groups=(GroupDef(id="main", workspace=str(tmp_path / "workspace"), is_main=True),),
    )
    model, first = ScriptedModel(), API(True)
    server = GatewayServer(config)
    scope = InputScope("main", tmp_path / "workspace")
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", model)), \
         patch("xdog.claw.channels.weixin.channel.WeixinApiClient", return_value=first):
        try:
            await server.start()
            app = server._orchestrator
            channel, = app._channels
            await channel._accept_inbound(text(1, "original", "work"))
            inputs = await app.input_service()
            row, = await inputs.store.records(scope)
            assert (await inputs.wait(scope, row.envelope.message.id)).status == "succeeded"
            output, = await (await app.output_service()).store.records(scope)
            assert output.status == "pending"
            identifier = output.id
        finally:
            await server.stop()

    def no_model(*args, **kwargs):
        raise AssertionError("Transport recovery must not rerun the Agent")

    second, server = API(False), GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", no_model)), \
         patch("xdog.claw.channels.weixin.channel.WeixinApiClient", return_value=second):
        try:
            await server.start()  # Actual channel registration/connect happens AFTER application.start().
            app = server._orchestrator
            channel, = app._channels
            channel._user_id_map["main"] = "wrong-peer"
            message = await asyncio.wait_for(second.sent.get(), 2)
            assert sent_parts(message) == ("original", "ctx:original", "Scheduled report")
            assert message.client_id == identifier
            outputs = await app.output_service()

            async def delivered():
                return (await outputs.store.records(scope))[0].status == "delivered"

            await until(delivered)
            await asyncio.sleep(0.03)
            assert second.sent.empty() and model.calls == ["work"]
        finally:
            await server.stop()


@pytest.mark.parametrize("action", ["release", "cancel-notifications", "shutdown"])
async def test_actual_child_notices_retry_when_parent_input_releases_capacity(tmp_path, action):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        runtime.group = replace(runtime.group, config=replace(runtime.group.config, max_queued_messages=1))
        host, session = runtime.goal_host, runtime._active_session
        goal = await host.service.create("Output", "Inspect actual output", ("Result exists",),
                                         context=OperationContext(True))
        goal = await host.service.add_task(goal.id, "Read result")
        entered, release = asyncio.Event(), asyncio.Event()

        def stream(selected, context, options):
            original = model(selected, context, options)
            role = model.calls[-1][0]

            async def events():
                if role == "parent" and not entered.is_set():
                    entered.set()
                    await release.wait()
                async for event in original:
                    yield event

            from xdog.ai.utils.event_stream import EventStream
            return EventStream.from_async_generator(events(), result_future=original._result_future)

        # Keep the genuine parent turn/receipt occupying the sole Inbox slot.
        # Child model streams still use the actual supervised goal_context/read/report path.
        session.agent.set_stream_fn(stream)
        human = asyncio.create_task(app.route_message(UserInput(group_id="main", content="HOLD", channel="tui")))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            goal = await host.service.assign(goal.id, goal.tasks[0].id, "Read and report")
            await session.children.supervisor.control("wait", goal.tasks[0].attempts[-1].worker.id)
            await host.flush()
            assert (await host.service.status(goal.id)).tasks[0].state == "submitted"
            notices = await host.service.store.notices()
            assert notices and all(row.status == "unknown" for row in notices)
            identifiers = {row.notice.id for row in notices}
            assert all([await host.inputs.store.get(host.scope, identifier) is None for identifier in identifiers])
            if action == "cancel-notifications":
                await host.service.cancel_notifications(goal.id)
                await host.flush()
            elif action == "shutdown":
                app.request_stop()
            release.set()
            await asyncio.wait_for(human, 3)
            if action != "release":
                await asyncio.sleep(0.04)
                assert all([await host.inputs.store.get(host.scope, identifier) is None for identifier in identifiers])
                return

            async def consumed():
                rows = await host.inputs.store.records(host.scope)
                return identifiers <= {row.envelope.message.id for row in rows if row.status == "succeeded"}

            # No Goal mutation, manual drain/flush, new input or model polling after capacity release.
            await until(consumed)
            rows = await host.inputs.store.records(host.scope)
            originals = [row for row in rows if row.envelope.message.id in identifiers]
            assert len(originals) == len(identifiers)
            assert all([len(await host.inputs.store.attempts(host.scope, row.envelope.message.id)) == 1
                        for row in originals])
            assert all(row.status == "accepted" for row in await host.service.store.notices())
        finally:
            release.set()
            await asyncio.gather(human, return_exceptions=True)
