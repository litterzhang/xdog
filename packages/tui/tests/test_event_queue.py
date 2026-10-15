from dataclasses import dataclass

import pytest
from xdog.tui.event_queue import EventQueue, thaw_event


@dataclass(frozen=True)
class Request:
    arguments: dict


def test_queue_snapshots_nested_payloads_and_frozen_dataclasses() -> None:
    queue = EventQueue()
    arguments = {"values": ["original"]}
    source = {"type": "request", "request": Request(arguments), "arguments": arguments}
    queue.put(source)
    arguments["values"].append("changed")
    source["type"] = "changed"
    event = queue.get_nowait()
    assert event["type"] == "request"
    assert event["arguments"]["values"] == ("original",)
    assert event["request"].arguments["values"] == ("original",)
    with pytest.raises(TypeError):
        event["type"] = "changed"
    with pytest.raises(TypeError):
        event["request"].arguments["values"] = ()
    local = thaw_event(event)
    local["arguments"]["values"].append("ui local")
    assert event["arguments"]["values"] == ("original",)


def test_snapshot_preserves_tuple_content_contract() -> None:
    queue = EventQueue()
    queue.put({"content": ("one", "two"), "history": ["a", "b"]})
    local = thaw_event(queue.get_nowait())
    assert local["content"] == ("one", "two")
    assert local["history"] == ["a", "b"]


def test_drain_yields_even_when_producer_keeps_queue_nonempty(monkeypatch) -> None:
    monkeypatch.setattr("xdog.tui.event_queue.time.monotonic", lambda: 0.0)
    events = EventQueue()
    events.put({"sequence": 0})
    seen = []

    def handle(event):
        seen.append(event["sequence"])
        events.put({"sequence": event["sequence"] + 1})

    assert events.drain(handle, max_events=4) == 4
    assert seen == [0, 1, 2, 3]
    assert events.qsize() == 1


def test_drain_time_budget_keeps_remaining_events_in_order(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("xdog.tui.event_queue.time.monotonic", lambda: now[0])
    events = EventQueue()
    for sequence in range(5):
        events.put({"sequence": sequence})
    seen = []

    def handle(event):
        seen.append(event["sequence"])
        now[0] += 0.005

    assert events.drain(handle, max_seconds=0.008) == 2
    assert seen == [0, 1]
    assert events.drain(handle, max_seconds=0.008) == 2
    assert events.drain(handle, max_seconds=0.008) == 1
    assert seen == list(range(5))


def test_drain_does_not_swallow_handler_errors() -> None:
    import queue
    events = EventQueue()
    events.put({"type": "final"})

    def handle(event):
        raise queue.Empty("handler failure")

    with pytest.raises(queue.Empty, match="handler failure"):
        events.drain(handle)


@pytest.mark.parametrize("limits", [{"max_events": 0}, {"max_seconds": 0}])
def test_drain_rejects_nonpositive_budgets(limits) -> None:
    with pytest.raises(ValueError):
        EventQueue().drain(lambda event: None, **limits)


def test_immutable_paths_roundtrip_inside_frozen_domain_snapshots(tmp_path):
    @dataclass(frozen=True)
    class Snapshot:
        path: object
        arguments: dict

    queue = EventQueue()
    source = {'names':['before']}
    queue.put({'snapshot':Snapshot(tmp_path,source)})
    source['names'].append('after')
    local = thaw_event(queue.get_nowait())['snapshot']
    assert local.path==tmp_path
    assert local.arguments=={'names':['before']}
