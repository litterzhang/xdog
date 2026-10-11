"""Canonical custom context survives summaries without opaque details or UI gating."""
from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.coding.core.compaction.compaction import compact_messages

from packages.agent.tests.test_context_messages import row


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('display', [False, True])
async def test_summary_projects_only_named_custom_content_and_provenance(host, display):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Reviewed summary'),)))
    messages = [CustomAgentMessage('xdog.extension_context.v1', row(display=display)),
                CustomAgentMessage('unrelated-metadata', {'text': 'DO NOT SUMMARIZE ARBITRARY METADATA'}),
                UserMessage(content='older'), AssistantMessage(content=(TextContent(text='reply'),)),
                UserMessage(content='recent')]
    if host == 'coding':
        agent = Agent(stream, config=AgentConfig(model='test/model'))
        await compact_messages(messages, agent, prepared_index=4)
    else:
        await Summarizer('test/model', stream).summarize(messages)
    assert len(calls) == 1
    text = str(calls[0].messages)
    assert 'EXPLICIT CUSTOM CONTENT' in text and 'fixture / review-note' in text
    assert 'NOT MODEL INPUT' not in text and 'DO NOT SUMMARIZE ARBITRARY METADATA' not in text
