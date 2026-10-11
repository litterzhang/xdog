"""The silent compaction flush cannot regain tools excluded by its parent."""
from __future__ import annotations

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.compaction.flush_runner import FlushRunner

from packages.claw.tests.test_active_tool_persistence import close, create


@pytest.mark.parametrize('selection', [None, [], ['current_time']])
async def test_real_flush_model_and_executor_obey_parent_active_selection(tmp_path, selection):
    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('current_time', 'filesystem')
    contexts = []
    target = tmp_path / 'flush-target'
    def stream(model, context, options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id='write', name='filesystem', arguments={
            'action': 'write', 'path': str(target), 'content': 'flush change'}),)) if len(contexts) == 1 else
            AssistantMessage(content=(TextContent(text='flush done'),)))
        return EventStream.empty(message)
    runtime.stream_fn = stream
    try:
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        await session.select_active_tools(selection, expected_revision=session.agent.tool_selection_revision)
        await FlushRunner(runtime.model, session.agent.stream_fn).run([], '', session._compaction_tools())
        assert contexts
        if selection is None:
            assert target.read_text() == 'flush change'
            assert all('filesystem' in {tool.name for tool in context.tools or ()} for context in contexts)
        else:
            assert not target.exists()
            assert all([tool.name for tool in context.tools or ()] == selection for context in contexts)
        assert not session.agent.messages
    finally:
        await close(runtime)
