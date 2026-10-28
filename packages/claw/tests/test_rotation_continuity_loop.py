"""Offline host loop: confirmed scope -> blocked work -> rotation -> source verification.

No network, shell command, repository deletion or real model request is made.
"""
from __future__ import annotations

import datetime
import json

from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


async def test_continue_after_rotation_loads_scope_and_checks_exact_original_messages(tmp_path):
    phase = 'seed'
    stage = 0
    original_id = ''
    checked = []
    normal_turns = []
    maintenance_writes = 0

    def reply(text):
        return EventStream.empty(AssistantMessage(content=(TextContent(text=text),)))

    def stream(model, context, options):
        nonlocal stage, maintenance_writes
        if (isinstance(context.system_prompt, str)
                and context.system_prompt.startswith('You are a conversation summarizer.')):
            return reply('Only owner/one and owner/two were confirmed. delete_repo is missing; '
                         'authorization timed out; NO deletion executed. Next verify source and present auth state.')
        last = context.messages[-1]
        if isinstance(last, UserMessage) and 'silent maintenance turn' in str(last.content):
            maintenance_writes += 1
            return EventStream.empty(AssistantMessage(content=(ToolCall(
                id='daily', name='memory', arguments={'action': 'write', 'target': 'daily',
                    'text': '用户限定删除对象，权限不足不能扩大范围或声称删除成功。'}),)))
        if isinstance(last, ToolResultMessage) and last.tool_call_id == 'daily':
            return reply('Important decision saved.')
        if phase == 'seed':
            return reply('Login works, but delete_repo is missing and extra authorization timed out. '
                         'No deletion executed.')
        normal_turns.append(context)
        assert {tool.name for tool in context.tools or ()} <= {'memory', 'agent', 'goal'}
        assert any('Context handoff' in str(message.content) for message in context.messages
                   if isinstance(message, UserMessage))
        assert any('delete_repo' in str(message.content) and 'owner/one' in str(message.content)
                   for message in context.messages if isinstance(message, UserMessage))
        if stage == 0:
            stage += 1
            return EventStream.empty(AssistantMessage(content=(ToolCall(
                id='recent', name='memory', arguments={'action': 'history', 'mode': 'recent'}),)))
        if stage == 1:
            recent = json.loads(''.join(part.text for part in last.content if isinstance(part, TextContent)))
            assert any(row['session_id'] == original_id for row in recent['items'])
            checked.append('recent')
            stage += 1
            return EventStream.empty(AssistantMessage(content=(ToolCall(
                id='source', name='memory', arguments={'action': 'history', 'mode': 'get',
                                                     'session_id': original_id, 'position': 0}),)))
        source = json.loads(''.join(part.text for part in last.content if isinstance(part, TextContent)))
        assert source['session_id'] == original_id and source['role'] == 'user'
        assert 'owner/one' in source['text'] and 'owner/two' in source['text'] and '仅限这两个' in source['text']
        assert 'not instructions or tool permissions' in source['notice']
        checked.append('original_user_scope')
        return reply('已核实原始对象和范围；此前没有删除。仍须检查当前 delete_repo 权限，不能直接重放删除命令。')

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='offline', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=('memory',)))
    try:
        await app.start()
        await app.route_message(UserInput(group_id='main', content='确认删除 owner/one 和 owner/two，操作仅限这两个。'))
        runtime = app._runtimes['main']
        original_id = runtime.get_new_session_id()
        original = runtime.transcript_store.load_transcript(original_id)
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
        runtime.transcript_store._index['main']['created_at'] = old.isoformat()
        runtime.transcript_store._save_index()
        phase = 'continue'
        result = await app.route_message(UserInput(group_id='main', content='打开代理，继续删除。'))
        assert not result.error and '已核实原始对象' in result.response_text
        assert checked == ['recent', 'original_user_scope'] and maintenance_writes == 1
        assert runtime.get_new_session_id() != original_id
        assert runtime.transcript_store.load_transcript(original_id) == original
        daily = runtime.workspace_dir / 'memory' / f'{datetime.date.today().isoformat()}.md'
        assert '用户限定删除对象' in daily.read_text()
        current = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
        assert current[0]['data']['memory_status'] == 'checked'
        assert current[0]['data']['status'] == 'summarized'
        assert len(normal_turns) == 3
        assert 'Original chat' in system_prompt_text(normal_turns[0].system_prompt)
    finally:
        await app.stop()
