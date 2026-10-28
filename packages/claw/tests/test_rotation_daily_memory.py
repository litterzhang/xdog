"""Rotation runs actual managed-memory writes without another Agent or permissions."""
from __future__ import annotations

import asyncio
import datetime
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.claw.core.runtime.context_handoff import prepare

from packages.claw.tests.test_active_tool_persistence import close, create


@pytest.mark.parametrize('previous_live', [True, False])
@pytest.mark.parametrize('reason', ['daily', 'manual'])
async def test_rotation_writes_real_daily_memory_with_selected_model(tmp_path, previous_live, reason):
    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('memory', 'current_time', 'bash')
    contexts = []
    maintenance_calls = 0

    def stream(model, context, options):
        nonlocal maintenance_calls
        contexts.append((model, context, options))
        if context.tools:
            maintenance_calls += 1
            if maintenance_calls == 1:
                return EventStream.empty(AssistantMessage(content=(ToolCall(
                    id='save', name='memory', arguments={'action': 'write', 'target': 'daily',
                                                       'text': '用户确定仅删除已确认的仓库。'}),)))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Scope retained; no deletion executed.'),)))

    runtime.stream_fn = stream
    runtime.summarizer = Summarizer('unused', stream)
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    meta = session.meta
    runtime.transcript_store.append_turn(meta.session_id,
        {'role': 'user', 'content': 'Confirmed repository scope; authorization pending.'})
    if previous_live:
        session.agent.set_model('offline/selected')
        session.agent.set_options(replace(session.agent.options, thinking='high'))
    else:
        runtime.transcript_store.save_model(meta.session_id, 'offline/selected')
        runtime.transcript_store.save_reasoning(meta.session_id, 'offline/selected', 'high')
    try:
        draft = await prepare(runtime, meta, reason, session if previous_live else None, stream)
        assert draft.memory_status == 'checked'
        assert draft.summary
        daily = runtime.workspace_dir / 'memory' / f'{datetime.date.today().isoformat()}.md'
        assert '用户确定仅删除' in daily.read_text()
        assert all(model == 'offline/selected' and options.thinking == 'high' for model, _, options in contexts)
        assert all([tool.name for tool in context.tools] == ['memory'] for _, context, _ in contexts if context.tools)
        assert runtime.transcript_store.get_active_session(runtime.group.id) == meta
    finally:
        await close(runtime)


@pytest.mark.parametrize('previous_live', [True, False])
async def test_disabled_selection_not_reenabled(tmp_path, previous_live):
    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    await session.select_active_tools([], expected_revision=session.agent.tool_selection_revision)
    meta = session.meta
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'Remember a decision.'})
    try:
        draft = await prepare(runtime, meta, 'manual', session if previous_live else None, runtime.stream_fn)
        assert draft.memory_status == 'skipped_tools_disabled'
        assert not (runtime.workspace_dir / 'memory').exists()
    finally:
        await close(runtime)


@pytest.mark.parametrize('failure', ['write', 'model', 'restricted_read', 'extra_tool', 'cancel'])
async def test_maintenance_failure_preserves_handoff_and_cancel_propagates(tmp_path, monkeypatch, failure):
    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('memory', 'bash')
    calls = 0
    def stream(model, context, options):
        nonlocal calls
        calls += 1
        if context.tools and calls == 1:
            if failure == 'cancel':
                raise asyncio.CancelledError()
            if failure == 'model':
                raise RuntimeError('model unavailable')
            args = ({'action': 'get', 'filename': 'secrets.txt'} if failure == 'restricted_read' else
                    {'action': 'write', 'target': 'daily', 'text': 'decision'})
            return EventStream.empty(AssistantMessage(content=(ToolCall(
                id='t', name='bash' if failure == 'extra_tool' else 'memory', arguments=args),)))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Handoff survives failure.'),)))
    runtime.stream_fn = stream
    runtime.summarizer = Summarizer('unused', stream)
    meta = runtime.transcript_store.create_session(runtime.group.id)
    runtime.transcript_store.append_turn(meta.session_id,
        {'role': 'user', 'content': 'Confirmed scope, no deletion yet.'})
    if failure == 'write':
        def fail(*args, **kwargs):
            raise OSError('disk unavailable')
        monkeypatch.setattr('xdog.claw.core.memory.daily_log.DailyLog.append', fail)
    try:
        if failure == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await prepare(runtime, meta, 'idle', None, stream)
        else:
            draft = await prepare(runtime, meta, 'idle', None, stream)
            assert draft.memory_status == 'failed'
            assert draft.recent and draft.summary
        assert runtime.transcript_store.get_active_session(runtime.group.id) == meta
    finally:
        await close(runtime)


