"""Receipt exception diagnostics are bounded data, not recursive tracebacks."""
from __future__ import annotations

import asyncio

import pytest
from xdog.claw.core.inputs.receipt_projection import (
    MAX_EXCEPTION_SUMMARY_BYTES,
    MAX_EXCEPTION_SUMMARY_DEPTH,
    MAX_EXCEPTION_SUMMARY_NODES,
    summarize_error,
)


@pytest.mark.parametrize('error', [OSError('unchanged ordinary error'), RuntimeError(''), ValueError('中文错误')])
def test_single_exception_text_is_unchanged(error):
    assert summarize_error(error) == (str(error) or type(error).__name__)


def test_group_retains_primary_and_nested_cleanup_leaves_without_tracebacks():
    primary = OSError('original append failure')
    primary.__cause__ = ValueError('not a receipt traceback')
    primary.add_note('not a receipt exception note')
    error = ExceptionGroup('Claw turn and cleanup failed', [
        primary,
        ExceptionGroup('cleanup failed', [RuntimeError('producer failure'), OSError('persist failure')]),
    ])
    summary = summarize_error(error)
    for detail in ('Claw turn and cleanup failed', 'original append failure', 'cleanup failed',
                   'producer failure', 'persist failure'):
        assert detail in summary
    assert summary.index('original append failure') < summary.index('persist failure')
    assert 'not a receipt' not in summary and 'Traceback' not in summary


def test_oversized_primary_cannot_consume_cleanup_diagnostic_budget():
    summary = summarize_error(ExceptionGroup('double fault', [
        OSError('主错误' * MAX_EXCEPTION_SUMMARY_BYTES), OSError('cleanup persist failure'),
    ]))
    assert '主错误' in summary and 'cleanup persist failure' in summary
    assert len(summary.encode()) <= MAX_EXCEPTION_SUMMARY_BYTES
    assert 'truncated' in summary


def test_depth_limit_does_not_hide_a_shallow_cleanup_sibling():
    error = OSError('too deep to render')
    for _ in range(MAX_EXCEPTION_SUMMARY_DEPTH + 1000):
        error = ExceptionGroup('nested', [error])
    summary = summarize_error(ExceptionGroup('double fault', [error, OSError('shallow cleanup failure')]))
    assert 'too deep to render' not in summary
    assert 'shallow cleanup failure' in summary and 'truncated' in summary
    assert len(summary.encode()) <= MAX_EXCEPTION_SUMMARY_BYTES


def test_count_limit_bounds_leaf_formatting_and_reports_omission():
    rendered = []

    class CountedError(Exception):
        def __str__(self):
            rendered.append(True)
            return 'bounded leaf'

    summary = summarize_error(ExceptionGroup('many', [CountedError() for _ in range(1000)]))
    assert len(rendered) <= MAX_EXCEPTION_SUMMARY_NODES
    assert 'truncated' in summary and len(summary.encode()) <= MAX_EXCEPTION_SUMMARY_BYTES


def test_total_utf8_bytes_are_bounded_even_with_large_group_labels_and_leaves():
    summary = summarize_error(ExceptionGroup('组' * MAX_EXCEPTION_SUMMARY_BYTES, [
        ValueError('叶' * MAX_EXCEPTION_SUMMARY_BYTES) for _ in range(MAX_EXCEPTION_SUMMARY_NODES)
    ]))
    assert len(summary.encode()) <= MAX_EXCEPTION_SUMMARY_BYTES
    assert 'truncated' in summary


def test_exception_group_does_not_call_custom_recursive_group_formatter():
    class Group(ExceptionGroup):
        def __str__(self):
            pytest.fail('Receipt summary must not format the exception group recursively')

    assert 'original' in summarize_error(Group('group', [OSError('original')]))


async def test_actual_consumer_cancellation_is_not_summarized(tmp_path, monkeypatch):
    from xdog.claw.core.inputs.consumer import InputService
    from xdog.claw.core.inputs.store import InputStore
    from xdog.claw.core.runtime.input_adapter import freeze_input
    from xdog.claw.core.types import UserInput

    store = await InputStore.open(tmp_path / 'inputs.sqlite3')

    async def execute(record, admit):
        raise asyncio.CancelledError

    def unexpected(error):
        pytest.fail('Actual cancellation must bypass exception summarization')

    monkeypatch.setattr('xdog.claw.core.inputs.consumer.summarize_error', unexpected)
    service = InputService(store, execute, paused=True)
    try:
        envelope = freeze_input(UserInput(group_id='main', content='cancel'), tmp_path, message_id='cancel')
        await store.enqueue(envelope)
        record = await store.claim(envelope.scope)
        with pytest.raises(asyncio.CancelledError):
            await service._execute(record)
    finally:
        await service.close()
