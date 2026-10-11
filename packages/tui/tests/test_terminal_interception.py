from __future__ import annotations

import pytest
from xdog.tui.terminal_interception import TerminalInputQueue


def create():
    owner, sent, delivered, notices = ['one'], [], [], []
    def send(item):
        sent.append(item)
        return True
    queue = TerminalInputQueue(lambda: owner[0], send,
                               lambda item, data: delivered.append((item.kind, data)), notices.append)
    return queue, owner, sent, delivered, notices


def test_fifo_nonblocking_dispatch_transform_consume_and_empty_are_not_replayed():
    queue, _, sent, delivered, _ = create()
    assert queue.submit('a', kind='key', surface='editor')
    assert queue.submit('paste\ntext', kind='paste', surface='editor')
    assert queue.submit('c', kind='key', surface='transcript')
    assert not sent and queue.poll() and len(sent) == 1
    assert not queue.poll()
    assert not queue.accept(999, {'consume': True})
    assert queue.accept(sent[0].id, {'data': 'A'})
    assert not queue.accept(sent[0].id, {'data': 'twice'})
    assert queue.poll() and delivered == [('key', 'A')] and len(sent) == 2
    assert queue.accept(sent[1].id, {'consume': True})
    queue.poll()
    assert queue.accept(sent[2].id, {'data': ''})
    queue.poll()
    assert delivered == [('key', 'A'), ('key', '')] and queue.pending == 0


def test_owner_changes_retain_original_inputs_without_dispatch_into_other_ui():
    queue, owner, sent, delivered, notices = create()
    queue.submit('old', kind='key', surface='editor')
    queue.submit('queued', kind='paste', surface='editor')
    queue.poll()
    owner[0] = 'permission'
    queue.accept(sent[0].id, {'data': '\r'})
    queue.poll()
    assert not delivered and len(sent) == 1
    assert [item.data for item in queue.recovery] == ['old', 'queued']
    assert len(notices) == 2


def test_interrupt_is_immediate_late_reply_cannot_answer_new_input():
    queue, _, sent, delivered, _ = create()
    queue.submit('old', kind='key', surface='editor')
    queue.poll()
    old = sent[0].id
    queue.invalidate()
    queue.submit('new', kind='key', surface='editor')
    queue.poll()
    assert not queue.accept(old, {'data': 'late'})
    queue.accept(sent[-1].id, {'data': 'NEW'})
    queue.poll()
    assert delivered == [('key', 'NEW')] and queue.recovery[0].data == 'old'


@pytest.mark.parametrize('result', [{'consume': 1}, {'data': 1}, {'data': 'x' * 16001},
                                   {'error': 'private'}, {'data': '\ud800'}, {'unexpected': {}}])
def test_malformed_results_keep_original_and_never_expose_private_error(result):
    queue, _, sent, delivered, notices = create()
    queue.submit('original', kind='key', surface='editor')
    queue.poll()
    queue.accept(sent[0].id, result)
    queue.poll()
    assert not delivered and queue.recovery[0].data == 'original'
    assert 'private' not in str(notices)


def test_count_byte_quotas_and_recovery_are_bounded_including_inflight_work():
    queue, _, sent, _, _ = create()
    for _ in range(32):
        assert queue.submit('a', kind='key', surface='editor')
    assert not queue.submit('overflow', kind='key', surface='editor')
    queue.poll()
    assert len(sent) == 1 and queue.pending == 32
    queue.close()
    assert queue.pending == 0 and len(queue.recovery) == 32
    assert not queue.submit('closed', kind='key', surface='editor')
    assert not queue.accept(sent[0].id, {'data': 'late'})
    queue, _, _, _, _ = create()
    for _ in range(8):
        assert queue.submit('x' * 16000, kind='paste', surface='editor')
    assert not queue.submit('x' * 16000, kind='paste', surface='editor')
    queue.invalidate()
    assert sum(len(item.data.encode()) for item in queue.recovery) <= 128 * 1024



def test_a_missing_reply_expires_and_late_reply_cannot_capture_next_input():
    now, sent, delivered, notices = [0.0], [], [], []
    queue = TerminalInputQueue(lambda: 'owned', lambda item: sent.append(item) or True,
        lambda item, data: delivered.append(data), notices.append, timeout=0.1, clock=lambda: now[0])
    queue.submit('expired', kind='key', surface='editor')
    queue.submit('next', kind='key', surface='editor')
    queue.poll()
    old = sent[0].id
    now[0] = 0.2
    queue.poll()
    assert len(sent) == 2 and queue.recovery[0].data == 'expired'
    assert not queue.accept(old, {'data': 'late'})
    queue.accept(sent[1].id, {'data': 'NEXT'})
    queue.poll()
    assert delivered == ['NEXT'] and len(notices) == 1
