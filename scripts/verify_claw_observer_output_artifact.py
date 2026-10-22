"""Wheel-only Eyes CLI/tool -> ordinary ACP/Core intake, without report routing.

The historical filename is retained for the artifact verifier entry point. Eyes
owns no chat-output tracking; separate Core output probes verify user delivery.
Only an offline model is scripted. Every daemon/data/socket path is temporary.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from xdog.agent.capabilities import ApplicationPort
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


async def cli(*arguments: str) -> dict:
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'xdog.eyes', *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(process.communicate(), 35)
    assert process.returncode == 0, (output, error)
    return json.loads(output)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='eyes-wheel-') as temporary:
        root = Path(temporary)
        os.environ['XDOG_EYES_RUNTIME_DIR'] = str(root / 'daemon')
        calls = []
        def model(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage(content=(TextContent(text='ordinary ACP response'),)))
        app = Orchestrator(ClawConfig(data_dir=str(root / 'data'), enabled_tools=('eyes',)),
                           model='test/dummy', stream_fn=model, defer_inputs=True)
        workspace = root / 'workspace'
        app.register_group(Group(id='main', workspace=str(workspace), enabled_tools=('eyes',)))
        server = await ClawAcpServer.start(app, root / 'input.sock')
        owner = 'artifact:explicit-owner'
        try:
            await app.application_capabilities.start(ApplicationPort())
            tool = next(tool for tool in app.application_capabilities.tools if tool.name == 'eyes')
            assert not calls
            config = {'version': 2, 'name': 'wheel-file-event',
                'sources': [{'id': 'files', 'type': 'file', 'root': '.', 'include': ['*.txt'],
                             'poll_interval_ms': 50}],
                'detectors': [{'id': 'changed', 'type': 'file.change', 'source': 'files',
                               'debounce_ms': 50, 'max_batch_delay_ms': 100}],
                'handlers': [{'id': 'receive', 'type': 'acp', 'delivery': 'enqueue',
                    'connection': {'transport': 'unix', 'path': str(server.path)},
                    'session': 'group:main', 'instruction': 'Read the requested file evidence.'}],
                'routes': [{'id': 'route', 'detector': 'changed', 'handler': 'receive',
                            'event_types': ['file.created']}]}
            path = workspace / 'eyes.json'
            path.write_text(json.dumps(config))
            result = await tool.execute('artifact-create', {'action': 'create', 'owner': owner,
                                                          'definition_path': str(path)})
            envelope = json.loads(''.join(part.text for part in result.content))
            assert envelope['exit_code'] == 0, envelope
            created = json.loads(envelope['stdout'])
            identifier = created['registration_id']
            await cli('--owner', owner, 'status', identifier, '--wait-ready', '--timeout', '10')
            inputs = await app.input_service()
            scope = InputScope('main', workspace)
            (workspace / 'evidence.txt').write_text('wheel-only source change')
            async with asyncio.timeout(15):
                while not (rows := await inputs.store.records(scope)):
                    await asyncio.sleep(.025)
            assert len(rows) == 1 and rows[0].status == 'pending' and not calls
            original_id = rows[0].envelope.message.id
            # Remove the entire application binding: the shared daemon and its
            # original registration remain independently inspectable via CLI.
            await app.application_capabilities.aclose()
            assert (await cli('--owner', owner, 'status', identifier))['registration_id'] == identifier
            await cli('--owner', owner, 'cancel', identifier)
            assert (await inputs.store.get(scope, original_id)).status == 'pending'
            inputs.resume()
            record = await asyncio.wait_for(inputs.wait(scope, original_id), 15)
            assert record.status == 'succeeded' and len(calls) == 1
            assert record.envelope.reply is None and record.envelope.message.origin == 'acp'
            assert not (root / 'data' / 'observers').exists()
            print(json.dumps({'eyes': 'CLI-backed installed tool, explicit owner, JSON-relative source, cwdless ACP',
                'shared_daemon_survives_binding_close': True, 'source_cancel_does_not_withdraw_input': True,
                'ordinary_main_agent_execution': True, 'implicit_chat_reply': False}))
        finally:
            await server.close()
            await app.stop()
            await cli('daemon', 'stop')


if __name__ == '__main__':
    asyncio.run(main())
