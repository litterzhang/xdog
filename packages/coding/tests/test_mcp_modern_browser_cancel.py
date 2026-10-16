"""Closing native read views cancels their owned job before an unadmitted wire leg."""
from __future__ import annotations

import asyncio
import threading

import pytest

from packages.coding.tests.test_mcp_modern_browser_ui import NODE, SDK_ROOT, browser, records, wait

pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent SDK 2.3.1 required')


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_close_native_browser_settles_owned_read_without_wire_replay_or_late_publication(
    tmp_path, monkeypatch, host, transport,
):
    async with browser(tmp_path, host, transport) as (ui, manager, adapter, agent, log):
        ui._editor.set_text('UNSUBMITTED CANCELED BROWSER')
        ui._open_mcp()
        await wait(ui, lambda: ui._mcp_panel is not None and not ui._mcp_panel._busy, log)
        ui._mcp_send('connect', 'modern', '', {})
        await wait(ui, lambda: manager.status()[0]['state'] == 'connected' and not ui._mcp_panel._busy, log)
        client = manager._clients['modern']
        entered, settled = threading.Event(), threading.Event()
        original = client.request

        async def held(method, params, **kwargs):
            if method == 'resources/read':
                entered.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    settled.set()
            return await original(method, params, **kwargs)

        monkeypatch.setattr(client, 'request', held)
        ui._mcp_send('read', 'modern', 'test://roots', {})
        await wait(ui, entered.is_set, log)
        ui._close_mcp()
        await wait(ui, settled.is_set, log)
        assert ui._mcp_panel is None and ui._mcp_document is None
        assert not agent.messages and not adapter.calls
        assert ui._editor.get_text() == 'UNSUBMITTED CANCELED BROWSER'
        assert not [row for row in records(log) if row['type'] == 'operation']
        assert not client.inputs._jobs
        # A round-trip on the existing connection proves settlement, not just
        # checking before an old result could arrive and resurrect the panel.
        assert (await manager._bridge(original('tools/list', {})))['tools']
        await asyncio.sleep(.02)
        ui._poll()
        assert ui._mcp_panel is None and ui._mcp_document is None
