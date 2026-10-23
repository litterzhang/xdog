"""A native display disconnect is not a successful durable message delivery."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.outbound_messages import OutboundMessages
from xdog.claw.core.types import Group, UserInput


@pytest.mark.parametrize('failure', ['write', 'drain'])
async def test_native_send_transport_error_is_unknown_not_delivered_or_replayed(tmp_path, failure):
    class Endpoint:
        def __init__(self):
            self.pong = asyncio.Event()
            self.closed = False
            self.sending = False
            self.errors = 0

        def get_extra_info(self, name, default=None):
            return default

        def is_closing(self):
            return self.closed

        def write(self, data):
            row = json.loads(data)
            self.sending = row['type'] == 'notification'
            if self.sending and failure == 'write':
                self.errors += 1
                raise BrokenPipeError('native notification socket failed')
            if row['type'] == 'pong':
                self.pong.set()

        async def drain(self):
            if self.sending and failure == 'drain':
                self.errors += 1
                raise ConnectionResetError('native notification acknowledgement lost')

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    config = ClawConfig(data_dir=str(tmp_path / 'data'))
    app = Orchestrator(config, model='offline/model', stream_fn=lambda *_: pytest.fail('No model call'))
    app.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=()))
    gateway = GatewayServer(config)
    gateway._orchestrator = app
    reader, writer = asyncio.StreamReader(), Endpoint()
    handling = asyncio.create_task(gateway._handle_client(reader, writer))
    inputs = await InputStore.open(tmp_path / 'original-input.sqlite3')
    try:
        reader.feed_data(b'{"type":"ping","group_id":"main"}\n')
        await asyncio.wait_for(writer.pong.wait(), 3)
        envelope = freeze_input(UserInput(group_id='main', channel='acp', content='admitted input'),
                                tmp_path, message_id='original')
        await inputs.enqueue(envelope)
        claimed = await inputs.claim(envelope.scope)
        record = await inputs.start(claimed, session_id='original-session')
        outputs = await app.output_service()
        port = OutboundMessages(record, app._channel_replies, outputs, lambda: True)
        destination, = (await port.channels())['channels']
        assert destination['name'] == 'tui'
        receipt = await port.send('send-once', destination['id'], 'only one channel')
        assert writer.errors == 1
        assert receipt['status'] == 'unknown', 'Native transport swallowed a real send error'
        row, = await outputs.store.records(envelope.scope)
        assert row.status == 'unknown'
        await outputs.resume()
        assert await port.send('send-once', destination['id'], 'only one channel') == receipt
        assert writer.errors == 1, 'An unknown effect was replayed'
    finally:
        reader.feed_eof()
        await asyncio.wait_for(handling, 5)
        await inputs.close()
        await app.stop()
