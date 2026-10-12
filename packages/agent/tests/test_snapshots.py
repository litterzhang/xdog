"""Offline codec tests. These do not claim public Agent dump/restore integration."""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from xdog.agent.core import AgentState, AgentTool, CustomAgentMessage
from xdog.agent.messages import messages_to_dicts
from xdog.agent.skills.types import Skill
from xdog.agent.snapshots import (
    SNAPSHOT_FORMAT,
    CompactionSnapshot,
    InterruptedTool,
    Snapshot,
    capture_skill_snapshots,
    decode_legacy_snapshot,
    decode_snapshot,
    encode_snapshot,
    skill_snapshot,
    validate_skill_bindings,
)
from xdog.ai.types import (
    AssistantMessage,
    CostBreakdown,
    ImageContent,
    JsonSchemaFormat,
    StreamOptions,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolChoice,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _custom_context(details: Any = None) -> CustomAgentMessage:
    return CustomAgentMessage(type="xdog.extension_context.v1", data={
        "id": "a" * 32, "extension": "test", "session_id": "session-1", "branch_id": "",
        "invocation": "b" * 32, "method": "tools/call", "custom_type": "note",
        "content": [{"type": "text", "text": "Context that must reach the model"}],
        "display": False, "details": details, "delivery": "next_turn", "trigger_turn": False,
    })


def _host_skills() -> dict[str, Skill]:
    return {
        "guard": Skill(slug="guard", name="Guard", description="Checks", content="Check work.", updated="v1"),
        "task": Skill(slug="task", name="Task", content="Build once.", scope="turn"),
        "retired": Skill(slug="retired", name="Retired", content="Already used.", scope="turn"),
    }


def _sample() -> tuple[AgentState, dict[str, Any]]:
    image = ImageContent(data="aGVsbG8=", mime_type="image/png")
    state = AgentState(
        model="host-logical-model", system_prompt="DO NOT SAVE THIS COMPOSED PREFIX",
        messages=(
            UserMessage(content="Earlier task context."),
            UserMessage(content="init", timestamp=1.25),
            UserMessage(content=(TextContent(text="inspect", text_signature="user-sig"), image), timestamp=2),
            AssistantMessage(
                content=(
                    ThinkingContent(thinking="reason", thinking_signature="sig", redacted=True),
                    TextContent(text="working", text_signature="text-sig"),
                    ToolCall(id="a", name="inspect", arguments={"path": "src", "nested": [None, True, 1.5]},
                             thought_signature="call-sig"),
                    ToolCall(id="b", name="report_progress", arguments={}),
                ),
                api="wire-label", provider="inert-provider-label", model="model-label", response_id="response",
                usage=Usage(input=10, output=4, cache_read=3, cache_write=2, total_tokens=19,
                            cost=CostBreakdown(input=0.1, output=0.2, cache_read=0.03, cache_write=0.02, total=0.35)),
                stop_reason="toolUse", timestamp=3,
            ),
            ToolResultMessage(tool_call_id="b", tool_name="report_progress", content=(TextContent(text="ok"),),
                              details={"receipt": [1, "two"]}, timestamp=4),
            ToolResultMessage(tool_call_id="a", tool_name="inspect", content=(image, TextContent(text="found")),
                              is_error=True, details={"code": "missing"}, timestamp=5),
            AssistantMessage(content=(TextContent(text="initialized"),), timestamp=6),
        ),
    )
    skills = _host_skills()
    kwargs = {
        "base_system_prompt": (SystemPromptBlock(text="Raw instructions", cache=True), SystemPromptBlock(text="task")),
        "options": StreamOptions(
            thinking="high", temperature=0.5, max_tokens=256, top_p=0.9, stop_sequences=("END",),
            parallel_tool_calls=False, verbosity="low", tool_choice=ToolChoice(type="tool", name="inspect"),
            response_format=JsonSchemaFormat.from_schema({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
        ),
        "settings": {"tool_execution": "parallel", "steering_mode": "all", "follow_up_mode": "one-at-a-time"},
        "skills": (
            skill_snapshot(skills["guard"]), skill_snapshot(skills["task"], lifecycle="pending"),
            skill_snapshot(skills["retired"], lifecycle="consumed"),
        ),
        "compaction": CompactionSnapshot(generation=2, summary="Earlier task context.", compacted_messages=12),
        "session_id": "session-1", "run_id": "run-init", "cwd": "/inert/workspace/hint",
    }
    return state, kwargs


def _encoded() -> dict[str, Any]:
    state, kwargs = _sample()
    return encode_snapshot(state, **kwargs)


def _reencode(snapshot: Snapshot) -> dict[str, Any]:
    return encode_snapshot(
        AgentState(model=snapshot.model, messages=snapshot.messages, error=snapshot.error),
        base_system_prompt=snapshot.base_system_prompt, options=snapshot.options, settings=snapshot.settings,
        skills=snapshot.skills, compaction=snapshot.compaction, session_id=snapshot.session_id,
        run_id=snapshot.run_id, cwd=snapshot.cwd, interrupted=snapshot.interrupted,
        interrupted_tools=snapshot.interrupted_tools,
    )


@pytest.mark.parametrize("wire", ["dict", "str", "bytes"])
def test_structured_roundtrip(wire: str) -> None:
    state, kwargs = _sample()
    raw = _encoded()
    payload = raw if wire == "dict" else json.dumps(raw)
    if wire == "bytes":
        payload = payload.encode()
    decoded = decode_snapshot(payload)
    decoded.require_resumable()
    assert decoded.messages == state.messages
    assert decoded.options == kwargs["options"]
    assert decoded.base_system_prompt == kwargs["base_system_prompt"]
    assert decoded.skills == kwargs["skills"]
    assert decoded.compaction == kwargs["compaction"]
    assert decoded.settings == kwargs["settings"]
    assert decoded.session_id == "session-1"
    assert decoded.run_id == "run-init"
    assert decoded.cwd == "/inert/workspace/hint"
    assert _reencode(decoded) == raw
    assert "DO NOT SAVE" not in json.dumps(raw)


@pytest.mark.parametrize("prompt", [None, "", "raw text", ()])
def test_raw_prefix_shape_is_preserved(prompt: Any) -> None:
    raw = encode_snapshot(AgentState(system_prompt="already composed"), base_system_prompt=prompt)
    assert decode_snapshot(raw).base_system_prompt == prompt


def test_empty_signatures_and_metadata_are_not_lost() -> None:
    state = AgentState(messages=(
        AssistantMessage(content=(TextContent(text="", text_signature=""),
                                  ThinkingContent(thinking="", thinking_signature="")),
                         response_id="", error_message=""),
    ))
    assert decode_snapshot(encode_snapshot(state, base_system_prompt="")).messages == state.messages


def test_no_aliasing_into_live_state_or_source_document() -> None:
    state, kwargs = _sample()
    raw = encode_snapshot(state, **kwargs)
    raw["messages"][3]["content"][2]["arguments"]["nested"].append("changed")
    call = state.messages[3].content[2]
    assert call.arguments["nested"] == [None, True, 1.5]
    decoded = decode_snapshot(raw)
    raw["messages"][3]["content"][2]["arguments"]["nested"].append("later")
    assert decoded.messages[3].content[2].arguments["nested"][-1] == "changed"
    decoded.messages[4].details["receipt"].append("candidate edit")
    assert state.messages[4].details["receipt"] == [1, "two"]


def test_options_whitelist_drops_handles_capabilities_and_routing() -> None:
    execute = Mock(side_effect=AssertionError("tool executed"))
    state = AgentState(tools=(AgentTool(name="danger", execute=execute),))
    options = StreamOptions(cancel=asyncio.Event(), web_search=True, prompt_cache_key="private",
                            metadata=(("api_key", "secret"),), service_tier="premium", temperature=0.4)
    raw = encode_snapshot(state, base_system_prompt="", options=options)
    encoded = json.dumps(raw)
    assert all(word not in encoded for word in ("danger", "secret", "private", "premium", "cancel", "web_search"))
    restored = decode_snapshot(raw)
    assert restored.options.cancel is None
    assert restored.options.web_search is False
    assert restored.options.metadata is None
    assert restored.options.temperature == 0.4
    execute.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("format", "other"), ("version", 2), ("version", -1), ("version", True), ("version", "1"),
    ("messages", {}), ("model", 42), ("interrupted", 1), ("session_id", []), ("error", False),
    ("base_system_prompt", [{"text": "hi", "cache": "yes"}]),
    ("settings", {"allow_all": True}), ("settings", {"tool_execution": "unsafe"}),
    ("options", {"max_tokens": True}), ("options", {"max_tokens": 0}),
    ("options", {"temperature": -1}), ("options", {"top_p": 1.1}), ("options", {"thinking": "unknown"}),
    ("options", {"cancel": None}), ("options", {"web_search": True}), ("options", {"api_key": "secret"}),
    ("options", {"stop_sequences": "END"}), ("options", {"tool_choice": {"type": "tool"}}),
    ("options", {"tool_choice": {"type": "any", "name": "grant-me"}}),
    ("options", {"response_format": {"name": "bad", "schema_json": "[]"}}),
    ("compaction", {"generation": 0, "summary": "invalid", "compacted_messages": 0}),
    ("compaction", {"generation": 1, "summary": None, "compacted_messages": 2}),
    ("compaction", {"generation": 3, "summary": "summary", "compacted_messages": 2}),
])
def test_invalid_document_fields_fail_before_any_state_change(field: str, value: Any) -> None:
    state, _ = _sample()
    before = copy.deepcopy(state)
    raw = _encoded()
    raw[field] = value
    with pytest.raises(ValueError):
        decode_snapshot(raw)
    assert state == before


@pytest.mark.parametrize("key", ["provider", "credentials", "tools", "permissions", "module", "auto_compaction"])
def test_unknown_authority_fields_are_not_accepted(key: str) -> None:
    raw = _encoded()
    raw[key] = {"import": "os.system", "command": "touch SHOULD_NOT_EXIST"}
    with pytest.raises(ValueError, match="unknown fields"):
        decode_snapshot(raw)


@pytest.mark.parametrize("message", [
    {"role": "alien", "content": []},
    {"role": "user", "content": [{"type": "alien"}]},
    {"role": "assistant", "content": [{"type": "image", "data": "x", "mime_type": "image/png"}]},
    {"role": "assistant", "content": [{"type": "text", "text": 4}]},
    {"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "redacted": "yes"}]},
    {"role": "assistant", "content": [{"type": "toolCall", "id": "a", "name": "x", "arguments": []}]},
    {"role": "assistant", "content": [], "usage": {"input": -1}},
    {"role": "assistant", "content": [], "usage": {"input": True}},
    {"role": "assistant", "content": [], "usage": {"cost": {"total": "1"}}},
    {"role": "assistant", "content": [], "stop_reason": "new-codec"},
    {"role": "user", "content": "x", "timestamp": -1},
    {"role": "custom", "type": "module", "data": []},
    {"role": "user", "content": "x", "execute": "os.system"},
])
def test_malformed_messages_are_not_silently_coerced_or_dropped(message: Any) -> None:
    raw = _encoded()
    raw["messages"] = [message]
    with pytest.raises(ValueError):
        decode_snapshot(raw)


@pytest.mark.parametrize("change", ["orphan", "duplicate_result", "wrong_name", "duplicate_call", "split", "missing"])
def test_tool_pair_integrity(change: str) -> None:
    raw = _encoded()
    messages = raw["messages"]
    if change == "orphan":
        messages[4]["tool_call_id"] = "not-a-call"
    elif change == "duplicate_result":
        messages.insert(5, copy.deepcopy(messages[4]))
    elif change == "wrong_name":
        messages[4]["tool_name"] = "not-the-tool"
    elif change == "duplicate_call":
        messages[3]["content"][3]["id"] = "a"
    elif change == "split":
        messages.insert(5, {"role": "user", "content": "intervening user"})
    else:
        messages.pop(5)
    with pytest.raises(ValueError):
        decode_snapshot(raw)


@pytest.mark.parametrize("status", ["not_started", "cancelled", "unknown"])
def test_interrupted_tools_are_inspectable_but_never_replayed(status: Any) -> None:
    state = AgentState(messages=(
        UserMessage(content="mutate"),
        AssistantMessage(content=(ToolCall(id="a", name="mutate", arguments={"idempotency_key": "key"}),)),
    ), pending_tool_calls=frozenset({"a"}), is_streaming=True)
    raw = encode_snapshot(state, base_system_prompt="", interrupted=True,
                          interrupted_tools=(InterruptedTool("a", "mutate", status),))
    restored = decode_snapshot(raw)
    assert restored.interrupted_tools == (InterruptedTool("a", "mutate", status),)
    assert restored.messages == state.messages
    with pytest.raises(ValueError, match="reconciliation"):
        restored.require_resumable()
    raw["interrupted"] = False
    with pytest.raises(ValueError):
        decode_snapshot(raw)


def test_partial_parallel_batch_preserves_completed_and_unknown_results() -> None:
    state, kwargs = _sample()
    state = replace(state, messages=state.messages[:5], pending_tool_calls=frozenset({"a"}))
    raw = encode_snapshot(state, **kwargs, interrupted=True, interrupted_tools=(InterruptedTool("a", "inspect"),))
    assert decode_snapshot(raw).messages == state.messages
    for tools in ([], [InterruptedTool("b", "report_progress")], [InterruptedTool("a", "wrong")],
                  [InterruptedTool("a", "inspect"), InterruptedTool("a", "inspect")]):
        with pytest.raises(ValueError):
            encode_snapshot(state, **kwargs, interrupted=True, interrupted_tools=tools)


@pytest.mark.parametrize("changes", [
    {"is_streaming": True}, {"stream_message": AssistantMessage()}, {"pending_tool_calls": frozenset({"x"})},
])
def test_busy_state_cannot_be_claimed_resumable(changes: Any) -> None:
    with pytest.raises(ValueError, match="busy"):
        encode_snapshot(AgentState(**changes), base_system_prompt="")


def test_interrupted_stream_fragment_is_omitted_and_continuation_blocked() -> None:
    state = AgentState(messages=(UserMessage(content="task"),), is_streaming=True,
                       stream_message=AssistantMessage(content=(TextContent(text="partial"),)), error="cancelled")
    restored = decode_snapshot(encode_snapshot(state, base_system_prompt="", interrupted=True))
    assert restored.messages == state.messages
    assert restored.error == "cancelled"
    with pytest.raises(ValueError):
        restored.require_resumable()


def test_skill_rebinding_checks_identity_and_does_not_resurrect_consumed_skills() -> None:
    restored = decode_snapshot(_encoded())
    host = _host_skills()
    del host["retired"]
    host["guard"] = replace(host["guard"], path=Path("/new/trusted/install/guard/SKILL.md"))
    assert [skill.slug for skill in validate_skill_bindings(restored, host)] == ["guard", "task"]
    for field, value in (("content", "new instructions"), ("scope", "turn"), ("name", "changed"), ("updated", "v2")):
        changed = dict(host)
        changed["guard"] = replace(host["guard"], **{field: value})
        with pytest.raises(ValueError):
            validate_skill_bindings(restored, changed)
    with pytest.raises(ValueError, match="missing or changed"):
        validate_skill_bindings(restored, {})


@pytest.mark.parametrize("field,value", [
    ("slug", "../evil"), ("scope", "forever"), ("content_hash", "not-a-hash"), ("lifecycle", "pending"),
])
def test_invalid_skill_records(field: str, value: str) -> None:
    raw = _encoded()
    raw["skills"][0][field] = value
    with pytest.raises(ValueError):
        decode_snapshot(raw)


def test_duplicate_skill_and_consumed_session_fail() -> None:
    raw = _encoded()
    raw["skills"].append(raw["skills"][0])
    with pytest.raises(ValueError, match="duplicate slug"):
        decode_snapshot(raw)
    raw = _encoded()
    raw["skills"][0]["lifecycle"] = "consumed"
    with pytest.raises(ValueError):
        decode_snapshot(raw)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object(), lambda: None, {1: "bad-key"}, ("tuple",)])
