"""A business listener cannot depend on or be disabled by replaceable UI publish."""

from __future__ import annotations

from xdog.agent.supervision import ChildSupervisor

from packages.agent.tests.test_supervision import factory


async def test_child_settlement_listeners_survive_ui_replacement_and_other_listener_failure():
    owner = ChildSupervisor(factory)
    events, ui = [], []

    def broken(event):
        raise RuntimeError("disconnected optional listener")

    remove_broken = owner.subscribe(broken)
    remove = owner.subscribe(events.append)
    owner.publish = ui.append
    try:
        identifier = await owner.start("Inspect")
        await owner.control("wait", identifier)
        assert any(row["child_id"] == identifier and not row["snapshot"]["children"][0]["settling"] for row in events)
        assert ui
        remove()
        remove()
        remove_broken()
        size = len(events)
        owner.publish = lambda event: None
        second = await owner.start("Another")
        await owner.control("wait", second)
        assert len(events) == size
    finally:
        await owner.aclose()


async def test_listener_cannot_mutate_another_listener_or_ui_snapshot():
    owner = ChildSupervisor(factory)
    received, ui = [], []
    owner.subscribe(lambda event: event["snapshot"]["children"].clear())
    owner.subscribe(received.append)
    owner.publish = ui.append
    try:
        identifier = await owner.start("Inspect")
        await owner.control("wait", identifier)
        assert received[-1]["snapshot"]["children"][0]["id"] == identifier
        assert ui[-1]["snapshot"]["children"][0]["status"] == "completed"
        assert owner.capture_source(identifier).status == "completed"
    finally:
        await owner.aclose()
