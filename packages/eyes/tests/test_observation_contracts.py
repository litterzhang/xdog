"""Source/detector composition follows data contracts, not transport names."""

import asyncio
import json
from dataclasses import replace

import pytest
from xdog.eyes.detectors.records import RecordDetector
from xdog.eyes.errors import ObserverValidationError, SourceError
from xdog.eyes.events import DeliveryResult
from xdog.eyes.executor import observe
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.models import ConditionDetectorDef, Predicate, ReceivedDetectorDef, RecordDetectorDef, SourceDef
from xdog.eyes.sources.file import records, scan, scan_async
from xdog.eyes.sources.records import PollResult, RecordSnapshot, record
from xdog.eyes.testing import FakeClock


def definition(source, detectors):
    return {
        "version": 2,
        "name": "composition",
        "sources": [source],
        "detectors": detectors,
        "handlers": [{"id": "sink", "type": "events"}],
        "routes": [
            {
                "id": "route",
                "detector": detectors[0]["id"],
                "handler": "sink",
                "event_types": ["event.received"] if detectors[0]["type"] == "event.received" else ["record.updated"],
            }
        ],
    }


@pytest.mark.parametrize(
    "source",
    [
        {"id": "s", "type": "file", "root": ".", "format": "json"},
        {"id": "s", "type": "http", "url": "https://example.test"},
        {"id": "s", "type": "feed", "url": "https://example.test/feed"},
        {"id": "s", "type": "github", "repository": "o/r", "resource": "issues"},
        {"id": "s", "type": "scheduler", "schedule": {"every_seconds": 60}},
        {"id": "s", "type": "event"},
    ],
)
def test_generic_detectors_compose_with_all_record_sources(tmp_path, source):
    detectors = [
        {"id": "d", "type": "record.change", "source": "s", "compare_fields": ["/status"]},
        {
            "id": "condition",
            "type": "value.condition",
            "source": "s",
            "all": [{"pointer": "/status", "op": "eq", "value": "failed"}],
        },
        {"id": "received", "type": "event.received", "source": "s"},
    ]
    result = definition_data(parse(definition(source, detectors), base_dir=tmp_path))
    assert "compare_fields" not in result["sources"][0]
    assert result["detectors"][0]["compare_fields"] == ["/status"]
    assert definition_data(parse(result, base_dir=tmp_path)) == result


@pytest.mark.parametrize(
    "source,detector",
    [
        ({"id": "s", "type": "event"}, {"id": "d", "type": "availability", "source": "s"}),
        ({"id": "s", "type": "event"}, {"id": "d", "type": "record.change", "source": "s", "removals": True}),
        (
            {"id": "s", "type": "feed", "url": "https://example.test/feed"},
            {"id": "d", "type": "record.change", "source": "s", "removals": True},
        ),
        ({"id": "s", "type": "http", "url": "https://example.test"}, {"id": "d", "type": "file.change", "source": "s"}),
    ],
)
def test_incompatible_data_semantics_fail_before_start(tmp_path, source, detector):
    with pytest.raises(ObserverValidationError, match="requires"):
        parse(definition(source, [detector]), base_dir=tmp_path)


def test_one_acquisition_independent_fields_conditions_and_full_evidence():
    title = RecordDetector(RecordDetectorDef(id="title", source="s", compare_fields=("/title",)), max_records=2)
    status = RecordDetector(RecordDetectorDef(id="status", source="s", compare_fields=("/status",)), max_records=2)
    condition = RecordDetector(
        ConditionDetectorDef(id="failed", source="s", all=(Predicate("/status", "eq", "failed"),)), max_records=2
    )
    received = RecordDetector(ReceivedDetectorDef(id="receipt", source="s"), max_records=2)
    for index, value in enumerate(
        [
            {"title": "招聘", "status": "pending", "time": 1},
            {"title": "招聘", "status": "failed", "time": 2},
        ]
    ):
        snapshot = RecordSnapshot((record("job", value, "https://example.test/job"),), index)
        for detector in (title, status, condition, received):
            detector.observe(snapshot, now=index)
    assert title.flush(now=2) == ()
    (changed,) = status.flush(now=2)
    assert json.loads(changed.evidence[0].after)["time"] == 2
    assert condition.flush(now=2)[0].type == "condition.met"
    assert len(received.flush(now=2)) == 2


