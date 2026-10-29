import json
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.detectors.records import RecordDetector
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.models import ConditionDetectorDef, GithubSourceDef, HttpSourceDef, Predicate, RecordDetectorDef
from xdog.eyes.sources.records import Record, RecordSnapshot, record
from xdog.eyes.sources.remote import RemoteSampler

from packages.eyes.tests.test_http_source import Transport
from packages.eyes.tests.test_remote_config import external


def release(identifier=1, **values):
    return {
        "id": identifier,
        "html_url": f"https://github.com/o/r/releases/{identifier}",
        "tag_name": f"v{identifier}",
        "name": "Published",
        "body": "Notes",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-29T00:00:00Z",
        **values,
    }


async def test_release_identity_publication_and_notes_update_without_since_queries():
    seen = []
    replies = [
        [release(), release(2, draft=True), release(3, prerelease=True)],
        [release(body="Revised notes"), release(4, published_at=None)],
    ]

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=replies.pop(0))

    source = GithubSourceDef(id="r", repository="o/r", resource="releases")
    sampler = RemoteSampler(Transport(handle))
    detector = RecordDetector(RecordDetectorDef(id="d", source="r"), max_records=256)
    try:
        initial = await sampler.poll(source, {}, now=1)
        assert initial.snapshot.valid and [row.key for row in initial.snapshot.records] == ["1"]
        assert initial.snapshot.records[0].updated_at is None  # Releases need not expose updated_at.
        detector.observe(initial.snapshot, now=1)
        assert not detector.flush(now=1)
        changed = await sampler.poll(source, initial.state, now=1000000)  # No incremental-window gap.
        assert changed.snapshot.valid
        detector.observe(changed.snapshot, now=2)
        events = detector.flush(now=2)
        assert len(events) == 1 and events[0].type == "record.updated"
        assert "Revised notes" in events[0].evidence[0].after
        assert all(
            request.url.path.endswith("/releases") and set(request.url.params) == {"per_page"} for request in seen
        )
    finally:
        await sampler.close()


async def test_release_prerelease_opt_in_pagination_and_pre_filter_budget():
    seen = []

    def handle(request):
        seen.append(request)
        if "page=2" not in str(request.url):
            return httpx.Response(
                200,
                json=[release(1, prerelease=True)],
                headers={"link": '<https://api.github.com/repos/o/r/releases?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[release(2)])

    sampler = RemoteSampler(Transport(handle))
    source = GithubSourceDef(id="r", repository="o/r", resource="releases", include_prerelease=True)
    try:
        value = await sampler.poll(source, {}, now=1)
        assert {row.key for row in value.snapshot.records} == {"1", "2"}
        failed = await sampler.poll(replace(source, max_records=1, include_prerelease=False), {}, now=2)
        assert not failed.snapshot.valid and "last_success" not in failed.state
    finally:
        await sampler.close()


def test_projection_keeps_full_evidence_and_updates_latest_metadata_without_event():
    detector = RecordDetector(RecordDetectorDef(id="d", source="s", compare_fields=("/body",)), max_records=10)

    def snap(body, stamp):
        return RecordSnapshot(
            (record("one", {"body": body, "updated": stamp}, "https://example.test/one"),),
            stamp,
        )

    detector.observe(snap("text", 1), now=1)
    detector.observe(snap("text", 2), now=2)
    assert not detector.flush(now=2)
    assert json.loads(detector.checkpoint()["records"][0]["value"])["updated"] == 2
    detector.observe(snap("edited", 3), now=3)
    evidence = detector.flush(now=3)[0].evidence[0]
    assert json.loads(evidence.before)["updated"] == 2
    assert json.loads(evidence.after) == {"body": "edited", "updated": 3}


def test_value_condition_is_not_silenced_by_compare_fields():
    detector = RecordDetector(
        ConditionDetectorDef(id="d", source="s", all=(Predicate("/status", "eq", "failed"),)), max_records=10
    )
    for index, status in enumerate(["pending", "failed"]):
        item = record("one", {"title": "same", "status": status}, "https://example.test")
        detector.observe(RecordSnapshot((item,), index), now=index)
    assert detector.flush(now=2)[0].type == "condition.met"


def test_old_record_checkpoint_without_comparison_restores():
    detector = RecordDetector(RecordDetectorDef(id="d", source="s"), max_records=10)
    checkpoint = {
        "records": [{"key": "one", "value": "{}", "url": "https://example.test", "updated_at": None}],
        "failures": 0,
        "failed_since": None,
        "unavailable": False,
        "has_healthy": False,
    }
    detector.restore(checkpoint, now=1)
    detector.observe(RecordSnapshot((Record("one", "{}", "https://example.test"),), 2), now=2)
    assert not detector.flush(now=2)


async def test_source_has_no_comparison_policy_and_detector_rejects_missing_field():
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, json={"title": "no status"})))
    try:
        value = await sampler.poll(
            HttpSourceDef(id="s", url="https://example.test"), {"last_success": 1}, now=2
        )
        assert value.snapshot.valid and value.state["last_success"] == 2
        detector = RecordDetector(RecordDetectorDef(id="d", source="s", compare_fields=("/status",)), max_records=2)
        with pytest.raises(KeyError):
            detector.observe(value.snapshot, now=2)
        assert detector.checkpoint()["records"] is None
    finally:
        await sampler.close()


@pytest.mark.parametrize("kind", ["feed", "release"])
def test_normalized_new_source_roundtrip(tmp_path, observer_data, kind):
    source = (
        {"id": "outside", "type": "feed", "url": "https://example.test/rss"}
        if kind == "feed"
        else {
            "id": "outside",
            "type": "github",
            "repository": "o/r",
            "resource": "releases",
            "include_prerelease": True,
        }
    )
    config = external(observer_data, source)
    config["detectors"][0]["compare_fields"] = ["/title"]
    value = definition_data(parse(config, base_dir=tmp_path))
    assert definition_data(parse(value, base_dir=tmp_path)) == value


async def test_projection_roundtrip_checkpoint_preserves_latest_evidence():
    detector = RecordDetector(RecordDetectorDef(id="d", source="s", compare_fields=("/value",)), max_records=2)
    first = record("one", {"value": 1, "timestamp": 1}, "https://example.test")
    detector.observe(RecordSnapshot((first,), 1), now=1)
    restored = RecordDetector(detector.definition, max_records=2)
    restored.restore(json.loads(json.dumps(detector.checkpoint())), now=2)
    second = record("one", {"value": 1, "timestamp": 2}, "https://example.test")
    restored.observe(RecordSnapshot((second,), 2), now=2)
    assert not restored.flush(now=2)
    assert json.loads(restored.checkpoint()["records"][0]["value"])["timestamp"] == 2
