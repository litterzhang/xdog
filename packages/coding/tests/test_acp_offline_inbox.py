"""Offline session identities receive durable data with no model or owner."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest
from acp import RequestError

from packages.coding.tests.test_acp_ingress import connected, payload
from packages.coding.tests.test_acp_ingress import ingress as ingress_fixture

ingress = ingress_fixture


async def test_enqueue_without_load_or_owner_is_durable_and_does_not_construct_agent(ingress, tmp_path, monkeypatch):
    from xdog.coding.core import sdk
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.sessions.ownership import lease_directory, runtime_directory

    server, config, manager, data = ingress

    def forbidden(*args, **kwargs):
        raise AssertionError('ACP intake must not construct an AgentSession/model')

    monkeypatch.setattr(sdk, 'create_agent_session', forbidden)
    monkeypatch.setattr(AgentSession, '__post_init__', forbidden)
    original_history = manager._session_path(data.session_id).read_bytes()
    async with connected(config.address) as (conn, client, hello):
        assert hello.field_meta['xdog']['extensions']['enqueue'] == 1
        result = await conn.ext_method('xdog/enqueue', payload(data, tmp_path))
        assert result['status'] == 'queued' and result['message_state'] == 'pending'
        duplicate = await conn.ext_method('xdog/enqueue', payload(data, tmp_path))
        assert duplicate['status'] == 'duplicate'
        status = await conn.ext_method('xdog/inbox_status', {'session_id':data.session_id, 'cwd':str(tmp_path)})
        assert status['messages'][0]['message_id'] == 'm1'
        assert status['messages'][0]['status'] == 'pending'
        assert 'evidence' not in status['messages'][0]
        assert not client.updates
        with pytest.raises(RequestError):
            await conn.load_session(session_id=data.session_id, cwd=str(tmp_path), mcp_servers=[])
    assert len(list(config.inbox_dir.rglob('*.json'))) == 1
    assert manager._session_path(data.session_id).read_bytes() == original_history
    owner_dir = runtime_directory(manager._dir)
    assert not (lease_directory(manager._dir)/(data.session_id+'.lock')).exists()
    assert not (owner_dir/(data.session_id+'.json')).exists()


@pytest.mark.parametrize('field,value', [('origin','human'),('permission','allow-all'),('branch_id','other'),
                                         ('approved',True),('owner_generation','g'),('inbox_dir','/elsewhere')])
async def test_enqueue_cannot_set_control_or_storage_fields(ingress, tmp_path, field, value):
    server, config, manager, data = ingress
    request = payload(data, tmp_path)
    request[field] = value
    async with connected(config.address) as (conn, client, hello):
        with pytest.raises(RequestError):
            await conn.ext_method('xdog/enqueue', request)
    assert not list(config.inbox_dir.rglob('*.json'))


async def test_lost_enqueue_ack_can_be_queried_and_retried_by_the_same_id(ingress, tmp_path):
    server, config, manager, data = ingress
    request = payload(data, tmp_path)
    async with connected(config.address) as (conn, client, hello):
        # Write raw request and deliberately discard the transport before reading a response.
        raw = {'jsonrpc':'2.0','id':100,'method':'_xdog/enqueue','params':request}
        reader, writer = await asyncio.open_unix_connection(config.address)
        writer.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
                                 'params':{'protocolVersion':1}}).encode()+b'\n')
        await writer.drain()
        await reader.readline()
        writer.write(json.dumps(raw).encode()+b'\n')
        await writer.drain()
        async with asyncio.timeout(3):
            while not list(config.inbox_dir.rglob('*.json')):
                await asyncio.sleep(0.01)
        writer.close()  # Committed, but the receipt was never read.
        await writer.wait_closed()
        result = await conn.ext_method('xdog/enqueue', request)
        assert result['status'] == 'duplicate'
        selected = await conn.ext_method('xdog/inbox_status',
            {'session_id':data.session_id,'cwd':str(tmp_path),'message_id':'m1'})
        assert len(selected['messages']) == 1
        with pytest.raises(RequestError):
            await conn.ext_method('xdog/enqueue', {**request,'instruction':'changed after lost ACK'})


async def test_standalone_service_module_accepts_offline_data_without_agent_imports(tmp_path):
    from xdog.coding.core.session_manager import SessionManager

    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(working_dir=tmp_path)
    script = '''
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in {'xdog.coding.core.agent_session','xdog.coding.core.sdk'}:
   raise AssertionError('offline ACP ingress must not import agent session construction')
sys.meta_path.insert(0,Block())
from xdog.coding.acp.ingress_main import main
main()
'''
    with tempfile.TemporaryDirectory(prefix='acp-module-') as short:
        address = Path(short)/'acp.sock'
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', script,
            '--sessions-dir',str(manager._dir),'--inbox-dir',str(tmp_path/'inbox'),'--socket',str(address),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            ready = await asyncio.wait_for(process.stdout.readline(), 10)
            assert ready, (await process.stderr.read()).decode()
            assert json.loads(ready)['address'] == str(address)
            async with connected(address) as (conn, client, hello):
                result = await conn.ext_method('xdog/enqueue',payload(data,tmp_path))
                assert result['status'] == 'queued'
            process.terminate()
            assert await asyncio.wait_for(process.wait(), 5) == 0
            assert not address.exists()
            assert len(list((tmp_path/'inbox').rglob('*.json'))) == 1
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_status_is_bounded_paginated_and_checks_selection_shape(ingress, tmp_path):
    server, config, manager, data = ingress
    async with connected(config.address) as (conn, client, hello):
        for index in range(65):
            await conn.ext_method('xdog/enqueue', payload(data,tmp_path,f'm{index:03}'))
        base = {'session_id':data.session_id,'cwd':str(tmp_path)}
        page = await conn.ext_method('xdog/inbox_status',base)
        assert len(page['messages']) == 64 and page['next_cursor'] == 64
        second = await conn.ext_method('xdog/inbox_status',{**base,'cursor':64})
        assert [r['message_id'] for r in second['messages']] == ['m064']
        assert second['next_cursor'] is None
        for invalid in ({'cursor':True},{'cursor':4097},{'cursor':0,'message_id':'m000'},{'cwd':'relative'}):
            with pytest.raises(RequestError):
                await conn.ext_method('xdog/inbox_status',{**base,**invalid})


async def test_service_shutdown_settles_an_inflight_durable_writer(ingress, tmp_path, monkeypatch):
    import threading

    from xdog.coding.core.sessions.inbox_store import InboxStore

    server, config, manager, data = ingress
    entered, release = threading.Event(), threading.Event()
    original = InboxStore.enqueue

    def delayed(self, message):
        entered.set()
        release.wait(5)
        return original(self,message)

    monkeypatch.setattr(InboxStore,'enqueue',delayed)
    closing = request = None
    try:
        async with connected(config.address) as (conn,client,hello):
            request = asyncio.create_task(conn.ext_method('xdog/enqueue',payload(data,tmp_path)))
            assert await asyncio.to_thread(entered.wait,3)
            closing = asyncio.create_task(server.aclose())
            await asyncio.sleep(0.02)
            assert not closing.done(), 'service must settle an accepted writer before releasing its lease'
            release.set()
            await asyncio.wait_for(closing,3)
    finally:
        release.set()
        await asyncio.gather(*(t for t in (request,closing) if t is not None),return_exceptions=True)
    assert len(list(config.inbox_dir.rglob('*.json'))) == 1
