"""Private Claw socket elicitation consent, independent capability and host ownership."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_mcp_elicitation_registration import peer_config
from packages.agent.tests.test_mcp_sampling_executor import Adapter


async def setup(tmp_path, mode='form'):
    servers, log = peer_config(tmp_path)
    if mode == 'url':
        peer = tmp_path / 'peer.py'
        peer.write_text(peer.read_text().replace("'message':'Review name','requestedSchema':{'type':'object',\n"
            "                'properties':{'name':{'type':'string'}},'required':['name']}",
            "'mode':'url','message':'Review external interaction','elicitationId':'external',"
            "'url':'https://operator.example/flow'"))
    if mode == 'url':
        peer = tmp_path / 'peer.py'
        peer.write_text(peer.read_text().replace("    elif method=='ping':",
            '''    elif row.get('id')=='form' and 'result' in row:
        for key in ['foreign', 'external', 'external']:
            send({'jsonrpc':'2.0','method':'notifications/elicitation/complete','params':{'elicitationId':key}})
    elif method=='ping':'''))
    adapter = Adapter()
    config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=servers)
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('explicit-model', adapter)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    return server, config, runtime, session, adapter, log


async def receive(reader, kind):
    async with asyncio.timeout(5):
        while True:
            row = json.loads(await reader.readline())
            if row['type'] == kind:
                return row


@pytest.mark.parametrize('end', ['release', 'detach', 'withdraw', 'disconnect', 'foreign'])
async def test_actual_socket_elicitation_request_reply_and_connection_retirement(tmp_path, end):
    server, config, runtime, session, adapter, log = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    envelope = {'type': 'mcp_elicitation_control', 'group_id': 'main', 'session_id': session.meta.session_id,
                'view': 'a' * 32, 'server': 'owned'}
    deferred = []
    async def next_event():
        if deferred:
            return deferred.pop(0)
        return await receive(reader, 'mcp_elicitation_event')
    async def send(row, kind='mcp_elicitation_result', source=reader, target=writer):
        target.write((json.dumps(row) + '\n').encode())
        await target.drain()
        async with asyncio.timeout(5):
            while True:
                packet = json.loads(await source.readline())
                if packet['type'] == kind:
                    return packet
                if source is reader and packet['type'] == 'mcp_elicitation_event':
                    deferred.append(packet)
    def response(event, value):
        return {**{key: event[key] for key in ('id', 'phase', 'receipt', 'sha256')}, 'response': value}
    try:
        assert (await send({'type': 'ping', 'group_id': 'main'}, 'pong'))['mcp_elicitation'] == 0
        assert not (await send({**envelope, 'request_id': 'old', 'action': 'enable'}))['success']
        assert not runtime.mcp._elicitation
        assert (await send({'type': 'ping', 'group_id': 'main', 'mcp_elicitation': 1}, 'pong'))['mcp_elicitation'] == 1
        enabled = await send({**envelope, 'request_id': 'enable', 'action': 'enable'})
        assert enabled['success'], enabled
        assert not log.exists() and not adapter.calls
        await runtime.mcp.connect('owned')
        first = (await next_event())['event']
        assert first['phase'] == 'request'
        proposal = response(first, {'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED'}})
        if end == 'foreign':
            await send({'type': 'ping', 'group_id': 'main', 'mcp_elicitation': 1}, 'pong', other_reader, other_writer)
            result = await send({**envelope, 'request_id': 'foreign', 'action': 'answer', 'response': proposal},
                                source=other_reader, target=other_writer)
            assert not result['success']
            return
        assert (await send({**envelope, 'request_id': 'propose', 'action': 'answer', 'response': proposal}))['success']
        second = (await next_event())['event']
        assert second['phase'] == 'response'
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(row.get('id') == 'form' for row in rows)
        assert (await send({'type': 'ping', 'group_id': 'main'}, 'pong'))['type'] == 'pong'
        if end == 'release':
            proof = response(second, {'action': 'accept'})
            assert (await send({**envelope, 'request_id': 'release', 'action': 'answer', 'response': proof}))['success']
            async with asyncio.timeout(5):
                while runtime.mcp._clients['owned'].reverse._jobs:
                    await asyncio.sleep(.003)
            await runtime.mcp._bridge(runtime.mcp._clients['owned'].request('ping', {}))
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            assert next(row for row in rows if row.get('id') == 'form')['result'] == {
                'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED'}}
        else:
            if end == 'detach':
                assert (await send({**envelope, 'request_id': 'detach', 'action': 'detach'}))['success']
            elif end == 'withdraw':
                pong = await send({'type': 'ping', 'group_id': 'main', 'mcp_elicitation': 0}, 'pong')
                assert pong['mcp_elicitation'] == 0
            else:
                writer.close()
                await writer.wait_closed()
            async with asyncio.timeout(5):
                while runtime.mcp._elicitation:
                    await asyncio.sleep(.003)
            assert not runtime.mcp._clients['owned'].reverse._jobs
        assert not adapter.calls and not session.agent.messages
    finally:
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


@pytest.mark.parametrize('mode', ['form', 'url'])
async def test_actual_claw_tui_full_elicitation_reviews_keep_parent_draft_and_no_model(tmp_path, mode, monkeypatch):
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.keys import KeyEvent

    from packages.coding.tests.test_mcp_elicitation_ui import paint
    opened = []
    import xdog.tui.url_open as url_module
    def fake_open(url, alive, canceled):
        assert alive() and not canceled.is_set()
        opened.append(url)
        return 'Owned URL helper received exact reviewed URL'
    monkeypatch.setattr(url_module, 'open_reviewed_url', fake_open)
    server, config, runtime, session, adapter, log = await setup(tmp_path, mode)
    ui = ChatApp(config.socket_path)
    notices = []
    original_notice = ui._chat_log.add_system
    def notice(text):
        notices.append(text)
        original_notice(text)
    ui._chat_log.add_system = notice
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(5):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_elicitation_supported)
        ui._editor.set_text('PARENT UNSENT ELICITATION')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('f'))
        await wait(lambda: not panel._busy)
        assert runtime.mcp._elicitation and not log.exists()
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.elicitation_review is not None)
        review = panel.elicitation_review
        review.handle_input(KeyEvent('enter'))
        assert review._mode == 'request'
        paint(review)
        if mode == 'url':
            ui._tui.set_focus(ui._editor)
            ui._mcp_send('elicitation_open', 'owned', 'https://operator.example/flow', {})
            assert not opened and not ui._reviewed_url.busy
            ui._tui.set_focus(panel)
            paint(review)
            review.handle_input(KeyEvent('o'))
            await wait(lambda: not ui._reviewed_url.busy)
            review.handle_input(KeyEvent('o'))
            assert opened == ['https://operator.example/flow']
            assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
        review.handle_input(KeyEvent('enter'))
        if mode == 'form':
            assert review._mode == 'form'
            review.editor.set_text('{"name":"USER REVIEWED"}')
            review.handle_input(KeyEvent('enter'))
            paint(review)
            review.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.elicitation_review is not None
                   and panel.elicitation_review.event['phase'] == 'response')
        reply_review = panel.elicitation_review
        assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
        reply_review.handle_input(KeyEvent('enter'))
        assert panel.elicitation_review is reply_review
        paint(reply_review)
        reply_review.handle_input(KeyEvent('enter'))
        await wait(lambda: not runtime.mcp._clients['owned'].reverse._jobs)
        await runtime.mcp._bridge(runtime.mcp._clients['owned'].request('ping', {}))
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        reply = next(row for row in rows if row.get('id') == 'form')['result']
        assert reply == ({'action': 'accept', 'content': {'name': 'USER REVIEWED'}} if mode == 'form'
                         else {'action': 'accept'})
        if mode == 'url':
            await wait(lambda: any('Peer reports external interaction complete' in text for text in notices))
            assert sum('Peer reports external interaction complete' in text for text in notices) == 1
            assert sum('URL request acknowledged' in text for text in notices) == 1
            assert not runtime.mcp._oauth
        ui._close_mcp()
        assert ui._editor.get_text() == 'PARENT UNSENT ELICITATION'
        assert not adapter.calls and not session.agent.messages
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_connection_loss_permanently_disables_local_elicitation_review_without_send(tmp_path, monkeypatch):
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.keys import KeyEvent

    from packages.tui.tests.test_mcp_elicitation_panel import event
    ui = ChatApp(str(tmp_path / 'unused.sock'))
    ui._connection_ready = ui._mcp_supported = ui._mcp_elicitation_supported = True
    ui._gateway_session_id = 'owned'
    sent = []
    monkeypatch.setattr(ui, '_queue_request', sent.append)
    try:
        ui._open_mcp()
        panel = ui._mcp_panel
        panel.publish_elicitation(event())
        review = panel.elicitation_review
        sent.clear()
        ui._handle_response({'type': 'internal_error', 'message': 'Owned connection lost'})
        assert not ui._mcp_elicitation_supported and review._disabled
        review.focused = False
        review.focused = True
        review.render(80)
        review.handle_input(KeyEvent('enter'))
        assert not any(row.get('type') == 'mcp_elicitation_control' for row in sent)
    finally:
        ui._request_exit()
