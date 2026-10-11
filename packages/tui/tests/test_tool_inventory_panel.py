from __future__ import annotations

import asyncio

from xdog.tui.components.tool_inventory import ToolInventoryPanel, inventory_records
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


async def test_full_schema_search_metadata_not_approval_and_chrome_does_not_rewrite_user_data():
    snapshot = {'model': 'model', 'api_enabled': False, 'tools': [{'name': 'probe',
        'description': 'Transcript (unchanged) Loading transcript',
        'parameters': {'type': 'object', 'properties': {'value': {'enum': [False, 0, None]}}}}]}
    records = inventory_records(snapshot)
    panel = ToolInventoryPanel(lambda: records, lambda: None, lambda: None)
    try:
        panel.publish_inventory(records)
        panel.render(80)
        async with asyncio.timeout(3):
            while not panel.settled:
                panel.poll()
                await asyncio.sleep(0.003)
        assert 'NOT execution approval' in panel.records[0].body and 'disabled' in panel.records[0].body
        assert 'Transcript (unchanged) Loading transcript' in panel.records[1].body
        panel.search('false,0,null')
        async with asyncio.timeout(3):
            while not panel.settled:
                panel.poll()
                await asyncio.sleep(0.003)
        assert panel.browser.snapshot().matches == 1
        for height in (0, 1, 4, 12):
            panel.set_render_budget(height)
            for width in (0, 1, 8, 23, 80):
                lines = panel.render(width)
                assert len(lines) <= height and all(visible_width(line) <= width for line in lines)
        panel.handle_input(KeyEvent('/'))
        panel.handle_paste('literal')
        assert panel.searching
    finally:
        panel.close()


def test_active_selection_and_legacy_unknown_are_not_execution_approval():
    base = {'model': 'model', 'api_enabled': False,
            'tools': [{'name': name, 'description': 'metadata', 'parameters': {}} for name in ('selected', 'excluded')]}
    records = inventory_records({**base, 'active_tools': ['selected'], 'selection_revision': 3,
                                 'selection_mode': 'explicit'})
    assert 'selected 1 / 2 configured' in records[0].body and 'revision 3' in records[0].body
    assert 'disabled' in records[0].body and 'NOT execution approval' in records[0].body
    assert 'Selection: selected' in records[1].body and 'Selection: excluded' in records[2].body
    legacy = inventory_records(base)
    assert all('unknown (legacy snapshot)' in row.body for row in legacy)
