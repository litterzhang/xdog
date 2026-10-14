from dataclasses import replace

import pytest
from xdog.eyes.loader import parse
from xdog.eyes.models import FileState


def test_baseline_and_debounce(tmp_path, observer_data):
    from xdog.eyes.detectors.file_change import FileChangeDetector
    from xdog.eyes.sources.file import FileSnapshot

    detector = FileChangeDetector(parse(observer_data, base_dir=tmp_path).detectors[0])
    detector.observe(FileSnapshot((("a", FileState("old", 3)),)), now=0)
    assert detector.flush(now=1) == ()
    detector.observe(FileSnapshot((("a", FileState("new", 3)),)), now=1)
    assert detector.flush(now=1.4) == ()
    events = detector.flush(now=1.6)
    assert [event.type for event in events] == ["file.modified"]
    assert events[0].evidence[0].before.digest == "old"
    assert events[0].evidence[0].after.digest == "new"
    assert detector.flush(now=4) == ()


@pytest.mark.parametrize("before,middle,after,want", [
    ({}, {"a": "new"}, {}, []),
    ({"a": "old"}, {"a": "new"}, {"a": "old"}, []),
    ({"a": "old"}, {}, {"a": "new"}, ["file.modified"]),
    ({"a": "old"}, {"b": "old"}, {"b": "old"}, ["file.created", "file.deleted"]),
])
def test_net_changes(tmp_path, observer_data, before, middle, after, want):
    from xdog.eyes.detectors.file_change import FileChangeDetector
    from xdog.eyes.sources.file import FileSnapshot

    def snapshot(values):
        return FileSnapshot(tuple((path, FileState(digest, 3)) for path, digest in sorted(values.items())))

    detector = FileChangeDetector(parse(observer_data, base_dir=tmp_path).detectors[0])
    detector.observe(snapshot(before), now=0)
    detector.observe(snapshot(middle), now=1)
    detector.observe(snapshot(after), now=1.2)
    assert [event.type for event in detector.flush(now=2)] == want


def test_continuous_changes_flush_at_maximum_delay(tmp_path, observer_data):
    from xdog.eyes.detectors.file_change import FileChangeDetector
    from xdog.eyes.sources.file import FileSnapshot

    definition = replace(parse(observer_data, base_dir=tmp_path).detectors[0], max_batch_delay_ms=1000)
    detector = FileChangeDetector(definition)
    detector.observe(FileSnapshot(()), now=0)
    for now in (1.0, 1.3, 1.6, 1.9):
        detector.observe(FileSnapshot((("a", FileState(str(now), 3)),)), now=now)
    assert [event.type for event in detector.flush(now=2.01)] == ["file.created"]
