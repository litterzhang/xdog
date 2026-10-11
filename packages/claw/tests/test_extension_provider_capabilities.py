from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

ALIAS = 'ext-providercaps-local/gpt-5.1/code:preview'


async def test_claw_catalog_compound_id_prompt_budget_and_independent_child_reasoning_metadata(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model=ALIAS, data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'caps.sock'), pid_file=str(tmp_path / 'caps.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providercaps',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(ALIAS, base)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        ui._handle_submit('/model')
        await wait(lambda: ui._extension_model_pending is None)
        assert ALIAS in '\n'.join(ui._chat_log.render(160))
        runtime = server._orchestrator._runtimes['main']
        session = runtime._active_session
        assert session.agent.max_prompt_tokens == runtime.max_prompt_tokens == 30000
        assert session.agent.context_window == runtime.context_window == 48000
        model = session.extensions.provider_model(ALIAS)
        assert model.supported_efforts == ('low', 'high') and model.vision_limits.max_prompt_images == 1
        checked = []
        def inspect_budget(turn_count, estimate, context_window):
            checked.append(context_window)
            return False
        with patch('xdog.claw.core.runtime.session.should_compact', inspect_budget):
            await session._maybe_compact()
        assert checked == [30000]  # Prompt ceiling, not the larger 48k total context window.
        ui._handle_submit('metadata-routed turn')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert 'CAPABILITY-PROVIDER-ANSWER' in '\n'.join(ui._chat_log.render(160)) and not calls
        child = await session.children.supervisor.start('native metadata child', config=ChildConfig(
            model=ALIAS, thinking='high', max_model_calls=1).to_dict())
        record = await session.children.supervisor.control('wait', child)
        assert record['status'] == 'completed' and record['effective']['max_prompt_tokens'] == 30000
        assert record['effective']['thinking'] == 'high' and record['calls'] == 1
        assert await session.extensions.command('ext-providercaps-count', '') == 'CALLS=1' and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
