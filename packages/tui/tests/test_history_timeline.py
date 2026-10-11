"""Lineage-backed merge retains repeated/divergent events and compaction provenance."""
import pytest
from xdog.tui.history_timeline import TimelineSource, timeline_records


def user(text):
    return {'role': 'user', 'content': [{'type': 'text', 'text': text}]}


def assistant(text):
    return {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}


def body_records(records, body):
    return [record for record in records if record.body == body]


def test_parent_prefix_merge_only_with_provenance_not_content_match():
    original = TimelineSource('ancestor', 'Original', (user('same request'), assistant('shared answer')))
    branch = TimelineSource('branch', 'Fork', (*original.messages, user('same request'), assistant('fork answer')),
                            parent='ancestor')
    unrelated = TimelineSource('unrelated', 'Independent', original.messages)
    records = timeline_records((original, branch, unrelated))
    same = body_records(records, 'same request')
    assert len(same) == 3  # shared first, repeated later, independent first.
    assert len(body_records(records, 'shared answer')) == 2
    assert any('Original [ancestor] | Fork [branch]' in record.title for record in same)
    assert any('Independent' in record.title for record in same)


def test_compaction_retained_tail_alias_and_transitive_link():
    original = TimelineSource('before1', 'Before #1', (user('older1'), assistant('answer1'),
                                                     user('recent'), assistant('recent answer')))
    first = TimelineSource('before2', 'Before #2', (user('summary1'), *original.messages[2:]),
        compaction={'recovery_branch_id': 'before1', 'original_count': 4, 'first_kept_index': 2})
    head = TimelineSource('@head', 'Current', (user('summary2'), *first.messages[1:], user('new turn')),
        compaction={'recovery_branch_id': 'before2', 'original_count': 3, 'first_kept_index': 1})
    records = timeline_records((original, first, head))
    assert len(body_records(records, 'recent')) == 1
    assert len(body_records(records, 'recent answer')) == 1
    assert 'Before #1 [before1] | Before #2 [before2] | Current [@head]' in body_records(records, 'recent')[0].title
    assert len(body_records(records, 'summary1')) == len(body_records(records, 'summary2')) == 1


def test_shared_call_with_divergent_results_stays_distinct():
    call = {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'read', 'name': 'read', 'arguments': {}}]}
    def result(text):
        return {'role': 'toolResult', 'tool_call_id': 'read', 'tool_name': 'read',
                'content': [{'type': 'text', 'text': text}]}
    original = TimelineSource('first', 'First', (user('request'), call, result('result1')))
    changed = TimelineSource('second', 'Second', (user('request'), call, result('result2')), parent='first')
    records = timeline_records((original, changed))
    tools = [record for record in records if record.role == 'tool']
    assert len(tools) == 2 and any('result1' in record.body for record in tools)
    assert any('result2' in record.body for record in tools)


def test_invalid_link_cycle_and_capacity_fail_instead_of_partial_timeline():
    with pytest.raises(ValueError, match='cycle'):
        timeline_records((TimelineSource('a', 'A', (user('x'),), parent='b'),
                          TimelineSource('b', 'B', (user('x'),), parent='a')))
    original = TimelineSource('a', 'A', (user('old'), user('retained')))
    mismatched = TimelineSource('b', 'B', (user('summary'), user('not copied')),
        compaction={'recovery_branch_id': 'a', 'original_count': 2, 'first_kept_index': 1})
    with pytest.raises(ValueError, match='does not match'):
        timeline_records((original, mismatched))
    with pytest.raises(ValueError, match='capacity'):
        timeline_records(tuple(TimelineSource(str(index), str(index), ()) for index in range(257)))


def test_redaction_and_signature_binary_omission_not_a_content_dedup_key():
    first = {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'visible reasoning',
                                             'thinking_signature': 'PRIVATE-CONTINUITY'}]}
    records = timeline_records((TimelineSource('a', 'A', (first,)),))
    assert 'visible reasoning' in str(records) and 'PRIVATE-CONTINUITY' not in str(records)