async def test_cold_extension_provider_never_uses_primary_adapter(tmp_path):
    runtime, calls = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    meta = runtime.transcript_store.create_session(runtime.group.id)
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'Persist this decision.'})
    runtime.transcript_store.save_model(meta.session_id, 'ext-private/model')
    try:
        draft = await prepare(runtime, meta, 'daily', None, runtime.stream_fn)
        assert draft.memory_status == 'unavailable_provider'
        assert draft.recent and not calls
    finally:
        await close(runtime)


async def test_no_new_facts_checked_without_fabricated_daily_write(tmp_path, monkeypatch):
    runtime, calls = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    meta = runtime.transcript_store.create_session(runtime.group.id)
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'No new facts.'})
    def no_agent(*args, **kwargs):
        raise AssertionError('Maintenance must not construct an Agent')
    monkeypatch.setattr('xdog.agent.agent.Agent.__init__', no_agent)
    try:
        draft = await prepare(runtime, meta, 'manual', None, runtime.stream_fn)
        assert draft.memory_status == 'checked'
        assert calls and not (runtime.workspace_dir / 'memory').exists()
    finally:
        await close(runtime)


async def test_flush_projects_previous_handoff_and_passes_options():
    from xdog.agent.core import CustomAgentMessage
    from xdog.ai.types import StreamOptions
    from xdog.claw.core.compaction.flush_runner import FlushRunner
    from xdog.claw.core.persistence.handoff import TYPE, capture, record

    data = record(capture('previous', [{'role': 'user', 'content': 'confirmed scope'}], 'daily'),
                  'current', '2026-10-28T01:00:00')
    contexts = []
    options = StreamOptions(thinking='low')
    def stream(model, context, actual_options):
        assert actual_options is options
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Nothing new.'),)))
    assert await FlushRunner('offline/model', stream).run(
        [CustomAgentMessage(type=TYPE, data=data)], '', [], options=options)
    assert 'confirmed scope' in str(contexts[0].messages)
    assert 'NOT a human message' in str(contexts[0].messages)


async def test_cold_memory_uses_actual_group_memory_manager(tmp_path, monkeypatch):
    from xdog.claw.core.memory.manager import MemoryManager
    monkeypatch.setattr('xdog.claw.core.memory.manager._HAS_HYBRID_SEARCH', False)
    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    runtime.memory = MemoryManager(runtime.workspace_dir, runtime.data_dir, runtime.group.id)
    meta = runtime.transcript_store.create_session(runtime.group.id)
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'Remember the decision.'})
    calls = 0
    def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return EventStream.empty(AssistantMessage(content=(ToolCall(
                id='save', name='memory',
                arguments={'action': 'write', 'target': 'daily', 'text': 'Actual manager fact'}),)))
        return EventStream.empty(AssistantMessage())
    try:
        draft = await prepare(runtime, meta, 'daily', None, stream)
        assert draft.memory_status == 'checked'
        assert 'Actual manager fact' in runtime.memory.daily_log.read_today()
    finally:
        await close(runtime)


@pytest.mark.parametrize('read_only', [False, True])
async def test_effective_memory_args_are_rechecked_after_extension_rewrite(tmp_path, monkeypatch, read_only):
    from unittest.mock import AsyncMock

    runtime, _ = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    if read_only:
        session.access.set_read_only(True)
    captured = []
    async def pre(name, args):
        captured.append((name, dict(args)))
        return {'action': 'get', 'filename': 'private-not-memory.txt'}
    monkeypatch.setattr(session.extensions, 'enabled', ('rewrite',))
    monkeypatch.setattr(session.extensions, 'event', AsyncMock())
    monkeypatch.setattr(session.extensions, 'pre', pre)
    (runtime.workspace_dir / 'private-not-memory.txt').write_text('MUST_NOT_BE_READ_BY_MAINTENANCE')
    try:
        tools = session._compaction_tools(memory_only=True)
        assert len(tools) == 1 and tools[0].name == 'memory'
        result = await tools[0].execute('read', {'action': 'get', 'filename': 'MEMORY.md'})
        assert captured and result.is_error
        assert 'Outside managed memory' in str(result.content)
        assert 'MUST_NOT_BE_READ_BY_MAINTENANCE' not in str(result.content)
        assert session.access.state.read_only == read_only
    finally:
        monkeypatch.undo()
        await close(runtime)


async def test_cold_maintenance_cannot_bypass_unrestored_extension_hooks(tmp_path):
    runtime, calls = create(tmp_path)
    runtime._enabled_tools = ('memory',)
    runtime.extension_names = ('privacy_veto',)
    meta = runtime.transcript_store.create_session(runtime.group.id)
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'Important private decision.'})
    try:
        draft = await prepare(runtime, meta, 'daily_reset', None, runtime.stream_fn)
        assert draft.memory_status == 'unavailable_host_hooks'
        assert draft.recent and not calls and not (runtime.workspace_dir / 'memory').exists()
        assert runtime.transcript_store.get_active_session(runtime.group.id) == meta
    finally:
        await close(runtime)
