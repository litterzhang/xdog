"""Reviewed extension messages use canonical Claw turns, never the unsent composer."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from unittest.mock import patch

from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent, KeyEventType

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
WORKER = '''import json

def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        queued = await api.request_message(options.get('text', '/reset literal requested text'),
                                           timeout=options.get('timeout', 300))
        after = await api.open_sessions() if options.get('after') else None
        twice = await api.request_message('second message') if options.get('twice') else None
        compact = await api.request_compaction() if options.get('compact') else None
        if options.get('fail'):
            raise RuntimeError('Callback failed after message approval')
        return 'MESSAGE-QUEUED ' + json.dumps({'queued': queued, 'after': after, 'twice': twice, 'compact': compact})

    async def forbidden(params, context):
        return 'ILLEGAL-MESSAGE-' + str(await api.request_message('Tool must not borrow parent owner'))

    api.command('request', 'Request reviewed host message', command)
    api.tool('forbidden', 'Cannot borrow message authority', {'type': 'object'}, forbidden)
'''


@asynccontextmanager
async def message_server(tmp_path, stream=None, *, allow_tools=False):
    root = tmp_path / 'extensions' / 'messagerequest'
    root.mkdir(parents=True)
    (root / 'extension.yaml').write_text('name: messagerequest\nentry_point: main\n')
    (root / 'main.py').write_text(WORKER)
    calls = []
    def default_stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='REQUEST-MODEL-DONE'),)))
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('messagerequest',), extension_dir=str(root.parent),
        extension_allow_tools=('messagerequest',) if allow_tools else ())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', stream or default_stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    try:
        yield server, runtime, owner, config, calls
    finally:
        await asyncio.wait_for(server.stop(), 10)


@asynccontextmanager
async def native_ui(config):
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._extension_message_supported)
        yield ui, wait
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()


def review(panel, approve=True):
    assert panel.dialog['operation'] == 'message'
    if approve:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(160):
        panel.render(80)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter'))