async def test_file_json_and_hash_views_share_one_safe_read(tmp_path):
    path = tmp_path / "task.json"
    path.write_text('{"status":"pending"}')
    source = SourceDef("s", "file", tmp_path, include=("*.json",), format="json")
    first = await scan_async(source)
    projected = records(source, first, now=1)
    assert json.loads(projected.records[0].value) == {"status": "pending"}
    assert projected.records[0].key == "task.json"
    assert projected.records[0].url == path.as_uri()
    metadata = records(replace(source, format="metadata"), first, now=1)
    assert json.loads(metadata.records[0].value)["digest"] == first.entries[0][1].digest
    # No second read inside the adapter: the file can disappear after acquisition.
    path.unlink()
    assert records(source, first, now=2).records[0].value == projected.records[0].value


@pytest.mark.parametrize("content", ["{", '{"duplicate":1,"duplicate":2}', '{"x":NaN}', "[" * 34 + "0" + "]" * 34])
def test_invalid_file_json_never_becomes_an_empty_snapshot(tmp_path, content):
    (tmp_path / "task.json").write_text(content)
    with pytest.raises(SourceError):
        scan(SourceDef("s", "file", tmp_path, format="json"))


def test_file_json_aggregate_content_bound(tmp_path):
    for index in range(2):
        (tmp_path / f"{index}.json").write_text(json.dumps({"v": "x" * 600000}))
    with pytest.raises(SourceError, match="exceeds"):
        scan(SourceDef("s", "file", tmp_path, format="json"))


async def test_invalid_projection_retains_source_cursor_and_all_detector_baselines(tmp_path):
    source = {"id": "s", "type": "http", "url": "https://example.test", "poll_interval_seconds": 1}
    detectors = [
        {"id": "d", "type": "record.change", "source": "s"},
        {"id": "strict", "type": "record.change", "source": "s", "compare_fields": ["/required"]},
    ]
    config = parse(definition(source, detectors), base_dir=tmp_path)
    checkpoints, delivered, seen = [], [], []
    clock, stop = FakeClock(limit=10), asyncio.Event()

    class Sampler:
        async def poll(self, source, state, *, now):
            seen.append(state)
            if len(seen) == 1:
                value = {"required": 1}
            else:
                value = {"missing": 2}
                if len(seen) == 3:
                    stop.set()
            return PollResult(RecordSnapshot((record("job", value, ""),), now), {"cursor": len(seen)}, 1)

        async def close(self):
            pass

    async def save(value):
        checkpoints.append(value)

    async def deliver(value):
        delivered.append(value)
        return DeliveryResult("succeeded")

    await observe(
        config,
        registration_id="reg",
        scanner=scan_async,
        deliver=deliver,
        emit=lambda event: None,
        clock=clock.now,
        wall_clock=clock.now,
        sleep=clock.sleep,
        cancel=stop,
        remote_sampler=Sampler(),
        save=save,
    )
    assert len(seen) >= 3 and seen[1] == seen[2] == {"cursor": 1}
    assert not delivered
    assert checkpoints[-1]["remote_sources"]["s"] == {"cursor": 1}
    assert json.loads(checkpoints[-1]["detectors"]["d"]["records"][0]["value"]) == {"required": 1}


