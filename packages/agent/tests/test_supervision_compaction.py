"""Supervised long tool runs use the native Agent policy and durable original-history exports."""

from __future__ import annotations

from dataclasses import replace

from xdog.agent.child_config import ChildConfig
from xdog.agent.child_model import resolve_child_model
from xdog.ai.types import AssistantMessage, StreamOptions
from xdog.ai.utils.event_stream import EventStream

from test_support.child_runtime.compaction import LongToolAdapter


def test_native_capacity_contract_does_not_fabricate_unknown_adapter_limits():
    adapter = LongToolAdapter()
    selected = resolve_child_model(
        adapter, "offline/parent", StreamOptions(), ChildConfig(model="offline/child"), 12000, True
    )
    assert selected.compaction(selected.stream(adapter)).output_reserve == adapter.metadata.max_tokens
    unknown = resolve_child_model(
        lambda *args: EventStream.empty(AssistantMessage()), "unknown", StreamOptions(), ChildConfig(), 0, None
    )
    assert unknown.compaction(lambda *args: EventStream.empty(AssistantMessage())) is None
    adapter.metadata = replace(adapter.metadata, context_window=4000)
    assert selected.stream(adapter).resolve_model(selected.id).context_window == 4000
    adapter.metadata = replace(adapter.metadata, max_tokens=0)
    unknown_output = resolve_child_model(adapter, "offline/child", StreamOptions(), ChildConfig(), 4000, True)
    assert unknown_output.compaction(unknown_output.stream(adapter)) is None
