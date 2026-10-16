"""ACP ingress routes to an existing execution owner; it never creates one."""
import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION, RequestError, connect_to_agent
from acp.schema import DeniedOutcome, RequestPermissionResponse, TextContentBlock

from packages.coding.tests.batch_helpers import single_input_execute


class Client:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))

    async def request_permission(self, **kwargs):
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome='cancelled'))


@asynccontextmanager
async def connected(address):
    client = Client()
    reader, writer = await asyncio.open_unix_connection(address, limit=1024*1024)
    conn = connect_to_agent(client, writer, reader)
    try:
        response = await conn.initialize(protocol_version=PROTOCOL_VERSION)
        yield conn, client, response
    finally:
        await conn.close()
        writer.close()
        await writer.wait_closed()


@pytest.fixture
async def ingress(tmp_path):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.session_manager import SessionManager

    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(working_dir=tmp_path)
    with tempfile.TemporaryDirectory(prefix='acp-in-') as short:
        config = IngressConfig(manager._dir, tmp_path/'inbox', Path(short)/'acp.sock')
        server = await IngressServer.start(config)
        try:
            yield server, config, manager, data
        finally:
            await server.aclose()


def payload(data, cwd, message_id='m1'):
    return {'session_id': data.session_id, 'cwd': str(cwd), 'message_id': message_id,
            'instruction': 'Review', 'evidence': [{'path':'a.py'}], 'source':'Eyes', 'provenance':{'route_id':'r'}}


async def test_ingress_cannot_create_a_hidden_agent(tmp_path):
    from xdog.agent.acp.contracts import Peer
    from xdog.coding.acp.ingress_backend import IngressBackend
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.ownership import OwnerRegistry, runtime_directory

    backend = IngressBackend(InboxStore(tmp_path/'sessions', tmp_path/'inbox'),
                             OwnerRegistry(runtime_directory(tmp_path/'sessions')))
    with pytest.raises(RequestError):
        await backend.new_session(tmp_path, Peer('peer', object()))


async def test_live_prompt_uses_the_existing_owner_and_leaves_it_open(coding_session, tmp_path):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    await runtime.open_endpoint()
    with tempfile.TemporaryDirectory(prefix='acp-route-') as short:
        server = await IngressServer.start(IngressConfig(coding_session.session_manager._dir,
                                                         tmp_path/'inbox', Path(short)/'acp.sock'))
        try:
            async with connected(server.config.address) as (conn, client, hello):
                await conn.load_session(session_id=coding_session.session_id, cwd=str(tmp_path), mcp_servers=[])
                result = await conn.prompt(session_id=coding_session.session_id,
                                           prompt=[TextContentBlock(type='text', text='Review')],
                                           xdog={'source':'Eyes', 'evidence':[{'path':'a.py'}]})
                assert result.stop_reason == 'end_turn'
                records = runtime.controller.store.records()
                assert len(records) == 1 and records[0].input.source == 'Eyes'
            await server.aclose()
            assert runtime.server.server.is_serving()
            assert not runtime.disposing
        finally:
            await server.aclose()
            await asyncio.wrap_future(runtime.close())


async def test_peer_close_does_not_cancel_the_active_human_turn(coding_session, tmp_path):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    await runtime.open_endpoint()
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(value):
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)

    runtime.controller.max_messages_per_turn = 1
    runtime.controller._execute_batch = single_input_execute(execute)
    human = runtime.controller.submit(SessionInput('human', 'human', 'Human', 'Work', (), {}), peer_id='local')
    await asyncio.wait_for(entered.wait(), 3)
    with tempfile.TemporaryDirectory(prefix='acp-human-') as short:
        server = await IngressServer.start(IngressConfig(coding_session.session_manager._dir,
                                                         tmp_path/'inbox', Path(short)/'acp.sock'))
        try:
            async with connected(server.config.address) as (conn, client, hello):
                await conn.load_session(session_id=coding_session.session_id, cwd=str(tmp_path), mcp_servers=[])
                pending = asyncio.create_task(conn.prompt(session_id=coding_session.session_id,
                    prompt=[TextContentBlock(type='text', text='Queued ACP work')]))
                async with asyncio.timeout(3):
                    while not runtime.inbox.store.records(coding_session.session_id, tmp_path):
                        await asyncio.sleep(0.01)
                await conn.cancel(session_id=coding_session.session_id)
                assert (await asyncio.wait_for(pending, 3)).stop_reason == 'cancelled'
                assert not human.result.done()
            release.set()
            assert (await asyncio.wrap_future(human.result)).status == 'succeeded'
        finally:
            release.set()
            await server.aclose()
            await asyncio.wrap_future(runtime.close())


async def test_owner_exit_leaves_ingress_available_for_durable_intake(coding_session, tmp_path):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    await runtime.open_endpoint()
    with tempfile.TemporaryDirectory(prefix='acp-exit-') as short:
        server = await IngressServer.start(IngressConfig(coding_session.session_manager._dir,
                                                         tmp_path/'inbox',Path(short)/'acp.sock'))
        try:
            async with connected(server.config.address) as (conn, client, hello):
                await conn.load_session(session_id=coding_session.session_id,cwd=str(tmp_path),mcp_servers=[])
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(runtime.close())), 2)
                receipt = await conn.ext_method('xdog/enqueue',payload(coding_session.session_data,tmp_path))
                assert receipt['status'] == 'queued'
                with pytest.raises(RequestError):
                    await conn.prompt(session_id=coding_session.session_id,
                                      prompt=[TextContentBlock(type='text',text='must not restart owner')])
                assert server.server.is_serving()
        finally:
            await server.aclose()
            await asyncio.wrap_future(runtime.close())


async def test_slow_consumer_has_a_bounded_forward_buffer_and_only_its_peer_is_closed(coding_session, tmp_path):
    from xdog.agent.acp.contracts import Peer
    from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextDeltaEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.acp.ingress_backend import IngressBackend
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    def stream(*args):
        message = AssistantMessage()
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)

        async def events():
            yield StartEvent(partial=message)
            for _ in range(400):
                yield TextDeltaEvent(delta='x'*4096,partial=message)
                await asyncio.sleep(0.001)
            yield DoneEvent(message=message)

        return EventStream.from_async_generator(events(),result_future=future)

    coding_session.agent.set_stream_fn(stream)
    runtime = SessionRuntime(coding_session,bound=False)
    runtime.start()
    await runtime.open_endpoint()
    backend = IngressBackend(InboxStore(coding_session.session_manager._dir,tmp_path/'inbox'),runtime.registry)
    peer = Peer('slow-peer',Client())

    async def no_history(update):
        pass

    async def blocked_consumer(update):
        await asyncio.Event().wait()

    try:
        ref = await backend.load_session(coding_session.session_id,tmp_path,peer,no_history)
        with pytest.raises(RuntimeError,match='capacity'):
            await asyncio.wait_for(backend.prompt(ref,peer,'Review',{},blocked_consumer),5)
        async with asyncio.timeout(3):
            while runtime.controller.busy:
                await asyncio.sleep(0.01)
        assert runtime.server.server.is_serving()
        assert not runtime.disposing
    finally:
        await backend.disconnect(peer)
        await asyncio.wrap_future(runtime.close())
