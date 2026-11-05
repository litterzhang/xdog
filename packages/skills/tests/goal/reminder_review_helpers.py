"""Bounded race regressions for reminder coalescing, without runtime implementation edits."""
from __future__ import annotations

from xdog.skills.goal.host import OperationContext

USER = OperationContext(True)


async def receipt_states(monkeypatch):
    import xdog.agent.acp.client as client
    states = {}

    async def status(target, identifier=None):
        return {'messages': [{'message_id': identifier, 'status': states.get(identifier, 'succeeded')}]}

    monkeypatch.setattr(client, 'inbox_status', status)
    return states
