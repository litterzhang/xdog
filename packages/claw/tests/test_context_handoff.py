"""Rotation is an atomic handoff, never a fresh grant or a replay of old work."""
from __future__ import annotations

import asyncio
import datetime

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.claw.core.persistence.handoff import TYPE, capture, project, text
from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group


def seed(store):
    meta = store.create_session('main')
    rows = [
        {'role': 'user', 'content': '确认删除 GitHub 仓库 owner/one 和 owner/two，仅限这两个。'},
        {'role': 'assistant', 'content': '登录有效，但缺少 delete_repo；追加授权超时。尚未执行删除。'},
        {'role': 'user', 'content': '我准备使用 proxy_on，稍后继续。'},
    ]
    store.replace_transcript(meta.session_id, rows)
    return meta, rows


def test_fallback_is_durable_before_the_new_context_pointer_and_survives_reload(tmp_path):
    store = TranscriptStore(tmp_path, owner_group='main')
    old, rows = seed(store)
    new = store.reset_session('main', reason='idle_reset')
    reloaded = TranscriptStore(tmp_path, owner_group='main')
    entry = reloaded.load_transcript(new.session_id)[0]
    assert entry['role'] == 'custom' and entry['type'] == TYPE
    data = entry['data']
    assert data['source_session_id'] == old.session_id
    assert data['target_session_id'] == new.session_id and data['reason'] == 'idle_reset'
    assert data['status'] == 'recent_only'
    assert reloaded.load_transcript(old.session_id) == rows
    message = transcript_to_messages([entry])[0]
    assert isinstance(message, CustomAgentMessage)
    projected = project(message)
    assert isinstance(projected, UserMessage)
    assert 'owner/one' in projected.content and 'delete_repo' in projected.content
    assert '尚未执行删除' in projected.content and 'NOT a human message or a permission grant' in projected.content
    assert 'INCOMPLETE HANDOFF' in projected.content
    catalog = next(row for row in reloaded.list_sessions('main') if row['session_id'] == new.session_id)
    assert catalog['previous_session_id'] == old.session_id
    assert catalog['handoff']['reason'] == 'idle_reset'
    assert catalog['handoff']['created_at'] == new.created_at


def test_failed_handoff_commit_retains_the_original_pointer_and_evidence(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, owner_group='main')
    old, rows = seed(store)
    original = store._atomic
    def fail(path, *args, **kwargs):
        if path == store._index_file:
            raise OSError('injected handoff pointer failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(store, '_atomic', fail)
    with pytest.raises(OSError, match='handoff pointer'):
        store.reset_session('main')
    assert store.get_active_session('main').session_id == old.session_id
    assert store.load_transcript(old.session_id) == rows
    assert len(store.list_sessions('main')) == 1
    assert TranscriptStore(tmp_path, owner_group='main').get_active_session('main').session_id == old.session_id


def test_late_summary_cannot_discard_a_changed_source(tmp_path):
    store = TranscriptStore(tmp_path)
    old, rows = seed(store)
    draft = capture(old.session_id, rows, 'daily_reset').summarized('Pending deletion, no operation completed.')
    store.append_turn(old.session_id, {'role': 'user', 'content': '停止，暂时不要删除。'})
    with pytest.raises(ValueError, match='source changed'):
        store.reset_session('main', handoff=draft)
    assert store.get_active_session('main').session_id == old.session_id


def test_repeated_fallback_rotation_is_bounded_and_keeps_source_links(tmp_path):
    store = TranscriptStore(tmp_path)
    old, _ = seed(store)
    previous = old
    for _ in range(25):
        current = store.reset_session('main')
        data = store.load_transcript(current.session_id)[0]['data']
        assert data['source_session_id'] == previous.session_id
        assert len(text(data)) < 18000
        assert 'owner/one' in text(data) and 'delete_repo' in text(data)
        assert data['recent'][0]['session_id'] == old.session_id
        previous = current


@pytest.mark.parametrize('failure', [False, True])
async def test_real_owner_rotation_loads_summary_or_fallback_without_replaying_tools(tmp_path, failure):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if (isinstance(context.system_prompt, str)
                and context.system_prompt.startswith('You are a conversation summarizer.')):
            if failure:
                raise RuntimeError('summary unavailable')
            answer = ('Scope: owner/one, owner/two. Missing delete_repo. No deletions. '
                      'Next: proxy_on then verify authorization.')
        else:
            answer = 'Current user turn only.'
        return EventStream.empty(AssistantMessage(content=(TextContent(text=answer),)))
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path,
                           model='offline', stream_fn=stream, summarizer=Summarizer('offline', stream))
    old, rows = seed(runtime.transcript_store)
    previous = await runtime.prepare_session(allow_reset=False)
    now = datetime.datetime.now(datetime.timezone.utc)
    runtime.transcript_store._index['main']['created_at'] = (now - datetime.timedelta(days=3)).isoformat()
    runtime.transcript_store._save_index()
    try:
        current = await runtime.prepare_session()
        assert current is not previous and current.meta.session_id != old.session_id
        current._restore_messages()
        assert isinstance(current.agent.messages[0], CustomAgentMessage)
        data = current.agent.messages[0].data
        assert data['status'] == ('recent_only' if failure else 'summarized')
        assert data['reason'] == 'daily_reset'
        projected = await current._extension_context(list(current.agent.messages))
        assert 'owner/one' in projected[0].content and 'delete_repo' in projected[0].content
        assert not current._user_operation_authorized
        assert runtime.transcript_store.load_transcript(old.session_id) == rows
        assert len(calls) == 1 and not calls[0].tools
    finally:
        await runtime.aclose()


