"""Extension message review dispatches the canonical producer only after delivery."""
from __future__ import annotations

import asyncio

from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


async def test_message_cancel_before_coroutine_admission_reports_single_settlement(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    controller.negotiate({'extension_messages': 1})
    try:
        controller._start_message({'action': 'message', 'message': 'Never admitted',
                                   'messages_generation': controller.messages_generation},
                                  (owner.session_id, 'request', 'scope'), nav.revision)
        controller.abort_prompt()
        await asyncio.gather(controller.prompt_task, return_exceptions=True)
        outcomes = [event for event in events if event['type'] == 'extension_message_result']
        assert len(outcomes) == 1 and outcomes[0]['canceled'] and outcomes[0]['settled']
        assert not calls and not owner.messages
    finally:
        await controller.aclose()
        await nav.aclose()
        await close(owner)
