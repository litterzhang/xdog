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


async def test_runtime_extension_message_uses_its_batch_scope_and_single_projection(tmp_path):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.session_events import RpcSessionEvents

    owner,calls = session(tmp_path)
    events = []
    nav = RpcNavigation(owner,events.append)
    extensions = RpcExtensions(owner,events.append,navigation=nav)
    extensions.negotiate({'extension_messages':1})
    runtime = SessionRuntime(owner,bound=True)
    runtime.start(setup_pending=True)
    observer = RpcSessionEvents(owner,runtime,extensions,events.append)
    observer.ready()
    try:
        await runtime.activate_inputs()
        extensions._start_message({'action':'message','message':'one reviewed follow-up',
            'messages_generation':extensions.messages_generation},(owner.session_id,'control','old-scope'),nav.revision)
        await asyncio.wait_for(extensions.prompt_task,3)
        assert len(calls)==1, 'a follow-up must not collide with a caller-owned extension scope'
        starts = [event for event in events if event['type']=='message_start']
        ends = [event for event in events if event['type']=='message_end']
        assert len(starts)==len(ends)==1
        assert starts[0]['batch_id']==ends[0]['batch_id'] and len(ends[0]['request_ids'])==1
        assert not any(event['type']=='error' for event in events)
        assert extensions.scope is None
    finally:
        observer.close()
        await extensions.aclose()
        await nav.aclose()
        await asyncio.wrap_future(runtime.close())
