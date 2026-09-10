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