def test_non_json_nested_payloads_fail(bad: Any) -> None:
    state = AgentState(messages=(CustomAgentMessage(data={"nested": bad}),))
    with pytest.raises(ValueError):
        encode_snapshot(state, base_system_prompt="")


def test_non_json_tool_details_fail_instead_of_disappearing() -> None:
    state, kwargs = _sample()
    state = replace(state, messages=state.messages[:4] + (
        replace(state.messages[4], details=object()),
    ) + state.messages[5:])
    with pytest.raises(ValueError, match="plain JSON"):
        encode_snapshot(state, **kwargs)


def test_bounded_json_rejects_cycles_depth_duplicates_and_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _encoded()
    raw["messages"][4]["details"]["cycle"] = raw
    with pytest.raises(ValueError, match="limit"):
        decode_snapshot(raw)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        decode_snapshot('{"version":1,"version":2}')
    with pytest.raises(ValueError, match="nonfinite"):
        decode_snapshot('{"value":NaN}')
    with pytest.raises(ValueError):
        decode_snapshot(b"\xff")
    with pytest.raises(ValueError):
        decode_snapshot("[" * 2000)
    raw = _encoded()
    monkeypatch.setattr("xdog.agent.snapshots.MAX_SNAPSHOT_BYTES", 100)
    with pytest.raises(ValueError, match="byte limit"):
        decode_snapshot(raw)


