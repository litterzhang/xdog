"""Production readiness/capacity recovery, not manual transport resumption."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path
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
        weixin_account_id="readiness-fixture", weixin_base_url="https://example.invalid",
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


@pytest.mark.parametrize("action", ["release", "pause", "shutdown"])
async def test_actual_acp_reminder_waits_for_committed_parent_and_safe_capacity_release(tmp_path, action):
    from xdog.claw.acp.server import ClawAcpServer
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        runtime.group = replace(runtime.group, config=replace(runtime.group.config, max_queued_messages=1))
        host, session = runtime.capabilities.get("goal"), runtime._active_session
        inputs = await app.input_service()
        scope = InputScope(runtime.group.id, runtime.workspace_dir)
        entered, release = asyncio.Event(), asyncio.Event()
        def stream(selected, context, options):
            original = model(selected, context, options)
            async def events():
                if not entered.is_set():
                    entered.set()
                    await release.wait()
                async for event in original:
                    yield event
            from xdog.ai.utils.event_stream import EventStream
            return EventStream.from_async_generator(events(), result_future=original._result_future)
        session.agent.set_stream_fn(stream)
        human = asyncio.create_task(app.route_message(UserInput(group_id="main", content="HOLD", channel="tui")))
        with tempfile.TemporaryDirectory(prefix='goal-capacity-') as short:
            server = await ClawAcpServer.start(app, Path(short) / 'a.sock')
            try:
                await asyncio.wait_for(entered.wait(), 3)
                goal = await host.service.create("Output", "Inspect actual output", ("Result exists",),
                                                context=OperationContext(True))
                await host.service.task({'id': 'work', 'title': 'Main work', 'expected_version': 0})
                await host._wake('capacity-fixture')
                await host.flush()
                assert host.host.execution_snapshot()['state'] == 'running'
                assert not await host.service.store.reminders()
                rows = await inputs.store.records(scope)
                assert not [r for r in rows if r.envelope.message.source == 'goal']
                if action == 'pause':
                    await host.service.change('pause', context=OperationContext(True), reason='User pause')
                elif action == 'shutdown':
                    app.request_stop()
                # The actual automatic turn completes the Goal. No worker/reviewer or polling runner.
                model.parent_action = lambda ctx: ({'action': 'complete', 'summary': 'Main verified reminder'}
                    if any(m.role == 'user' and 'Goal reminder for' in str(m.content) for m in ctx.messages) else None)
                release.set()
                await asyncio.wait_for(human, 3)
                if action != 'release':
                    await asyncio.sleep(.04)
                    assert not [r for r in await inputs.store.records(scope) if r.envelope.message.source == 'goal']
                    assert not await host.service.store.reminders()
                    return
                async def consumed():
                    records = await inputs.store.records(scope)
                    return any(r.envelope.message.source == 'goal' and r.status == 'succeeded' for r in records)
                # No Goal mutation, manual drain, new input or model polling after the release.
                await until(consumed)
                originals = [r for r in await inputs.store.records(scope) if r.envelope.message.source == 'goal']
                assert len(originals) == 1
                assert len(await inputs.store.attempts(scope, originals[0].envelope.message.id)) == 1
                reminders = await host.service.store.reminders()
                assert len(reminders) == 1 and reminders[0]['status'] == 'accepted'
                assert reminders[0]['id'] == originals[0].envelope.message.id
                assert (await host.service.status(goal.id))['current']['status'] == 'completed'
                assert not session.children.supervisor._children
            finally:
                release.set()
                await asyncio.gather(human, return_exceptions=True)
                await server.close()
