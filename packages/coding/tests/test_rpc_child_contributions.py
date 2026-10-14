"""Child declarative worker contributions never borrow parent contribution authority."""
from __future__ import annotations

import pytest

from packages.coding.tests.test_rpc_children import close, control, setup


@pytest.mark.parametrize('change', ['withdraw', 'reenable', 'revision', 'controls', 'child-close'])
async def test_queued_child_contributions_refuse_changed_generation_owner_and_closed_child(tmp_path, change):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        children.negotiate({'child_contributions': 1})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        generation, owner, binding = children.contribution_generation, children.bound, children.binding
        children._contribution_pending += 1
        if change == 'withdraw':
            children.negotiate({'child_contributions': 0})
        elif change == 'reenable':
            children.negotiate({'child_contributions': 0})
            children.negotiate({'child_contributions': 1})
        elif change == 'revision':
            nav.refresh_revision()
        elif change == 'controls':
            children.negotiate({'child_controls': 0})
        else:
            await control(children, 'cancel', 'cancel', child_id=child)
            await control(children, 'close', 'close', child_id=child)
        children._deliver_contribution(owner, binding, generation, child, {'text': 'OLD'})
        assert not any(event['type'] == 'child_contribution' for event in events)
    finally:
        await close(session, nav, sources, children)
