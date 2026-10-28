import json
from dataclasses import asdict

import pytest
from xdog.eyes.detectors.records import RecordDetector, matches
from xdog.eyes.models import AvailabilityDetectorDef, ConditionDetectorDef, Predicate, RecordDetectorDef
from xdog.eyes.sources.records import Record, RecordSnapshot, canonical


def snapshot(*values, at=1, **kwargs):
    return RecordSnapshot(tuple(Record(str(key), canonical(value), 'https://example.test/'+str(key))
                                for key, value in values), at, **kwargs)


def flush(detector):
    return detector.flush(now=100)


def test_record_baseline_changes_and_complete_only_removals():
    detector = RecordDetector(RecordDetectorDef(id='d', source='s', removals=True), max_records=10)
    detector.observe(snapshot((1, {'name': '招聘公告'})), now=1)
    assert not flush(detector)
    detector.observe(snapshot((1, {'name': '招聘公告'})), now=2)
    assert not flush(detector)
    detector.observe(snapshot((1, {'name': '修订'}), (2, {'name': '新增'})), now=3)
    events = flush(detector)
    assert [event.type for event in events] == ['record.updated', 'record.created']
    assert '修订' in events[0].evidence[0].after
    detector.observe(snapshot(complete=False), now=4)
    assert not flush(detector)
    detector.observe(snapshot(), now=5)
    assert {event.type for event in flush(detector)} == {'record.removed'}


def test_failures_and_not_modified_do_not_replace_baselines():
    detector = RecordDetector(RecordDetectorDef(id='d', source='s', removals=True), max_records=10)
    detector.observe(snapshot((1, {})), now=1)
    detector.observe(snapshot(healthy=False, valid=False, complete=False), now=2)
    detector.observe(snapshot(unchanged=True), now=3)
    assert not flush(detector)
    assert len(detector.checkpoint()['records']) == 1


def test_condition_edges_per_record_and_new_attempt():
    definition = ConditionDetectorDef(id='d', source='s', all=(Predicate('/status', 'eq', 'completed'),
                                                               Predicate('/conclusion', 'eq', 'failure')))
    detector = RecordDetector(definition, max_records=10)
    detector.observe(snapshot(('run:1', {'status': 'in_progress'})), now=1)
    detector.observe(snapshot(('run:1', {'status': 'completed', 'conclusion': 'failure'})), now=2)
    assert [event.type for event in flush(detector)] == ['condition.met']
    detector.observe(snapshot(('run:1', {'status': 'completed', 'conclusion': 'failure'})), now=3)
    assert not flush(detector)
    detector.observe(snapshot(('run:2', {'status': 'completed', 'conclusion': 'failure'}), complete=False), now=4)
    assert [event.type for event in flush(detector)] == ['condition.met']


@pytest.mark.parametrize('actual,op,expected,result', [(True, 'eq', 1, False), (1, 'eq', 1.0, True),
    ('2', 'gt', 1, False), (3, 'gt', 1, True), (None, 'eq', None, True)])
def test_condition_comparison_types(actual, op, expected, result):
    assert matches(canonical({'x': actual}), (Predicate('/x', op, expected),)) is result
    assert not matches('{}', (Predicate('/x', 'ne', expected),))


def test_health_threshold_duration_and_restart_do_not_create_false_recovery():
    definition = AvailabilityDetectorDef(id='health', source='s', failures=3, for_seconds=10)
    detector = RecordDetector(definition, max_records=10)
    detector.observe(snapshot(at=1), now=1)
    assert not flush(detector)
    for stamp in (2, 5):
        detector.observe(snapshot(at=stamp, healthy=False, valid=False), now=stamp)
        assert not flush(detector)
    restored = RecordDetector(definition, max_records=10)
    restored.restore(json.loads(json.dumps(detector.checkpoint())), now=100)
    restored.observe(snapshot(at=12, healthy=False, status_code=500, error='HTTP status 500'), now=101)
    failed = flush(restored)
    assert failed[0].type == 'availability.unavailable'
    assert failed[0].evidence[0].failures == 3
    restored.observe(snapshot(at=13, healthy=False), now=102)
    assert not flush(restored)
    restored.observe(snapshot(at=14), now=103)
    assert [event.type for event in flush(restored)] == ['availability.recovered']


def test_initial_abnormal_can_be_suppressed_until_healthy_baseline():
    detector = RecordDetector(AvailabilityDetectorDef(id='d', source='s', failures=1, initial_abnormal=False),
                              max_records=10)
    detector.observe(snapshot(healthy=False), now=1)
    detector.observe(snapshot(), now=2)
    assert not flush(detector)
    detector.observe(snapshot(healthy=False), now=3)
    assert flush(detector)[0].type == 'availability.unavailable'


def test_safe_window_retention_never_claims_deletion():
    detector = RecordDetector(RecordDetectorDef(id='d', source='s', removals=True), max_records=2)
    old = Record('old', '{}', 'https://example.test/old', 1)
    detector.observe(RecordSnapshot((old,), 1, complete=False), now=1)
    new = Record('new', '{}', 'https://example.test/new', 100)
    detector.observe(RecordSnapshot((new,), 100, complete=False, window_start=50), now=100)
    events = flush(detector)
    assert len(events) == 1 and events[0].type == 'record.created'
    assert [item['key'] for item in detector.checkpoint()['records']] == ['new']
    assert asdict(events[0])['evidence'][0]['url'] == new.url