def test_code_looking_data_stays_inert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sentinel = tmp_path / "executed"
    payload = {"__module__": "os", "callable": "system", "command": f"touch {sentinel}",
               "provider": "evil", "permissions": "allow-all", "api_key": "ordinary-sensitive-message-data"}
    raw = encode_snapshot(AgentState(messages=(_custom_context(payload),)),
                          base_system_prompt="")
    import_module = Mock(side_effect=AssertionError("dynamic import"))
    monkeypatch.setattr(importlib, "import_module", import_module)
    snapshot = decode_snapshot(raw)
    assert snapshot.messages[0].data["details"] == payload
    assert not sentinel.exists()
    import_module.assert_not_called()


@pytest.mark.parametrize("patch", [{}, {"temperature": 0.2}, {"messages": None}, {"messages": []},
                                    {"model": None}, {"system_prompt": [{"text": "old prefix"}]}])
def test_legacy_is_an_explicit_partial_route(patch: Any) -> None:
    assert decode_legacy_snapshot(patch).values == patch
    with pytest.raises(ValueError):
        decode_snapshot(patch)


def test_legacy_current_dump_shape_and_missing_fields_preserved() -> None:
    state, _ = _sample()
    legacy = {"messages": messages_to_dicts(list(state.messages)), "system_prompt": "old composed prefix",
              "model": "model", "thinking": None, "temperature": 0.4, "max_tokens": None}
    restored = decode_legacy_snapshot(legacy)
    assert restored.values == legacy
    legacy["messages"].clear()
    assert restored.values["messages"]
    assert "skills" not in restored.values
    with pytest.raises(ValueError):
        decode_legacy_snapshot(_encoded())
    with pytest.raises(ValueError):
        decode_legacy_snapshot({"messages": [{"role": "user", "content": "ok"}, {"role": "bad"}]})
    with pytest.raises(ValueError):
        decode_legacy_snapshot({"messages": messages_to_dicts(list(state.messages[:4]))})


