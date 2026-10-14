"""Both hosts persist narrowed inventory before acknowledged lifecycle completion."""
from __future__ import annotations

import pytest

from packages.claw.tests.test_active_tool_persistence import close as close_claw
from packages.claw.tests.test_active_tool_persistence import create as create_claw
from packages.coding.tests.test_active_tool_persistence import close as close_coding
from packages.coding.tests.test_active_tool_persistence import create as create_coding


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_prepared_navigation_commits_only_available_selected_names(tmp_path, host):
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
            target = store.create_session('main')
            store.replace_transcript(target.session_id, messages_to_transcript([UserMessage(content='target')]))
            store.save_active_tools(target.session_id, ('missing', 'current_time'))
            store.activate_session('main', original)
            await asyncio.to_thread(runtime.navigate_session, 'resume', target.session_id, '', -1, original)
            assert runtime._active_session.agent.active_tool_selection == ('current_time',)
            assert store.get_active_tools(target.session_id) == ('current_time',)
        assert not calls
    finally:
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))
