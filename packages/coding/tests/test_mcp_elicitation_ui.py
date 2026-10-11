"""Actual Coding elicitation request/form/reply consent, no provider or implicit URL navigation."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_elicitation_registration import peer_config
from packages.agent.tests.test_mcp_sampling_executor import Adapter


async def wait(ui, predicate):
    async with asyncio.timeout(5):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def paint(panel):
    for _ in range(400):
        panel.render(80)
        if panel._review._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Elicitation review never fully painted')


@pytest.mark.parametrize('mode', ['form', 'url'])
@pytest.mark.parametrize('end', ['release', 'close', 'decline'])
async def test_coding_elicitation_actual_peer_full_reviews_preserve_parent_and_permissions(
    tmp_path, mode, end, monkeypatch,
):
    opened = []
    import xdog.tui.url_open as url_module
    def fake_open(url, alive, canceled):
        assert alive() and not canceled.is_set()
        opened.append(url)
        return 'Owned URL helper received exact reviewed URL'
    monkeypatch.setattr(url_module, 'open_reviewed_url', fake_open)
    configs, log = peer_config(tmp_path)
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
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_servers': configs}))
    sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
        session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
        session_manager=sessions, settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    ui = InteractiveMode(session)
    notices = []
    original_notice = ui._chat_log.add_system
    def notice(text):
        notices.append(text)
        original_notice(text)
    ui._chat_log.add_system = notice
    try:
        ui._editor.set_text('PARENT UNSENT ELICITATION')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        assert not session.mcp._elicitation and not log.exists()
        panel.handle_input(KeyEvent('f'))
        await wait(ui, lambda: not panel._busy)
        assert session.mcp._elicitation and not log.exists()
        await session.manage_mcp('connect', 'owned')
        await wait(ui, lambda: panel.elicitation_review is not None)
        review = panel.elicitation_review
        review.handle_input(KeyEvent('enter'))
        assert review._mode == 'request' and not adapter.calls
        if end == 'decline':
            review.handle_input(KeyEvent('d', ctrl=True))
        else:
            paint(review)
            if mode == 'url':
                ui._tui.set_focus(ui._editor)
                ui._mcp_send('elicitation_open', 'owned', 'https://operator.example/flow', {})
                assert not opened and not ui._reviewed_url.busy
                ui._tui.set_focus(panel)
                paint(review)
                review.handle_input(KeyEvent('o'))
                await wait(ui, lambda: not ui._reviewed_url.busy)
                review.handle_input(KeyEvent('o'))
                assert opened == ['https://operator.example/flow']
                assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
            review.handle_input(KeyEvent('enter'))
            if mode == 'form':
                assert review._mode == 'form'
                review.editor.set_text('{"name":"PRIVATE REVIEWED FORM"}')
                review.handle_input(KeyEvent('enter'))
                assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
                paint(review)
                review.handle_input(KeyEvent('enter'))
            await wait(ui, lambda: panel.elicitation_review is not None
                       and panel.elicitation_review.event['phase'] == 'response')
            response = panel.elicitation_review
            rows = [json.loads(row) for row in log.read_text().splitlines()]
            assert not any(row.get('id') == 'form' for row in rows)
            if end == 'close':
                ui._close_mcp()
                await wait(ui, lambda: not session.mcp._elicitation)
            else:
                response.handle_input(KeyEvent('enter'))
                assert panel.elicitation_review is response
                paint(response)
                response.handle_input(KeyEvent('enter'))
        if end != 'close':
            await wait(ui, lambda: not session.mcp._clients['owned'].reverse._jobs)
            await session.mcp._bridge(session.mcp._clients['owned'].request('ping', {}))
            rows = [json.loads(row) for row in log.read_text().splitlines()]
            returned = next(row for row in rows if row.get('id') == 'form')['result']
            assert returned == ({'action': 'decline'} if end == 'decline' else
                {'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED FORM'}} if mode == 'form' else
                {'action': 'accept'})
        if mode == 'url' and end == 'release':
            await wait(ui, lambda: any('Peer reports external interaction complete' in text for text in notices))
            assert sum('Peer reports external interaction complete' in text for text in notices) == 1
            assert sum('URL request acknowledged' in text for text in notices) == 1
            assert not session.mcp._oauth
        elif mode == 'url':
            assert not any('Peer reports external interaction complete' in text for text in notices)
        assert not adapter.calls and not session.messages and session.permissions.mode == 'deny'
        assert ui._editor.get_text() == 'PARENT UNSENT ELICITATION'
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
