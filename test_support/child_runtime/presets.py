from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.child_config import ChildPresets
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream

PRESETS = {
    "reviewer": {
        "description": "Inspect without tools",
        "config": {"instructions": "REVIEW-INSTRUCTIONS", "tools": [], "inherit_context": False},
    }
}


class TestChildPresets:
    async def test_preset_tool_and_direct_start_use_same_expanded_snapshot_and_survive_catalog_removal(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        seen = []

        def factory(spec, publish):
            seen.append(spec)
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        store = child_store_type(tmp_path)
        owner = runtime_factory(factory, store.save, begin=store.begin, presets=ChildPresets(PRESETS))
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
            restored = runtime_factory(factory, records=store.load())
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

    async def test_explicit_fresh_context_overrides_preset_without_erasing_template(self, *, runtime_factory):
        specs = []

        def factory(spec, publish):
            specs.append(spec)
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        owner = runtime_factory(factory, presets=ChildPresets({"reader": {"config": {"instructions": "READ"}}}))
        try:
            child = await owner.start("task", inherit_context=False, config={"preset": "reader"})
            await owner.control("wait", child)
            assert not specs[0].inherit_context and specs[0].instructions == "READ"
        finally:
            await owner.aclose()
