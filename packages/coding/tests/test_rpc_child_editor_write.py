"""Original child-worker proposal capability, not parent composer/permission delegation."""
from __future__ import annotations

import asyncio

import pytest

from packages.coding.tests.test_rpc_children import close, control, packet, setup, wait


@pytest.mark.parametrize('ending', ['apply', 'deny', 'withdraw', 'old-client', 'error'])
async def test_live_original_child_worker_write_only_review_and_independent_generation(tmp_path, ending):
    session, adapter, nav, sources, children, events = setup(tmp_path, extension='editorwrite')
    task = None
    try:
        if ending != 'old-client':
            children.negotiate({'child_editor_write': 1})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(adapter.entered.is_set)
        runtime = session.children.extensions.owners[child]
        task = asyncio.create_task(runtime.tools()[0].execute('owned-proposal',
                                  {'mode': 'error' if ending == 'error' else 'ok'}))
        if ending != 'old-client':
            await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
            question = next(event for event in events if event['type'] == 'child_extension_dialog')
            assert question['dialog']['operation'] == 'propose'
            assert question['dialog']['editor_write']['child_id'] == child
            reply = packet(children, child_id=child, id=question['dialog']['id'],
                value={'approved': ending != 'deny', 'revision': 'a' * 64 if ending != 'deny' else ''})
            with pytest.raises(ValueError):
                children.answer_dialog({**reply, 'child_owner': 'foreign'})
            if ending == 'withdraw':
                children.negotiate({'child_editor_write': 0})
                children.negotiate({'child_editor_write': 1})
            else:
                children.answer_dialog(reply)
        result = await task
        await asyncio.sleep(.02)
        apply = [event for event in events if event['type'] == 'child_extension_editor_write_apply']
        assert len(apply) == int(ending == 'apply')
        if apply:
            assert apply[0]['editor_write']['child_id'] == child
            assert apply[0]['child_owner'] == children.lease and apply[0]['text'] == 'PROPOSED 界\nunsent'
        assert result.is_error == (ending == 'error')
        assert len(adapter.contexts) == 1 and not session.messages and session.permissions.mode == 'deny'
        assert not session.children.supervisor._children[child].agent.pending_inputs()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await close(session, nav, sources, children)