# Each invocation below is a fresh interpreter. No mock provider or Agent is
# involved: this proves codec persistence, not the parent's SDK integration.
_SUBPROCESS = r'''
import json, os, runpy, socket, sys
ns = runpy.run_path(sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("network access forbidden")
socket.socket = forbidden
mode = sys.argv[2]
if mode == "init":
    raw = ns["_encoded"]()
else:
    raw = json.loads(sys.stdin.read())
    snapshot = ns["decode_snapshot"](raw)
    snapshot.require_resumable()
    assert ns["_reencode"](snapshot) == raw
    bound = ns["validate_skill_bindings"](snapshot, ns["_host_skills"]())
    assert [skill.slug for skill in bound] == ["guard", "task"]
    assert snapshot.compaction.generation == 2
    assert snapshot.messages[3].content[0].thinking_signature == "sig"
    assert snapshot.messages[2].content[1].data == "aGVsbG8="
    assert snapshot.base_system_prompt[0].text == "Raw instructions"
    if mode == "build":
        # An explicit host-side history edit, NOT a replay or model/tool call.
        snapshot = ns["replace"](snapshot, messages=snapshot.messages + (
            ns["UserMessage"](content="build"), ns["AssistantMessage"](content=(ns["TextContent"](text="built"),)),
        ), run_id="run-build")
    else:
        assert snapshot.run_id == "run-build"
        assert snapshot.messages[-1].content[0].text == "built"
    raw = ns["_reencode"](snapshot)
print(json.dumps({"pid": os.getpid(), "snapshot": raw}))
'''


