"""Parts snapshots retain full history without relaxing individual JSON bounds."""
from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.compaction import SUMMARY_TEMPLATE
from xdog.agent.core import AgentMessage, CustomAgentMessage
from xdog.agent.skills import SkillManager
from xdog.agent.snapshots import (
    MAX_SNAPSHOT_BYTES,
    CompactionSnapshot,
    decode_snapshot,
    decode_snapshot_messages,
    decode_snapshot_parts,
    encode_snapshot_messages,
)
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def agent(**kwargs) -> Agent:
    def never(*args, **kwargs):
        raise AssertionError("Snapshot operations must never start a model")
    return Agent(never, config=AgentConfig(model="offline/test", **kwargs))


def pair(identifier="call") -> tuple[AgentMessage, ...]:
    return (
        AssistantMessage(content=(ThinkingContent(thinking="reason", thinking_signature="signature"),
                                  ToolCall(id=identifier, name="inspect", arguments={"nested": ["original"]})),
                         stop_reason="toolUse"),
        ToolResultMessage(tool_call_id=identifier, tool_name="inspect", content=(TextContent(text="ok"),),
                          details={"receipt": [1]}, timestamp=3),
    )


def test_metadata_omits_messages_and_preserves_safe_versioned_fields():
    source = agent(system_prompt="RAW", options=StreamOptions(thinking="low", max_tokens=123),
                   tool_execution="sequential")
    source.replace_messages([UserMessage(content="saved"), *pair()])
    complete = source.dump()
    metadata = source.dump_metadata()
    assert "messages" not in metadata
    assert metadata == {key: value for key, value in complete.items() if key != "messages"}
    assert decode_snapshot_parts(metadata, source.messages) == decode_snapshot(complete)


def test_more_than_32_mib_history_restores_in_parts_but_whole_apis_remain_bounded():
    source = agent()
    source.replace_messages([UserMessage(content="x" * (2 * 1024 * 1024)) for _ in range(17)])
    metadata = source.dump_metadata()
    target = agent()
    rows = encode_snapshot_messages(source.messages)
    restored_messages = decode_snapshot_messages(rows)
    target.restore_parts(metadata, restored_messages, allowed_models=["offline/test"])
    assert target.messages == source.messages
    with pytest.raises(ValueError, match="byte limit"):
        source.dump()
    with pytest.raises(ValueError, match="byte limit"):
        target.restore({**metadata, "messages": [{"role": "user", "content": row.content}
                                                for row in source.messages]})


def test_single_message_with_multiple_images_above_32_mib_uses_per_part_bounds():
    # A valid tiny PNG plus inert trailing padding: each encoded image is below
    # 32 MiB, while this one typed user message exceeds the document limit.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS9sAAAAASUVORK5CYII="
    )
    data = base64.b64encode(png + b"\0" * (9 * 1024 * 1024)).decode()
    image = ImageContent(data=data, mime_type="image/png")
    source = agent()
    source.replace_messages([UserMessage(content=(TextContent(text="inspect"), image, image, image))])
    metadata = source.dump_metadata()
    target = agent()
    rows = encode_snapshot_messages(source.messages)
    target.restore_parts(metadata, decode_snapshot_messages(rows))
    assert target.messages == source.messages
    assert sum(len(part.data) for part in target.messages[0].content if isinstance(part, ImageContent)) \
        > MAX_SNAPSHOT_BYTES
    with pytest.raises(ValueError, match="byte limit"):
        source.dump()


@pytest.mark.parametrize("location", ["part", "string", "header", "metadata"])
def test_individual_json_units_still_have_original_byte_limit(location):
    metadata = agent().dump_metadata()
    oversized = "x" * (MAX_SNAPSHOT_BYTES + 1)
    messages: tuple[AgentMessage, ...] = ()
    if location == "part":
        messages = (UserMessage(content=(ImageContent(data=oversized, mime_type="image/png"),)),)
    elif location == "string":
        messages = (UserMessage(content=oversized),)
    elif location == "header":
        messages = (AssistantMessage(response_id=oversized),)
    else:
        metadata.update(base_system_prompt=oversized, system_prompt=oversized)
    with pytest.raises(ValueError, match="byte limit"):
        decode_snapshot_parts(metadata, messages)


