from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_config import ChildPresets, parse_child_start
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream

PRESETS = {"reviewer": {"description": "Inspect without tools", "config": {
    "instructions": "REVIEW-INSTRUCTIONS", "tools": [], "inherit_context": False}}}


def test_preset_resolution_is_immutable_bounded_and_explicit_flags_override_saved_defaults():
    values = {"reviewer": {"description": "Inspect without tools", "config": {**PRESETS["reviewer"]["config"]}}}
    presets = ChildPresets(values)
    values["reviewer"]["config"]["instructions"] = "MUTATED"
    task, access, config = parse_child_start("--preset reviewer --label custom task", presets)
    assert task == "task" and access == "read-only" and config.preset == "reviewer" and config.label == "custom"
    assert config.instructions == "REVIEW-INSTRUCTIONS" and config.tools == () and not config.inherit_context
    snapshot = presets.snapshot()
    snapshot[0]["config"]["instructions"] = "MUTATED-SNAPSHOT"
    assert presets.resolve({"preset": "reviewer"}).instructions == "REVIEW-INSTRUCTIONS"
    assert (ChildPresets.from_snapshot(presets.snapshot()).resolve({"preset": "reviewer"})
            == presets.resolve({"preset": "reviewer"}))


@pytest.mark.parametrize("bad", [
    {"../outside": {}}, {"reviewer": {"command": "evil"}}, {"reviewer": {"config": {"access": "inherit"}}},
    {"reviewer": {"config": {"permission_mode": "allow-all"}}}, {"reviewer": {"config": {"preset": "other"}}},
    {"reviewer": {"config": {"max_tokens": 32001}}}, {"reviewer": {"description": "x" * 1001}},
    {str(index): {} for index in range(33)}, {"reviewer": {"config": {"tools": ["agent_spawn"]}}},
])
def test_preset_catalog_cannot_encode_execution_permissions_recursion_or_unbounded_resources(bad):
    with pytest.raises(ValueError):
        ChildPresets(bad)


async def test_preset_tool_and_direct_start_use_same_expanded_snapshot_and_survive_catalog_removal(tmp_path):
    seen = []
    def factory(spec, publish):
        seen.append(spec)
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    store = ChildStore(tmp_path)
    owner = ChildSupervisor(factory, store.save, begin=store.begin, presets=ChildPresets(PRESETS))
    try:
        control = owner.tools()[0]
        listed = await control.execute("presets", {"action": "presets"})
        assert "REVIEW-INSTRUCTIONS" in listed.content[0].text and not seen
        spawn = owner.tools()[0]
        result = await spawn.execute("spawn", {"action": "spawn", "task": "task", "config": {"preset": "reviewer"}})
        child = result.content[0].text
        record = await owner.control("wait", child)
        assert seen[0].preset == "reviewer" and not seen[0].inherit_context
        assert record["config"]["instructions"] == "REVIEW-INSTRUCTIONS"
        restored = ChildSupervisor(factory, records=store.load())
        try:
            assert (await restored.control("inspect", child))["config"] == record["config"]
            assert len(seen) == 1  # Empty new catalog does not resume or reinterpret old child work.
        finally:
            await restored.aclose()
        with pytest.raises(ValueError, match="Unknown child preset"):
            await owner.start("task", config={"preset": "unknown"})
        assert len(seen) == 1
    finally:
        await owner.aclose()


async def test_explicit_fresh_context_overrides_preset_without_erasing_template():
    specs = []
    def factory(spec, publish):
        specs.append(spec)
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(factory, presets=ChildPresets({"reader": {"config": {"instructions": "READ"}}}))
    try:
        child = await owner.start("task", inherit_context=False, config={"preset": "reader"})
        await owner.control("wait", child)
        assert not specs[0].inherit_context and specs[0].instructions == "READ"
    finally:
        await owner.aclose()