def test_true_offline_three_process_roundtrip(tmp_path: Path) -> None:
    snapshot_file = tmp_path / "snapshot.json"
    pids = set()
    for mode in ("init", "build", "verify"):
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS, str(Path(__file__).resolve()), mode],
            input=snapshot_file.read_text() if snapshot_file.exists() else "",
            text=True, capture_output=True, check=True, timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        result = json.loads(completed.stdout)
        pids.add(result["pid"])
        assert result["pid"] != os.getpid()
        snapshot_file.write_text(json.dumps(result["snapshot"]))
    assert len(pids) == 3
    final = decode_snapshot(snapshot_file.read_bytes())
    assert final.run_id == "run-build"
    assert len(final.messages) == 9
    assert json.loads(snapshot_file.read_text())["format"] == SNAPSHOT_FORMAT


def test_capture_current_agent_skill_selection_without_rereading_files() -> None:
    trusted = _host_skills()
    pending = trusted["task"]
    # A manager may now return different contents: use the pending object that
    # actually belongs to this Agent, not a newly read same-name skill.
    newer = {**trusted, "task": replace(pending, content="changed on disk")}
    records = capture_skill_snapshots(("guard", "task", "retired"), (pending,), newer)
    assert records == _sample()[1]["skills"]
    assert [record.slug for record in capture_skill_snapshots(("guard",), (), trusted)] == ["guard"]
    for selection, turns, bindings in (
        (("guard",), (pending,), trusted),
        (("task",), (pending, pending), trusted),
        (("guard",), (trusted["guard"],), trusted),
        (("guard",), (), {}),
    ):
        with pytest.raises(ValueError):
            capture_skill_snapshots(selection, turns, bindings)