async def test_handoff_survives_a_later_compaction_summary(tmp_path):
    store = TranscriptStore(tmp_path)
    seed(store)
    meta = store.reset_session('main')
    observed = []
    def stream(model, context, options):
        observed.append(context)
        return EventStream.empty(AssistantMessage(
            content=(TextContent(text='Retained scope and missing permission.'),)))
    await Summarizer('offline', stream).summarize(transcript_to_messages(store.load_transcript(meta.session_id)))
    assert any('owner/one' in str(message.content) for message in observed[0].messages)


async def test_cancelled_handoff_does_not_drop_the_original_live_owner(tmp_path, monkeypatch):
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(Summarizer, 'summarize', blocked)
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path,
                           model='offline', stream_fn=lambda *_: EventStream.empty(AssistantMessage()),
                           summarizer=Summarizer('offline'))
    old, rows = seed(runtime.transcript_store)
    original = await runtime.prepare_session(allow_reset=False)
    work = asyncio.create_task(runtime.reset_context(old.session_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert runtime._active_session is original
        assert runtime.transcript_store.get_active_session('main').session_id == old.session_id
        assert runtime.transcript_store.load_transcript(old.session_id) == rows
    finally:
        await runtime.aclose()


async def test_unpersisted_history_cannot_be_dropped_by_rotation(tmp_path):
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path,
                           model='offline', stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    old, rows = seed(runtime.transcript_store)
    original = await runtime.prepare_session(allow_reset=False)
    original._restore_messages()
    original.agent.replace_messages([
        *original.agent.messages, UserMessage(content='New unsaved scope; do not lose it.')])
    try:
        with pytest.raises(ValueError, match='Unpersisted history'):
            await runtime.reset_context(old.session_id)
        assert runtime._active_session is original
        assert runtime.transcript_store.get_active_session('main').session_id == old.session_id
        assert runtime.transcript_store.load_transcript(old.session_id) == rows
    finally:
        await runtime.aclose()


async def test_summary_retains_canonical_external_input_without_promoting_authority(tmp_path):
    from xdog.agent.session_input import SessionInput, encode_input
    observed = []
    def stream(model, context, options):
        observed.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Untrusted external scope retained.'),)))
    external = encode_input(SessionInput('external-id', 'automatic', 'goal',
        'UNIQUE_ACP_TASK: inspect marker, do not delete anything.', ({'marker': 'source-evidence'},),
        {'trigger': 'fixture'}))
    await Summarizer('offline', stream).summarize([external])
    visible = str(observed[0].messages)
    assert 'UNIQUE_ACP_TASK' in visible and 'source-evidence' in visible
    assert 'not a human message' in visible


async def test_history_evidence_is_readonly_but_does_not_release_mutation_scope(tmp_path):
    from xdog.agent.core import AgentContext
    from xdog.agent.types import BeforeToolCallContext
    from xdog.ai.types import ToolCall
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('memory',)), data_dir=tmp_path,
                           model='offline', stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    old, _ = seed(runtime.transcript_store)
    runtime.transcript_store.reset_session('main')
    session = await runtime.prepare_session(allow_reset=False)
    session.access.set_read_only(True)
    memory = next(tool for tool in session.agent.active_tools if tool.name == 'memory')
    async def check(args):
        return await session._before_tool_call(BeforeToolCallContext(
            tool_call=ToolCall(id='test', name='memory', arguments=args), args=args,
            assistant_message=AssistantMessage(), context=AgentContext(tools=[memory])))
    try:
        args = {'action': 'history', 'mode': 'get', 'session_id': old.session_id, 'position': 0}
        assert await check(args) is None
        result = await memory.execute('read', args, ctx=session.agent.tool_context)
        assert 'owner/one' in str(result.content)
        denied = await check({'action': 'write', 'target': 'daily', 'text': 'not a user-approved mutation'})
        assert denied.block and session.access.state.read_only
    finally:
        await runtime.aclose()
