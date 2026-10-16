"""Direct ACP endpoints work without knowing any harness or executable."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from acp import Agent, RequestError, run_agent
from acp.schema import AgentMessageChunk, InitializeResponse, LoadSessionResponse, PromptResponse, TextContentBlock

from packages.eyes.tests.test_acp_handler import delivery


@pytest.fixture
async def unix_peer():
    events, tasks, writers = [], set(), set()
    gate = asyncio.Event()
    gate.set()

    class Peer(Agent):
        def on_connect(self, conn):
            self.conn = conn

        async def initialize(self, protocol_version, **kwargs):
            events.append(('initialize', None))
            await gate.wait()
            return InitializeResponse.model_validate(
                {'protocolVersion': protocol_version, 'agentCapabilities': {'loadSession': True}}
            )

        async def load_session(self, session_id, cwd, mcp_servers=None, **kwargs):
            events.append(('load', (session_id, cwd, mcp_servers)))
            if session_id != 'target':
                raise RequestError(-32602, 'Unknown session')
            return LoadSessionResponse()

        async def prompt(self, session_id, prompt, **kwargs):
            events.append(('prompt', (session_id, prompt, kwargs)))
            await self.conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(session_update='agent_message_chunk',
                                         content=TextContentBlock(type='text', text='Done')),
            )
            return PromptResponse(stop_reason='end_turn')

        async def cancel(self, session_id, **kwargs):
            events.append(('cancel', session_id))

    async def accept(reader, writer):
        writers.add(writer)
        try:
            await run_agent(Peer(), writer, reader)
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    def connected(reader, writer):
        task = asyncio.create_task(accept(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    with tempfile.TemporaryDirectory(prefix='eyes-acp-') as directory:
        path = Path(directory) / 'peer.sock'
        server = await asyncio.start_unix_server(connected, path=str(path), limit=1024 * 1024)
        path.chmod(0o600)
        try:
            yield path, events, gate, writers
        finally:
            gate.set()
            server.close()
            await server.wait_closed()
            for writer in tuple(writers):
                writer.close()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tuple(tasks)), 3)


def handler_for(path, cwd, session='target'):
    from xdog.eyes.connections import UnixConnection
    from xdog.eyes.handlers.acp import AcpHandler
    from xdog.eyes.models import AcpHandlerDef

    return AcpHandler(AcpHandlerDef('handler', 'acp', UnixConnection(path), session, 'Review tests'), cwd=cwd)


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_unix_delivery_never_spawns_a_cli_and_closes_only_its_connection(unix_peer, tmp_path, monkeypatch):
    from xdog.eyes import acp_client

    path, events, gate, writers = unix_peer

    def forbidden(*args, **kwargs):
        raise AssertionError('Unix callback must not spawn any executable')

    monkeypatch.setattr(acp_client, 'spawn_agent_process', forbidden)
    handler = handler_for(path, tmp_path)
    try:
        await handler.start()
        assert handler.process is None
        assert (await handler.deliver(delivery())).status == 'succeeded'
        assert handler.client.updates == 1
        assert ('load', ('target', str(tmp_path), [])) in events
        prompt = next(data for kind, data in events if kind == 'prompt')
        assert prompt[0] == 'target'
        assert prompt[2]['xdog']['provenance']['delivery_id'] == 'd1'
        assert 'Untrusted evidence' in prompt[1][1].text
    finally:
        await handler.close()
    await wait_until(lambda: not writers)
    second = handler_for(path, tmp_path)
    try:
        await second.start()  # Closing peer one did not stop the service.
        assert (await second.deliver(delivery('d2'))).status == 'succeeded'
    finally:
        await second.close()


async def test_cancelled_handshake_releases_the_socket(unix_peer, tmp_path):
    path, events, gate, writers = unix_peer
    gate.clear()
    handler = handler_for(path, tmp_path)
    task = asyncio.create_task(handler.start())
    try:
        await wait_until(lambda: any(kind == 'initialize' for kind, data in events))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        await wait_until(lambda: not writers)
        assert handler.client.conn is None
    finally:
        gate.set()
        await handler.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_target_load_does_not_fall_back_to_another_session(unix_peer, tmp_path):
    path, events, gate, writers = unix_peer
    handler = handler_for(path, tmp_path, session='missing')
    try:
        with pytest.raises(RequestError, match='Unknown session'):
            await handler.start()
        await wait_until(lambda: not writers)
        assert not [event for event in events if event[0] == 'prompt']
    finally:
        await handler.close()


@pytest.mark.parametrize('kind', ['socket-mode', 'directory-mode', 'socket-link', 'directory-link', 'file', 'fifo'])
async def test_insecure_or_non_socket_target_is_rejected_before_connect(unix_peer, tmp_path, kind):
    path, events, gate, writers = unix_peer
    target = path
    original_mode = path.parent.stat().st_mode & 0o777
    try:
        if kind == 'socket-mode':
            path.chmod(0o666)
        elif kind == 'directory-mode':
            path.parent.chmod(0o755)
        elif kind == 'socket-link':
            target = path.parent / 'link'
            target.symlink_to(path)
        elif kind == 'directory-link':
            target = path.parent / 'linkdir' / path.name
            (path.parent / 'linkdir').symlink_to(path.parent, target_is_directory=True)
        elif kind == 'file':
            target = path.parent / 'file'
            target.touch(mode=0o600)
        elif kind == 'fifo':
            target = path.parent / 'fifo'
            os.mkfifo(target, 0o600)
        handler = handler_for(target, tmp_path)
        try:
            with pytest.raises(ValueError):
                await handler.start()
            assert not events
        finally:
            await handler.close()
    finally:
        path.chmod(0o600)
        path.parent.chmod(original_mode)