def test_explicit_trusted_manager_rebinding_and_drift(tmp_path: Path) -> None:
    from xdog.agent.skills.manager import SkillManager

    manager = SkillManager(tmp_path, packaged={})
    manager.save_skill("guard", "Original session instructions", name="guard")
    initial = manager.load_skill("guard")
    assert initial is not None
    state = AgentState(system_prompt="composed text must not be serialized")
    records = capture_skill_snapshots(("guard",), (), {"guard": initial})
    candidate = decode_snapshot(encode_snapshot(state, base_system_prompt="Raw base", skills=records))
    # Host explicitly chooses the manager and performs reads. The decoder never
    # discovers a manager or imports/installs a source named by the document.
    bound = validate_skill_bindings(candidate, {record.slug: manager.load_skill(record.slug)
                                               for record in candidate.skills})
    assert bound == (initial,)
    manager.save_skill("guard", "Changed instructions", name="guard")
    with pytest.raises(ValueError, match="missing or changed"):
        validate_skill_bindings(candidate, {"guard": manager.load_skill("guard")})
    with pytest.raises(ValueError, match="missing or changed"):
        validate_skill_bindings(candidate, {"guard": None})
    assert candidate.base_system_prompt == "Raw base"


@pytest.mark.parametrize("prompt", [None, "raw", (SystemPromptBlock(text="raw", cache=True),)])
def test_compatibility_aliases_mirror_canonical_data(prompt: Any) -> None:
    options = StreamOptions(thinking="high", temperature=0.25, max_tokens=1024)
    raw = encode_snapshot(AgentState(system_prompt="composed"), base_system_prompt=prompt, options=options)
    assert raw["system_prompt"] == raw["base_system_prompt"]
    assert raw["thinking"] == "high"
    assert raw["temperature"] == 0.25
    assert raw["max_tokens"] == 1024
    assert decode_snapshot(raw).options == options
    for key in ("system_prompt", "thinking", "temperature", "max_tokens"):
        del raw[key]
    assert decode_snapshot(raw).base_system_prompt == prompt


@pytest.mark.parametrize("key,value", [
    ("system_prompt", "composed or different"), ("thinking", "low"),
    ("temperature", 0.75), ("max_tokens", 1024), ("temperature", True), ("max_tokens", True),
])
def test_conflicting_or_malformed_aliases_rejected(key: str, value: Any) -> None:
    raw = _encoded()
    raw[key] = value
    with pytest.raises(ValueError):
        decode_snapshot(raw)


def test_canonical_change_cannot_be_hidden_by_compatibility_alias() -> None:
    raw = _encoded()
    raw["options"]["thinking"] = "low"
    with pytest.raises(ValueError, match="alias conflicts"):
        decode_snapshot(raw)
    raw = _encoded()
    raw["system_prompt"][0]["text"] = "changed alias"
    assert raw["base_system_prompt"][0]["text"] == "Raw instructions"
    with pytest.raises(ValueError, match="alias conflicts"):
        decode_snapshot(raw)


def test_known_custom_context_roundtrips_and_projects() -> None:
    from xdog.agent.extensions.context_message import llm_message

    message = _custom_context({"opaque": [1, 2]})
    raw = encode_snapshot(AgentState(messages=(message,)), base_system_prompt="")
    decoded = decode_snapshot(raw)
    assert decoded.messages == (message,)
    projected = llm_message(decoded.messages[0])
    assert projected is not None
    assert projected.content[-1].text == "Context that must reach the model"
    assert "opaque" not in str(projected.content)
    raw["messages"][0]["data"]["content"] = []
    with pytest.raises(ValueError):
        decode_snapshot(raw)