async def test_scheduler_records_can_use_received_and_conditions(tmp_path):
    config = definition(
        {"id": "s", "type": "scheduler", "schedule": {"every_seconds": 1}},
        [{"id": "d", "type": "event.received", "source": "s"}],
    )
    config["detectors"].append(
        {
            "id": "condition",
            "type": "value.condition",
            "source": "s",
            "all": [{"pointer": "/timezone", "op": "eq", "value": "UTC"}],
        }
    )
    clock, stop, sent = FakeClock(limit=10), asyncio.Event(), []

    async def deliver(value):
        sent.append(value)
        stop.set()
        return DeliveryResult("succeeded")

    await observe(
        parse(config, base_dir=tmp_path),
        registration_id="reg",
        scanner=scan_async,
        deliver=deliver,
        emit=lambda event: None,
        clock=clock.now,
        wall_clock=clock.now,
        sleep=clock.sleep,
        cancel=stop,
    )
    assert len(sent) == 1
    event = sent[0].events[0]
    assert event.type == "event.received"
    assert json.loads(event.evidence[0].after)["schedule_id"] == "s"


@pytest.mark.parametrize('fields', [['/a', '/a'], ['not/a/pointer'], ['/a~2b'], [1], ['/x'] * 33, None])
def test_invalid_detector_projection_is_rejected(tmp_path, fields):
    value = definition({'id': 's', 'type': 'file', 'root': '.'}, [
        {'id': 'd', 'type': 'record.change', 'source': 's', 'compare_fields': fields}])
    with pytest.raises(ObserverValidationError):
        parse(value, base_dir=tmp_path)


async def test_old_daemon_refuses_contract_registration_and_publish_before_submission(tmp_path, monkeypatch):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.protocol import FEATURES, hello

    calls = []

    async def request(location, action, body, **options):
        calls.append(action)
        assert action == 'hello'
        return {'status': 'ok', 'generation': 'older-daemon',
                'body': {**hello(), 'features': [*FEATURES, 'remote-sources-v1', 'content-sources-v1']}}

    monkeypatch.setattr('xdog.eyes._daemon_control.request', request)
    control = DaemonControl(owner='project', directory=tmp_path / 'private')
    value = definition({'id': 's', 'type': 'file', 'root': '.'}, [
        {'id': 'd', 'type': 'record.change', 'source': 's', 'compare_fields': ['/digest']}])
    with pytest.raises(RuntimeError, match='observation-contracts-v1'):
        await control.start(parse(value, base_dir=tmp_path), wait_ready=False)
    with pytest.raises(RuntimeError, match='event-sources-v1'):
        await control.publish('existing', source_id='s', event_id='new', payload={})
    assert calls == ['hello']  # Neither mutation was sent; no daemon restart was attempted.
    await control.aclose()


async def test_real_file_json_uses_common_field_detector_without_source_comparison(tmp_path):
    import time

    path = tmp_path / 'task.json'
    path.write_text('{"status":"running","timestamp":1}')
    value = definition({'id': 's', 'type': 'file', 'root': str(tmp_path), 'format': 'json',
                        'include': ['*.json'], 'poll_interval_ms': 10}, [
        {'id': 'd', 'type': 'record.change', 'source': 's', 'compare_fields': ['/status']}])
    stop, baseline, delivered, checkpoints = asyncio.Event(), asyncio.Event(), [], []

    def emit(event):
        if event.kind == 'watching':
            baseline.set()

    async def save(value):
        checkpoints.append(value)

    async def deliver(value):
        delivered.append(value)
        stop.set()
        return DeliveryResult('succeeded')

    task = asyncio.create_task(observe(parse(value, base_dir=tmp_path), registration_id='file-fields',
        scanner=scan_async, deliver=deliver, emit=emit, clock=time.monotonic, sleep=asyncio.sleep,
        cancel=stop, save=save))
    try:
        async with asyncio.timeout(5):
            await baseline.wait()
            path.write_text('{"status":"running","timestamp":2}')
            while not checkpoints or json.loads(
                    checkpoints[-1]['detectors']['d']['records'][0]['value'])['timestamp'] != 2:
                await asyncio.sleep(.01)
            assert not delivered
            path.write_text('{"status":"failed","timestamp":3}')
            await task
        evidence = delivered[0].events[0].evidence[0]
        assert json.loads(evidence.before)['timestamp'] == 2
        assert json.loads(evidence.after)['status'] == 'failed'
    finally:
        stop.set()
        await task
