"""Modern native browser cancellation cannot resume an unreviewed outer request."""
from __future__ import annotations

import asyncio

import pytest
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_mcp_modern_browser_ui import NODE, SDK_ROOT, browser, records, wait

pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent SDK 2.3.1 required')


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('method', ['resources/read', 'prompts/get'])
@pytest.mark.parametrize('family', ['sampling', 'form', 'url'])
async def test_close_native_browser_during_modern_review_cancels_outer_job_without_resume(
    tmp_path, host, method, family,
):
    async with browser(tmp_path, host, 'stdio') as (ui, manager, adapter, agent, log):
        ui._editor.set_text('UNSUBMITTED CANCELED BROWSER')
        ui._open_mcp()
        await wait(ui, lambda: ui._mcp_panel is not None and not ui._mcp_panel._busy, log)
        panel = ui._mcp_panel
        for key in ('b', 'f'):
            panel.handle_input(KeyEvent(key))
            await wait(ui, lambda: not panel._busy, log)
        panel.handle_input(KeyEvent('c'))
        await wait(ui, lambda: not panel._busy and manager.status()[0]['state'] == 'connected', log)
        client = manager._clients['modern']
        panel.handle_input(KeyEvent('u' if method == 'resources/read' else 'p'))
        await wait(ui, lambda: not panel._busy and panel._mode == 'catalog', log)
        name = 'test://' + family if method == 'resources/read' else family
        panel._selected = next(index for index, row in enumerate(panel._visible()) if row['name'] == name)
        panel.handle_input(KeyEvent('enter'))
        if method == 'prompts/get':
            panel.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: panel.sampling_review is not None or panel.elicitation_review is not None, log)
        review = panel.sampling_review if family == 'sampling' else panel.elicitation_review
        assert review.event['protocol'] == '2026-07-28' and not adapter.calls
        assert len([row for row in records(log) if row['type'] == 'operation']) == 1
        ui._close_mcp()  # Actual host closes/revokes the original view, not a mocked owner predicate.
        await wait(ui, lambda: not manager._sampling and not manager._elicitation and not client.inputs._jobs, log)
        assert ui._mcp_panel is None and ui._mcp_document is None
        assert not agent.messages and not adapter.calls
        assert ui._editor.get_text() == 'UNSUBMITTED CANCELED BROWSER'
        # Same connected modern peer remains usable; a subsequent catalog reply
        # proves that cleanup settled, rather than merely checking before a late resume.
        result = await manager._bridge(client.request('tools/list', {}))
        assert result['tools']
        await asyncio.sleep(.02)
        ui._poll()
        operations = [row for row in records(log) if row['type'] == 'operation']
        assert len(operations) == 1 and 'responses' not in operations[0]
        assert ui._mcp_panel is None and ui._mcp_document is None