def test_parts_detach_tool_arguments_details_and_metadata():
    source = agent()
    messages = pair()
    metadata = source.dump_metadata()
    candidate = decode_snapshot_parts(metadata, messages)
    messages[0].content[1].arguments["nested"].append("source changed")
    messages[1].details["receipt"].append(2)
    metadata["settings"]["tool_execution"] = "sequential"
    assert candidate.messages[0].content[1].arguments == {"nested": ["original"]}
    assert candidate.messages[1].details == {"receipt": [1]}
    assert candidate.settings["tool_execution"] == "parallel"
    target = agent()
    target.restore_parts(source.dump_metadata(), candidate.messages)
    candidate.messages[0].content[1].arguments["nested"].append("candidate changed")
    candidate.messages[1].details["receipt"].append(3)
    assert target.messages[0].content[1].arguments == {"nested": ["original"]}
    assert target.messages[1].details == {"receipt": [1]}


@pytest.mark.parametrize("messages", [
    (pair()[1],),
    (pair()[0], UserMessage(content="split"), pair()[1]),
    (*pair(), *pair()),
    (pair()[0], replace(pair()[1], tool_name="other")),
    (pair()[0],),
])
def test_whole_history_pairs_are_checked_and_restore_is_atomic(messages):
    target = agent()
    target.append_message(UserMessage(content="keep"))
    before = target.dump()
    with pytest.raises(ValueError):
        target.restore_parts(agent().dump_metadata(), messages)
    assert target.dump() == before


@pytest.mark.parametrize("messages", [(pair()[1],), (*pair(), *pair())])
def test_dump_metadata_checks_entire_original_history_not_just_pending_calls(messages):
    source = agent()
    source.replace_messages(messages)
    with pytest.raises(ValueError):
        source.dump_metadata()


def test_dump_metadata_rejects_busy_and_unconfirmed_work():
    source = agent()
    source._idle_event.clear()
    with pytest.raises(RuntimeError, match="safe snapshot"):
        source.dump_metadata()
    source._idle_event.set()
    source._unconfirmed_tools["old"] = "inspect"
    with pytest.raises(RuntimeError, match="safe snapshot"):
        source.dump_metadata()


def test_interrupted_parts_decode_for_diagnosis_but_never_restore_or_replay():
    source = agent()
    source.replace_messages([pair()[0]])
    raw = source.dump(recovery=True)
    metadata = {key: value for key, value in raw.items() if key != "messages"}
    candidate = decode_snapshot_parts(metadata, source.messages)
    assert candidate == decode_snapshot(raw)
    assert candidate.interrupted_tools[0].tool_call_id == "call"
    target = agent()
    before = target.dump()
    with pytest.raises(ValueError, match="reconciliation"):
        target.restore_parts(metadata, source.messages)
    assert target.dump() == before
    metadata["interrupted_tools"][0]["tool_name"] = "wrong"
    with pytest.raises(ValueError, match="known tool call"):
        decode_snapshot_parts(metadata, source.messages)


def test_wrong_model_and_unavailable_adapter_model_are_atomic():
    metadata = agent().dump_metadata()
    target = agent()
    before = target.dump()
    with pytest.raises(ValueError, match="not allowed"):
        target.restore_parts(metadata, (), allowed_models=["different"])
    assert target.dump() == before

    class Adapter:
        def resolve_model(self, name):
            return Model(id="different") if name == "different" else None

        def __call__(self, *args):
            raise AssertionError("No model execution")

    target.set_stream_fn(Adapter())
    with pytest.raises(ValueError, match="unavailable"):
        target.restore_parts(metadata, ())
    assert target.dump() == before


