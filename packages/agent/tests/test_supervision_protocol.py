"""Independent child knowledge/configuration and canonical directional message facts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from xdog.agent.supervision import ChildSpec, child_message_status, child_prompt, child_task
from xdog.agent.supervision_config import ChildConfig, ChildPresets, parse_child_start
from xdog.ai.types import AssistantMessage, SystemPromptBlock, TextContent, ToolCall


def test_config_defaults_and_new_fields_are_immutable_and_json_roundtrippable():
    assert ChildConfig().skills == () and not ChildConfig().inherit_context
    assert ChildSpec("0123456789ab", "task").skills == ()
    value = {
        "system_prompt": "CHILD ONLY",
        "skills": ["review"],
        "tool_actions": {"write": [], "read": ["list", "show"]},
        "tools": ["read", "write"],
    }
    config = ChildConfig.from_dict(value)
    assert config.tool_actions == (("read", ("list", "show")), ("write", ()))
    value["tool_actions"]["read"].append("write")
    assert dict(config.tool_actions)["read"] == ("list", "show")
    assert ChildConfig.from_dict(json.loads(json.dumps(config.to_dict()))) == config
    assert ChildConfig.from_dict({"skills": None}).skills is None
    with pytest.raises(FrozenInstanceError):
        config.system_prompt = "changed"
    # Old positional argument slots are unchanged; new fields are append-only.
    spec = ChildSpec("0123456789ab", "task", "read-only", False, "label", "legacy", (), None, None, None, ())
    assert spec.instructions == "legacy" and spec.system_prompt == "" and spec.tool_actions == ()
    assert spec.config().to_dict()["skills"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"inherit_context": True},
        {"system_prompt": None},
        {"system_prompt": "x" * 16001},
        {"skills": "review"},
        {"skills": ["review", "review"]},
        {"skills": ["../secret"]},
        {"skills": [1]},
        {"tool_actions": None},
        {"tool_actions": []},
        {"tool_actions": {"agent": ["spawn"]}},
        {"tool_actions": {"read": "list"}},
        {"tool_actions": {"read": ["list", "list"]}},
        {"tool_actions": {"read": [1]}},
        {"tool_actions": {"read": [""]}},
        {"tool_actions": {"read": ["x\ny"]}},
        {"tool_actions": {"../read": ["list"]}},
        {"tools": [], "tool_actions": {"read": ["list"]}},
        {"mode": "allow-all"},
        {"actions": {}},
    ],
)
def test_config_does_not_accept_malformed_or_authority_override_fields(bad):
    with pytest.raises(ValueError):
        ChildConfig.from_dict(bad)


def test_presets_and_flags_keep_independent_prompt_skills_and_actions():
    presets = ChildPresets(
        {"reviewer": {"config": {"system_prompt": "REVIEW", "skills": ["review"], "tool_actions": {"read": ["list"]}}}}
    )
    config = presets.resolve({"preset": "reviewer", "skills": []})
    assert config.skills == () and config.system_prompt == "REVIEW"
    assert config.tool_actions == (("read", ("list",)),)
    assert ChildPresets.from_snapshot(presets.snapshot()).snapshot() == presets.snapshot()
    task, access, selected = parse_child_start('--system-prompt "Independent role" --skills none Knowledge task')
    assert task == "Knowledge task" and access == "read-only"
    assert selected.system_prompt == "Independent role" and selected.skills == ()


@pytest.mark.parametrize("parent", ["PARENT SECRET", (SystemPromptBlock(text="PARENT SECRET"),), None])
def test_child_prompt_ignores_every_parent_prompt_form_and_legacy_instructions(parent):
    spec = ChildSpec(
        id="0123456789ab", task="TASK KNOWLEDGE", system_prompt="CHILD ROLE", instructions="LEGACY ONLY", label="Review"
    )
    prompt = child_prompt(parent, spec)
    assert isinstance(prompt, str) and prompt.startswith("CHILD ROLE")
    assert "PARENT SECRET" not in prompt and "TASK KNOWLEDGE" not in prompt and "LEGACY ONLY" not in prompt
    assert "grant no additional permissions" in prompt


def test_legacy_instructions_are_user_task_material_and_rendering_never_changes_spec():
    spec = ChildSpec("0123456789ab", "EXPLICIT TASK", instructions="LEGACY KNOWLEDGE", system_prompt="CHILD SYSTEM")
    assert child_task(spec) == "Task materials:\nLEGACY KNOWLEDGE\n\nTask:\nEXPLICIT TASK"
    assert child_task(ChildSpec("0123456789ab", "EXPLICIT TASK")) == "EXPLICIT TASK"
    assert "LEGACY KNOWLEDGE" not in child_prompt("PARENT SECRET", spec)
    assert spec.task == "EXPLICIT TASK" and spec.instructions == "LEGACY KNOWLEDGE"


@pytest.mark.parametrize("stop,status", [("stop", "received"), ("error", "error"), ("aborted", "aborted")])
def test_tool_call_body_is_received_not_final_and_failure_status_is_not_lost(stop, status):
    message = AssistantMessage(
        content=(TextContent(text="TOOL PHASE BODY"), ToolCall(id="read1", name="read", arguments={})), stop_reason=stop
    )
    assert child_message_status(message) == status
    assert child_message_status(AssistantMessage(content=(TextContent(text="FINAL BODY"),))) == "completed"