@pytest.mark.parametrize("kind", ["custom", "compaction", "python", "future.context.v2"])
def test_unknown_custom_context_codecs_rejected_in_versioned_snapshots(kind: str) -> None:
    message = CustomAgentMessage(type=kind, data={"text": "must not disappear"})
    with pytest.raises(ValueError, match="unsupported custom context codec"):
        encode_snapshot(AgentState(messages=(message,)), base_system_prompt="")
    raw = _encoded()
    raw["messages"] = messages_to_dicts([message])
    with pytest.raises(ValueError, match="unsupported custom context codec"):
        decode_snapshot(raw)
    # Explicit legacy route keeps its documented host-converter responsibility.
    assert decode_legacy_snapshot({"messages": raw["messages"]}).values["messages"] == raw["messages"]


def test_active_interrupted_turn_skill_preserves_run_identity_and_binding() -> None:
    trusted = _host_skills()
    records = capture_skill_snapshots(("guard", "task"), (trusted["task"],), trusted,
                                     active_turn_slugs=("task",))
    assert records[1].lifecycle == "active"
    candidate = decode_snapshot(encode_snapshot(AgentState(), base_system_prompt="", skills=records,
                                                interrupted=True, run_id="entered-run"))
    assert candidate.run_id == "entered-run"
    assert validate_skill_bindings(candidate, trusted) == (trusted["guard"], trusted["task"])
    with pytest.raises(ValueError, match="reconciliation"):
        candidate.require_resumable()


@pytest.mark.parametrize("interrupted,run_id", [(False, "run"), (True, None), (True, ""), (True, "  ")])
def test_active_turn_skill_requires_interruption_and_run_id(interrupted: bool, run_id: Any) -> None:
    active = skill_snapshot(_host_skills()["task"], lifecycle="active")
    with pytest.raises(ValueError, match="active turn skills require"):
        encode_snapshot(AgentState(), base_system_prompt="", skills=(active,), interrupted=interrupted, run_id=run_id)


@pytest.mark.parametrize("status", ["unknown", "cancelled"])
def test_paired_cancelled_result_can_still_have_unknown_side_effect(status: Any) -> None:
    state, kwargs = _sample()
    raw = encode_snapshot(state, **kwargs, interrupted=True,
                          interrupted_tools=(InterruptedTool("a", "inspect", status),))
    candidate = decode_snapshot(raw)
    assert candidate.messages == state.messages
    assert candidate.interrupted_tools[0].status == status
    with pytest.raises(ValueError, match="reconciliation"):
        candidate.require_resumable()
    raw["interrupted"] = False
    with pytest.raises(ValueError, match="recovery records require"):
        decode_snapshot(raw)


@pytest.mark.parametrize("record", [InterruptedTool("not-a-call", "inspect"),
                                  InterruptedTool("a", "wrong-name"), InterruptedTool("a", "inspect", "not_started")])
def test_paired_recovery_records_must_reference_real_calls(record: InterruptedTool) -> None:
    state, kwargs = _sample()
    with pytest.raises(ValueError):
        encode_snapshot(state, **kwargs, interrupted=True, interrupted_tools=(record,))


def test_mixed_paired_and_unpaired_recovery_requires_every_unpaired_call() -> None:
    state, kwargs = _sample()
    state = replace(state, messages=state.messages[:5])  # b has a result; a does not.
    records = (InterruptedTool("a", "inspect"), InterruptedTool("b", "report_progress", "cancelled"))
    candidate = decode_snapshot(encode_snapshot(state, **kwargs, interrupted=True, interrupted_tools=records))
    assert candidate.interrupted_tools == records
    with pytest.raises(ValueError, match="unfinished tool pairs require"):
        encode_snapshot(state, **kwargs, interrupted=True, interrupted_tools=records[1:])


@pytest.mark.parametrize("field,value", [("method", []), ("details", "x" * 32769), ("content", [])])
def test_malformed_known_custom_records_fail_as_validation_errors(field: str, value: Any) -> None:
    message = _custom_context()
    message.data[field] = value
    with pytest.raises(ValueError, match="invalid custom context record"):
        encode_snapshot(AgentState(messages=(message,)), base_system_prompt="")