def test_skill_bindings_and_compaction_summary_share_restore_validation(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text("---\nname: guard\ndescription: guard\n---\nORIGINAL_BODY")
    manager = SkillManager(tmp_path, packaged={})

    def never(*args):
        raise AssertionError("No model execution")

    source = Agent(never, config=AgentConfig(model="offline/test", system_prompt="RAW"),
                   skills=manager, active_skills=["guard"])
    summary = "Saved work"
    source.replace_messages([UserMessage(content=SUMMARY_TEMPLATE.format(summary=summary)), *pair()])
    # replace_messages deliberately resets compaction; bind its actual matching snapshot.
    source._compaction_state = CompactionSnapshot(generation=2, summary=summary, compacted_messages=10)
    metadata = source.dump_metadata()
    target = Agent(never, config=AgentConfig(model="offline/test"), skills=manager)
    before = target.dump()
    with pytest.raises(ValueError, match="summary"):
        target.restore_parts(metadata, [UserMessage(content="wrong summary")])
    assert target.dump() == before
    target.restore_parts(metadata, source.messages)
    assert target._compaction_state == source._compaction_state
    assert str(target.state.system_prompt).count("ORIGINAL_BODY") == 1
    assert target.dump_metadata() == metadata
    path.write_text("---\nname: guard\ndescription: guard\n---\nCHANGED_BODY")
    fresh = Agent(never, config=AgentConfig(model="offline/test"), skills=manager)
    before = fresh.dump()
    with pytest.raises(ValueError, match="changed host binding"):
        fresh.restore_parts(metadata, source.messages)
    assert fresh.dump() == before
    missing = agent()
    before = missing.dump()
    with pytest.raises(ValueError, match="host binding"):
        missing.restore_parts(metadata, source.messages)
    assert missing.dump() == before


@pytest.mark.parametrize("mutation", ["messages", "version", "unknown", "options", "alias"])
def test_metadata_schema_and_aliases_are_not_bypassed(mutation):
    metadata = agent().dump_metadata()
    if mutation == "messages":
        metadata["messages"] = []
    elif mutation == "version":
        metadata["version"] = 999
    elif mutation == "unknown":
        metadata["tools"] = [{"name": "forged"}]
    elif mutation == "options":
        metadata["options"]["temperature"] = "bad"
    else:
        metadata["system_prompt"] = "different"
    with pytest.raises(ValueError):
        decode_snapshot_parts(metadata, ())


@pytest.mark.parametrize("bad", [object(), float("nan"), {1: "non-string key"}])
def test_nested_arguments_reject_non_json_and_do_not_silently_coerce(bad):
    messages = pair()
    messages[0].content[1].arguments["bad"] = bad
    with pytest.raises(ValueError):
        decode_snapshot_parts(agent().dump_metadata(), messages)


def test_unknown_custom_codec_is_never_imported_or_restored():
    with pytest.raises(ValueError, match="unsupported custom"):
        decode_snapshot_parts(agent().dump_metadata(), [CustomAgentMessage(type="arbitrary.module", data={})])


def test_restore_parts_keeps_host_runtime_tools_and_credentials():
    source = agent(options=StreamOptions(thinking="low", max_tokens=64))
    metadata = source.dump_metadata()
    target = agent(options=StreamOptions(metadata=(("api_key", "host-secret"),)))
    tool = AgentTool(name="host-tool", parameters={"type": "object"})
    target.set_tools([tool])
    target.restore_parts(copy.deepcopy(metadata), [UserMessage(content="data only")])
    assert target.state.tools == (tool,)
    assert target.options.metadata == (("api_key", "host-secret"),)
    assert target.options.thinking == "low" and target.options.max_tokens == 64
    assert "host-secret" not in str(target.dump_metadata())


def test_restore_parts_requires_open_idle_agent():
    target = agent()
    metadata = target.dump_metadata()
    target._idle_event.clear()
    try:
        with pytest.raises(RuntimeError, match="active run"):
            target.restore_parts(metadata, ())
    finally:
        target._idle_event.set()


async def test_closed_agent_can_export_metadata_but_not_restore():
    target = agent()
    metadata = target.dump_metadata()
    await target.aclose()
    assert target.dump_metadata() == metadata
    with pytest.raises(RuntimeError, match="closed"):
        target.restore_parts(metadata, ())


def test_persisted_message_codec_keeps_summary_string_timestamps_and_signatures():
    source = agent()
    summary = "History that must stay a string"
    source.replace_messages([
        UserMessage(content=SUMMARY_TEMPLATE.format(summary=summary), timestamp=1.25),
        UserMessage(content=(TextContent(text="signed", text_signature="user-signature"),), timestamp=2.5),
        *pair(),
        AssistantMessage(content=(TextContent(text="done", text_signature="assistant-signature"),),
                         response_id="response", timestamp=4),
    ])
    source._compaction_state = CompactionSnapshot(generation=1, summary=summary, compacted_messages=4)
    metadata = source.dump_metadata()
    rows = encode_snapshot_messages(source.messages)
    assert isinstance(rows[0]["content"], str)
    persisted_rows = json.loads(json.dumps(rows))
    restored = decode_snapshot_messages(persisted_rows)
    assert restored == source.messages
    assert restored[0].timestamp == 1.25 and isinstance(restored[0].content, str)
    target = agent()
    target.restore_parts(metadata, restored)
    assert target.messages == source.messages
    assert target._compaction_state == source._compaction_state
    # Neither public message codec aliases nested mutable argument/detail JSON.
    rows[2]["content"][1]["arguments"]["nested"].append("encoded change")
    persisted_rows[2]["content"][1]["arguments"]["nested"].append("wire change")
    assert restored[2].content[1].arguments == {"nested": ["original"]}
    assert source.messages[2].content[1].arguments == {"nested": ["original"]}


@pytest.mark.parametrize("messages", [
    (pair()[0],),
    (pair()[0], AssistantMessage(content=(TextContent(text="error"),), stop_reason="error")),
])
def test_message_codecs_explicitly_admit_interrupted_diagnostics_but_not_automatic_restore(messages):
    with pytest.raises(ValueError):
        encode_snapshot_messages(messages)
    rows = encode_snapshot_messages(messages, interrupted=True)
    with pytest.raises(ValueError):
        decode_snapshot_messages(rows)
    typed = decode_snapshot_messages(rows, interrupted=True)
    assert typed == messages
    source = agent()
    source.replace_messages(messages)
    metadata = source.dump(recovery=True)
    metadata.pop("messages")
    target = agent()
    before = target.dump()
    with pytest.raises(ValueError, match="reconciliation"):
        target.restore_parts(metadata, typed)
    assert target.dump() == before


@pytest.mark.parametrize("messages", [(pair()[1],), (*pair(), *pair()),
                                       (pair()[0], replace(pair()[1], tool_name="other"))])
def test_diagnostic_message_codecs_still_reject_orphan_duplicate_or_mismatched_pairs(messages):
    from xdog.agent.messages import messages_to_dicts

    with pytest.raises(ValueError):
        encode_snapshot_messages(messages, interrupted=True)
    with pytest.raises(ValueError):
        decode_snapshot_messages(messages_to_dicts(list(messages)), interrupted=True)


def test_message_codec_single_part_limits_and_non_json_fields_remain_bounded():
    oversized = "x" * (MAX_SNAPSHOT_BYTES + 1)
    messages = [UserMessage(content=(ImageContent(data=oversized, mime_type="image/png"),))]
    with pytest.raises(ValueError, match="byte limit"):
        encode_snapshot_messages(messages)
    with pytest.raises(ValueError, match="byte limit"):
        decode_snapshot_messages([{"role": "user", "content": [
            {"type": "image", "data": oversized, "mime_type": "image/png"}]}])
    with pytest.raises(ValueError, match="plain JSON"):
        decode_snapshot_messages([{"role": "user", "content": [{"type": "text", "text": object()}]}])
