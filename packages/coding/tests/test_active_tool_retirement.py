"""Both hosts persist narrowed inventory before acknowledged lifecycle completion."""
from __future__ import annotations

import pytest

from packages.claw.tests.test_active_tool_persistence import close as close_claw
from packages.claw.tests.test_active_tool_persistence import create as create_claw
from packages.coding.tests.test_active_tool_persistence import close as close_coding
from packages.coding.tests.test_active_tool_persistence import create as create_coding


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_navigation_preserves_inventory_or_rejects_claw_route_switch(tmp_path, host):
    import asyncio

    from xdog.ai.types import AssistantMessage, UserMessage
    runtime = None
    if host == 'coding':
        owner, calls = await create_coding(tmp_path)
    else:
        runtime, calls = create_claw(tmp_path)
        owner = runtime.get_or_create_session(allow_reset=False)
    try:
        await owner.initialize_extensions()
        if host == 'coding':
            target = owner.session_manager.create_session(model='offline/model', working_dir=tmp_path)
            target.active_tools = ('missing', 'current_time')
            target.messages = [UserMessage(content='target'), AssistantMessage()]
            owner.session_manager.save_session(target)
            assert await asyncio.to_thread(owner.switch_session, target.session_id)
            assert owner.agent.active_tool_selection == ('current_time',)
            assert owner.session_manager.load_session(owner.session_id).active_tools == ('current_time',)
        else:
            from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
            store = runtime.transcript_store
            original = owner.meta.session_id
            target = 'historical-target'
            store._atomic(store._path(target), messages_to_transcript([UserMessage(content='target')]), jsonl=True)
            store._catalog['sessions'][target] = {**store._catalog['sessions'][original], 'session_id': target}
            store._save_catalog()
            store.save_active_tools(target, ('missing', 'current_time'))
            selected = owner.agent.active_tool_selection
            with pytest.raises(ValueError, match='read-only'):
                await asyncio.to_thread(runtime.navigate_session, 'resume', target, '', -1, original)
            assert runtime._active_session is owner and owner.agent.active_tool_selection == selected
            assert store.get_active_session('main').session_id == original
            assert store.get_active_tools(target) == ('missing', 'current_time')  # Read-only archive, never adopted.
            assert not hasattr(store, 'activate_session')
        assert not calls
    finally:
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))
