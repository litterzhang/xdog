"""Versioned, data-only SDK snapshots; no Agent mutation or runtime binding.

``encode_snapshot`` is for an idle/safe boundary. ``interrupted=True`` explicitly
exports diagnostic recovery data, never a resumable checkpoint. The host must
call ``require_resumable`` and ``validate_skill_bindings`` before adopting decoded
data, bind its own provider/tools/permissions, and explicitly start the next run.
No callable, credential, approval, tool registration, automatic-compaction switch,
or model capability claim is restored. Model IDs and cwd are inert hints, not
provider routing instructions or permission to change directories.

The system prompt is the RAW host prefix, not AgentState's skill-composed prompt.
Recompose it once with explicitly rebound skills; consumed turn skills must not
be activated. Skill hashes detect drift, not authenticity. Hosts remain responsible
for snapshot provenance, sensitive message data, storage and business idempotency.

Versioned custom messages support only ``xdog.extension_context.v1``, the context
codec understood by Agent's default projection. Its canonical record is validated;
unknown application types require a future explicit codec contract, not silent
history loss. No snapshot-selected converter or executable registry is loaded.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, NoReturn

from xdog.agent.core import AgentMessage, AgentState
from xdog.agent.messages import dict_to_message, message_to_dict
from xdog.agent.skills.types import Skill
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    JsonSchemaFormat,
    StreamOptions,
    SystemPrompt,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolChoice,
    ToolResultMessage,
    UserMessage,
)

SNAPSHOT_FORMAT = "xdog.agent.snapshot"
SNAPSHOT_VERSION = 1
# Inline images are supported, but the parser is deliberately bounded.
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_ITEMS = 500_000
_COMPAT_ALIASES = frozenset({"system_prompt", "thinking", "temperature", "max_tokens"})
_OPTION_KEYS = frozenset({
    "thinking", "temperature", "max_tokens", "top_p", "stop_sequences",
    "parallel_tool_calls", "verbosity", "tool_choice", "response_format",
})
_SETTING_VALUES = {
    "tool_execution": {"sequential", "parallel"},
    "steering_mode": {"all", "one-at-a-time"},
    "follow_up_mode": {"all", "one-at-a-time"},
}


@dataclass(frozen=True)
class SkillSnapshot:
    slug: str
    scope: Literal["session", "turn"]
    content_hash: str
    lifecycle: Literal["active", "pending", "consumed"]


@dataclass(frozen=True)
class CompactionSnapshot:
    """Latest summary and cumulative archived-message boundary; no archive or policy.

    ``compacted_messages`` counts original messages removed across generations,
    not an index into the current (already compacted) history. The host's compactor
    owns the summary message representation in ``messages``.
    """

    generation: int = 0
    summary: str | None = None
    compacted_messages: int = 0


@dataclass(frozen=True)
class InterruptedTool:
    tool_call_id: str
    tool_name: str
    status: Literal["not_started", "cancelled", "unknown"] = "unknown"


@dataclass(frozen=True)
class Snapshot:
    """Validated detached values, not a live session or authority to run it.

    Nested message JSON and settings are detached copies, not recursively frozen.
    Treat this candidate as read-only and adopt it atomically after host checks.
    """

    messages: tuple[AgentMessage, ...]
    base_system_prompt: SystemPrompt
    model: str
    options: StreamOptions
    settings: dict[str, str]
    skills: tuple[SkillSnapshot, ...]
    compaction: CompactionSnapshot
    session_id: str | None
    run_id: str | None
    run_active: bool
    cwd: str | None
    error: str | None
    interrupted: bool
    interrupted_tools: tuple[InterruptedTool, ...]

    def require_resumable(self) -> None:
        """Reject every interrupted export, including tools whose outcome is unknown.

        This API never fills in synthetic results or retries an operation. The host
        must reconcile interrupted state separately, not clear a flag and replay.
        """
        if self.interrupted or self.interrupted_tools:
            raise ValueError("Interrupted snapshot requires host reconciliation; automatic continuation is forbidden")


@dataclass(frozen=True)
class LegacySnapshot:
    """Explicit partial patch: missing keys stay missing, including option keys.

    ``values`` is detached JSON suitable for the existing legacy restore path,
    after validation. Legacy composed prefixes cannot be split into raw prefix
    and skills reliably; this route makes no skill/compaction fidelity claim.
    """

    values: dict[str, Any]


def _fail(path: str, message: str) -> NoReturn:
    raise ValueError(f"{path}: {message}")


def _object(value: Any, path: str, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> None:
    if type(value) is not dict:
        _fail(path, "expected object")
    if required - value.keys():
        _fail(path, f"missing fields {sorted(required - value.keys())}")
    if value.keys() - required - optional:
        _fail(path, f"unknown fields {sorted(value.keys() - required - optional)}")


def _string(value: Any, path: str, *, nullable: bool = False, nonempty: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) is not str or (nonempty and not value):
        _fail(path, "expected string" + (" (nonempty)" if nonempty else ""))


def _integer(value: Any, path: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        _fail(path, f"expected integer >= {minimum}")


def _number(value: Any, path: str) -> None:
    if type(value) not in (int, float) or value < 0:
        _fail(path, "expected nonnegative finite number")


def _boolean(value: Any, path: str) -> None:
    if type(value) is not bool:
        _fail(path, "expected boolean")


def _enum(value: Any, path: str, choices: set[str]) -> None:
    if type(value) is not str or value not in choices:
        _fail(path, f"expected one of {sorted(choices)}")


def _list(value: Any, path: str) -> None:
    if type(value) is not list:
        _fail(path, "expected array")


def _json_copy(value: Any) -> Any:
    """Reject non-data objects BEFORE json can coerce keys or invoke encoders."""
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > _MAX_ITEMS:
            _fail("snapshot", "JSON nesting/item limit exceeded")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                _fail("snapshot", "JSON object keys must be strings")
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            _fail("snapshot", "only plain JSON data is permitted")
        elif type(item) is float and not math.isfinite(item):
            _fail("snapshot", "nonfinite number")
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            _fail("snapshot", "byte limit exceeded")
        return json.loads(text)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("snapshot: invalid JSON data") from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("snapshot", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read(value: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, (str, bytes)):
        try:
            size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
            if size > MAX_SNAPSHOT_BYTES:
                _fail("snapshot", "byte limit exceeded")
            value = json.loads(value, object_pairs_hook=_pairs)
        except (UnicodeError, RecursionError) as exc:
            raise ValueError("snapshot: invalid JSON") from exc
    copied = _json_copy(value)
    if type(copied) is not dict:
        _fail("snapshot", "expected object")
    return copied


def _prompt(value: Any, path: str) -> SystemPrompt:
    if value is None or type(value) is str:
        return value
    _list(value, path)
    blocks = []
    for block in value:
        _object(block, path, {"text"}, {"cache"})
        _string(block["text"], path)
        _boolean(block.get("cache", False), path)
        blocks.append(SystemPromptBlock(text=block["text"], cache=block.get("cache", False)))
    return tuple(blocks)


def _encode_prompt(prompt: SystemPrompt) -> Any:
    if prompt is None or isinstance(prompt, str):
        return prompt
    if type(prompt) is not tuple or any(type(block) is not SystemPromptBlock for block in prompt):
        _fail("base_system_prompt", "expected string, None or SystemPromptBlock tuple")
    return [{"text": block.text, "cache": block.cache} for block in prompt]


def _content(parts: Any, role: str, path: str) -> None:
    _list(parts, path)
    for part in parts:
        if type(part) is not dict:
            _fail(path, "expected content object")
        kind = part.get("type")
        if kind == "text":
            _object(part, path, {"type", "text"}, {"text_signature"})
            _string(part["text"], path)
            _string(part.get("text_signature"), path, nullable=True)
        elif kind == "image" and role != "assistant":
            _object(part, path, {"type", "data", "mime_type"})
            _string(part["data"], path)
            _string(part["mime_type"], path, nonempty=True)
        elif kind == "thinking" and role == "assistant":
            _object(part, path, {"type", "thinking"}, {"thinking_signature", "redacted"})
            _string(part["thinking"], path)
            _string(part.get("thinking_signature"), path, nullable=True)
            _boolean(part.get("redacted", False), path)
        elif kind == "toolCall" and role == "assistant":
            _object(part, path, {"type", "id", "name", "arguments"}, {"thought_signature"})
            _string(part["id"], path, nonempty=True)
            _string(part["name"], path, nonempty=True)
            if type(part["arguments"]) is not dict:
                _fail(path, "tool arguments must be an object")
            _string(part.get("thought_signature"), path, nullable=True)
        else:
            _fail(path, "unknown or invalid content codec")


def _message(raw: Any, path: str, *, validate_custom: bool = True) -> AgentMessage:
    if type(raw) is not dict:
        _fail(path, "expected message object")
    role = raw.get("role")
    if role == "custom":
        _object(raw, path, {"role", "type", "data"})
        _string(raw["type"], path, nonempty=True)
        if type(raw["data"]) is not dict:
            _fail(path, "custom data must be an object")
        if validate_custom:
            # Fixed SDK codec, never an import selected by snapshot data. Reuse
            # the projection's validator so malformed records cannot pass dump
            # only to disappear/fail at the next model request.
            from xdog.agent.extensions.context_message import TYPE, record
            from xdog.agent.mcp.stdio import McpError

            if raw["type"] != TYPE:
                _fail(path, "unsupported custom context codec")
            try:
                record(raw["data"])
            except (ValueError, TypeError, KeyError, McpError) as exc:
                raise ValueError(f"{path}: invalid custom context record") from exc
    elif role == "user":
        _object(raw, path, {"role", "content"}, {"timestamp"})
        if type(raw["content"]) is not str:
            _content(raw["content"], role, path)
    elif role == "assistant":
        _object(raw, path, {"role", "content"}, {
            "usage", "api", "provider", "model", "response_id", "stop_reason", "error_message", "timestamp",
        })
        _content(raw["content"], role, path)
        for key in ("api", "provider", "model"):
            _string(raw.get(key, ""), path)
        for key in ("response_id", "error_message"):
            _string(raw.get(key), path, nullable=True)
        _enum(raw.get("stop_reason", "stop"), path, {"stop", "length", "toolUse", "error", "aborted"})
        usage = raw.get("usage", {})
        counters = {"input", "output", "cache_read", "cache_write", "total_tokens"}
        _object(usage, path, set(), counters | {"cost"})
        for key in counters:
            _integer(usage.get(key, 0), path)
        cost = usage.get("cost", {})
        _object(cost, path, set(), {"input", "output", "cache_read", "cache_write", "total"})
        for value in cost.values():
            _number(value, path)
    elif role == "toolResult":
        _object(raw, path, {"role", "tool_call_id", "tool_name", "content"}, {"is_error", "details", "timestamp"})
        _string(raw["tool_call_id"], path, nonempty=True)
        _string(raw["tool_name"], path, nonempty=True)
        _boolean(raw.get("is_error", False), path)
        _content(raw["content"], role, path)
    else:
        _fail(path, "unknown message codec")
    if "timestamp" in raw:
        _number(raw["timestamp"], path)
    message = dict_to_message(raw)
    # The existing codec omits user/tool timestamps, user string representation,
    # and non-assistant text signatures. Keep these values in this versioned codec.
    if isinstance(message, (UserMessage, ToolResultMessage)):
        content = raw["content"]
        if type(content) is list:
            content = tuple(
                TextContent(text=p["text"], text_signature=p.get("text_signature"))
                if p["type"] == "text" else ImageContent(data=p["data"], mime_type=p["mime_type"])
                for p in content
            )
        message = replace(message, content=content, timestamp=raw.get("timestamp", 0.0))
    if isinstance(message, ToolResultMessage):
        message = replace(message, details=raw.get("details"))
    return message


def _encode_message(message: AgentMessage) -> dict[str, Any]:
    # Prevent the permissive legacy encoder from stringifying unknown objects.
    from xdog.agent.core import CustomAgentMessage

    if type(message) not in (UserMessage, AssistantMessage, ToolResultMessage, CustomAgentMessage):
        _fail("messages", "unregistered message class")
    if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
        if not isinstance(message.content, str):
            allowed = (TextContent, ThinkingContent, ToolCall) if isinstance(message, AssistantMessage) else (
                TextContent, ImageContent,
            )
            if any(type(part) not in allowed for part in message.content):
                _fail("messages", "unregistered content class")
        elif not isinstance(message, UserMessage):
            _fail("messages", "only user messages can have string content")
    raw = message_to_dict(message)
    if isinstance(message, AssistantMessage):
        for key in ("api", "provider", "model", "response_id", "error_message", "timestamp"):
            raw[key] = getattr(message, key)
        for part, encoded in zip(message.content, raw["content"], strict=True):
            if isinstance(part, TextContent):
                encoded["text_signature"] = part.text_signature
            elif isinstance(part, ThinkingContent):
                encoded["thinking_signature"] = part.thinking_signature
            elif isinstance(part, ToolCall):
                encoded["thought_signature"] = part.thought_signature
    if isinstance(message, (UserMessage, ToolResultMessage)):
        raw["timestamp"] = message.timestamp
        if isinstance(message.content, str):
            raw["content"] = message.content
        else:
            for user_part, encoded in zip(message.content, raw["content"], strict=True):
                if isinstance(user_part, TextContent) and user_part.text_signature is not None:
                    encoded["text_signature"] = user_part.text_signature
    if isinstance(message, ToolResultMessage):
        # Fail rather than silently lose non-JSON details.
        raw["details"] = message.details
    return raw


def _tool_pairs(messages: Sequence[AgentMessage], *, diagnostic: bool = False) -> dict[str, str]:
    pending: dict[str, str] = {}
    seen: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            if pending.get(message.tool_call_id) != message.tool_name:
                _fail(f"messages[{index}]", "orphan, duplicate or mismatched tool result")
            del pending[message.tool_call_id]
            continue
        if pending:
            # The established Agent error path appends an error assistant after
            # a failed tool finalizer. Preserve it in explicitly non-resumable
            # diagnostics, never treat it as a valid model-history boundary.
            diagnostic_error = (diagnostic and isinstance(message, AssistantMessage)
                                and message.stop_reason in {"error", "aborted"}
                                and not any(isinstance(part, ToolCall) for part in message.content))
            if not diagnostic_error:
                _fail(f"messages[{index}]", "message splits an unfinished tool batch")
        if isinstance(message, AssistantMessage):
            for part in message.content:
                if isinstance(part, ToolCall):
                    if part.id in seen:
                        _fail(f"messages[{index}]", "duplicate tool call ID")
                    seen.add(part.id)
                    pending[part.id] = part.name
    return pending


def _options(raw: Any) -> StreamOptions:
    _object(raw, "options", set(), _OPTION_KEYS)
    for key in ("thinking", "verbosity"):
        if raw.get(key) is not None:
            choices = {"low", "medium", "high"} | ({"minimal", "xhigh"} if key == "thinking" else set())
            _enum(raw[key], f"options.{key}", choices)
    for key in ("temperature", "top_p"):
        if raw.get(key) is not None:
            _number(raw[key], f"options.{key}")
    if raw.get("top_p") is not None and raw["top_p"] > 1:
        _fail("options.top_p", "must be <= 1")
    if raw.get("max_tokens") is not None:
        _integer(raw["max_tokens"], "options.max_tokens", 1)
    if raw.get("parallel_tool_calls") is not None:
        _boolean(raw["parallel_tool_calls"], "options.parallel_tool_calls")
    values = dict(raw)
    if raw.get("stop_sequences") is not None:
        _list(raw["stop_sequences"], "options.stop_sequences")
        for stop in raw["stop_sequences"]:
            _string(stop, "options.stop_sequences")
        values["stop_sequences"] = tuple(raw["stop_sequences"])
    if raw.get("tool_choice") is not None:
        choice = raw["tool_choice"]
        _object(choice, "tool_choice", {"type"}, {"name"})
        _enum(choice["type"], "tool_choice", {"auto", "any", "none", "tool"})
        _string(choice.get("name"), "tool_choice.name", nullable=choice["type"] != "tool", nonempty=True)
        if choice["type"] != "tool" and choice.get("name") is not None:
            _fail("tool_choice", "name is valid only for a named tool")
        values["tool_choice"] = ToolChoice(**choice)
    if raw.get("response_format") is not None:
        fmt = raw["response_format"]
        _object(fmt, "response_format", {"schema_json", "name"}, {"description", "strict"})
        _string(fmt["schema_json"], "response_format.schema_json")
        _read(fmt["schema_json"])  # JSON object only; no schema resolution or remote $ref fetch.
        _string(fmt["name"], "response_format.name", nonempty=True)
        _string(fmt.get("description"), "response_format.description", nullable=True)
        if fmt.get("strict") is not None:
            _boolean(fmt["strict"], "response_format.strict")
        values["response_format"] = JsonSchemaFormat(**fmt)
    return StreamOptions(**values)


def skill_snapshot(skill: Skill, *, lifecycle: Literal["active", "pending", "consumed"] = "active") -> SkillSnapshot:
    """Fingerprint a host-resolved skill without reading its path or discovering code.

    Paths are intentionally excluded so a trusted installation can relocate across
    processes. Hash instruction-bearing metadata as well as the unrendered body.
    """
    identity = {key: getattr(skill, key) for key in ("slug", "name", "description", "content", "scope", "updated")}
    digest = hashlib.sha256(json.dumps(_json_copy(identity), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    raw = {"slug": skill.slug, "scope": skill.scope, "content_hash": digest, "lifecycle": lifecycle}
    return _skill(raw)


def _skill(raw: Any) -> SkillSnapshot:
    _object(raw, "skills", {"slug", "scope", "content_hash", "lifecycle"})
    _string(raw["slug"], "skills.slug", nonempty=True)
    if re.fullmatch(r"(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)", raw["slug"]) is None:
        _fail("skills.slug", "invalid slug")
    _enum(raw["scope"], "skills.scope", {"session", "turn"})
    _string(raw["content_hash"], "skills.content_hash")
    if re.fullmatch(r"[0-9a-f]{64}", raw["content_hash"]) is None:
        _fail("skills.content_hash", "expected SHA-256 hex")
    lifecycles = {"active"} if raw["scope"] == "session" else {"active", "pending", "consumed"}
    _enum(raw["lifecycle"], "skills.lifecycle", lifecycles)
    return SkillSnapshot(**raw)


def capture_skill_snapshots(
    active_slugs: Sequence[str],
    turn_skills: Sequence[Skill],
    resolved_skills: Mapping[str, Skill | None],
    *,
    active_turn_slugs: Sequence[str] = (),
) -> tuple[SkillSnapshot, ...]:
    """Capture Agent's selection using trusted objects, without filesystem access.

    Pass ``_active_skill_slugs``, ``_turn_skills`` and the skills resolved when the
    prefix was last composed. Cache those session objects at activation: loading
    files anew at dump time could fingerprint instructions different from the
    ones actually used. Pending turn objects take precedence over the mapping.
    A selected turn skill absent from the pending tuple is recorded as consumed.
    Retired slugs already removed from the selection need no resurrection record.
    For an interrupted run, explicitly pass turn slugs that entered the model as
    ``active_turn_slugs``. Encoding these requires an active or interrupted run and a run_id;
    never infer model entry merely from a pending selection or streaming flag.
    """
    selected = tuple(dict.fromkeys(active_slugs))
    pending = {skill.slug: skill for skill in turn_skills}
    if len(pending) != len(turn_skills) or not pending.keys() <= set(selected):
        _fail("skills", "pending turn skills must be unique and selected")
    if any(skill.scope != "turn" for skill in turn_skills):
        _fail("skills", "pending tuple contains a non-turn skill")
    active_turns = set(active_turn_slugs)
    if not active_turns <= set(selected):
        _fail("skills", "active turn skills must be selected")
    records = []
    for slug in selected:
        skill = pending.get(slug) or resolved_skills.get(slug)
        if skill is None or skill.slug != slug:
            _fail("skills", f"missing trusted skill identity for {slug!r}")
        if slug in active_turns and skill.scope != "turn":
            _fail("skills", "active turn selection contains a non-turn skill")
        lifecycle: Literal["active", "pending", "consumed"] = (
            "active" if skill.scope == "session" or slug in active_turns
            else "pending" if slug in pending else "consumed"
        )
        records.append(skill_snapshot(skill, lifecycle=lifecycle))
    return tuple(records)


def validate_skill_bindings(snapshot: Snapshot, skills: Mapping[str, Skill | None]) -> tuple[Skill, ...]:
    """Check host-provided objects, never resolve paths from the snapshot.

    Return session skills and pending/active turn skills. Active turn bindings are
    for interrupted-run reconciliation only; ``require_resumable`` still blocks.
    Consumed skills remain
    audit data and need not be installed. Missing or changed active content fails
    explicitly; a host wanting new content must make a separate activation choice.
    """
    bound: list[Skill] = []
    for record in snapshot.skills:
        if record.lifecycle == "consumed":
            continue
        skill = skills.get(record.slug)
        if skill is None or skill_snapshot(skill, lifecycle=record.lifecycle) != record:
            _fail("skills", f"missing or changed host binding for {record.slug!r}")
        bound.append(skill)
    return tuple(bound)


def decode_snapshot(value: str | bytes | dict[str, Any]) -> Snapshot:
    """Validate the entire v1 document and return a detached candidate.

    No fallback to legacy: missing format/version and unknown future versions fail.
    Incomplete pairs require interrupted recovery data covering each pending call.
    Matching completed pairs can also carry unknown/cancelled recovery records:
    an error result does not prove an external side effect was rolled back.
    Such candidates fail ``require_resumable``. Compatibility aliases are optional
    and checked against canonical fields; they never override them.
    """
    return _decode_snapshot(_read(value))


def _decode_snapshot(raw: dict[str, Any], messages: tuple[AgentMessage, ...] | None = None) -> Snapshot:
    """Shared schema and whole-history checks after bounded JSON detachment."""
    _object(raw, "snapshot", {
        "format", "version", "messages", "base_system_prompt", "model", "options", "settings", "skills",
        "compaction", "session_id", "run_id", "run_active", "cwd", "error", "interrupted", "interrupted_tools",
    }, _COMPAT_ALIASES)
    if raw["format"] != SNAPSHOT_FORMAT:
        _fail("format", "unknown snapshot format")
    if type(raw["version"]) is not int or raw["version"] != SNAPSHOT_VERSION:
        _fail("version", "unsupported snapshot version")
    _string(raw["model"], "model")
    for key in ("session_id", "run_id", "cwd", "error"):
        _string(raw[key], key, nullable=True)
    prompt = _prompt(raw["base_system_prompt"], "base_system_prompt")
    options = _options(raw["options"])
    if "system_prompt" in raw and _prompt(raw["system_prompt"], "system_prompt") != prompt:
        _fail("system_prompt", "compatibility alias conflicts with base_system_prompt")
    option_aliases = {key: raw[key] for key in ("thinking", "temperature", "max_tokens") if key in raw}
    _options(option_aliases)
    for key, value in option_aliases.items():
        if value != getattr(options, key):
            _fail(key, "compatibility alias conflicts with options")
    _object(raw["settings"], "settings", set(), set(_SETTING_VALUES))
    for key, setting in raw["settings"].items():
        _enum(setting, f"settings.{key}", _SETTING_VALUES[key])
    _list(raw["messages"], "messages")
    if messages is None:
        messages = tuple(_message(message, f"messages[{i}]") for i, message in enumerate(raw["messages"]))
    _boolean(raw["interrupted"], "interrupted")
    pending = _tool_pairs(messages, diagnostic=raw["interrupted"])
    _boolean(raw["run_active"], "run_active")
    if raw["run_active"] and not raw["run_id"]:
        _fail("run_active", "active run requires a run_id")
    _list(raw["interrupted_tools"], "interrupted_tools")
    interrupted_tools = []
    recorded: dict[str, str] = {}
    for tool in raw["interrupted_tools"]:
        _object(tool, "interrupted_tools", {"tool_call_id", "tool_name", "status"})
        _string(tool["tool_call_id"], "interrupted_tools.tool_call_id", nonempty=True)
        _string(tool["tool_name"], "interrupted_tools.tool_name", nonempty=True)
        _enum(tool["status"], "interrupted_tools.status", {"not_started", "cancelled", "unknown"})
        if tool["tool_call_id"] in recorded:
            _fail("interrupted_tools", "duplicate call ID")
        recorded[tool["tool_call_id"]] = tool["tool_name"]
        interrupted_tools.append(InterruptedTool(**tool))
    known_calls = {
        part.id: part.name for message in messages if isinstance(message, AssistantMessage)
        for part in message.content if isinstance(part, ToolCall)
    }
    if recorded and not raw["interrupted"]:
        _fail("interrupted_tools", "recovery records require an interrupted snapshot")
    if not pending.keys() <= recorded.keys():
        _fail("interrupted_tools", "unfinished tool pairs require recovery metadata")
    for tool in interrupted_tools:
        if known_calls.get(tool.tool_call_id) != tool.tool_name:
            _fail("interrupted_tools", "recovery record must match a known tool call")
        if tool.tool_call_id not in pending and tool.status == "not_started":
            _fail("interrupted_tools", "paired calls can carry only unknown/cancelled recovery records")
    _list(raw["skills"], "skills")
    skills = tuple(_skill(record) for record in raw["skills"])
    if any(record.scope == "turn" and record.lifecycle == "active" for record in skills):
        if not (raw["interrupted"] or raw["run_active"]) or not raw["run_id"] or not raw["run_id"].strip():
            _fail("skills", "active turn skills require an active/interrupted snapshot with a nonempty run_id")
    if len({record.slug for record in skills}) != len(skills):
        _fail("skills", "duplicate slug")
    compact = raw["compaction"]
    _object(compact, "compaction", {"generation", "summary", "compacted_messages"})
    _integer(compact["generation"], "compaction.generation")
    _integer(compact["compacted_messages"], "compaction.compacted_messages")
    _string(compact["summary"], "compaction.summary", nullable=compact["generation"] == 0, nonempty=True)
    if compact["generation"] == 0:
        if compact["summary"] is not None or compact["compacted_messages"] != 0:
            _fail("compaction", "uncompacted state cannot have a summary or archived boundary")
    elif compact["compacted_messages"] < compact["generation"]:
        _fail("compaction", "each generation must archive at least one message")
    return Snapshot(
        messages=messages, base_system_prompt=prompt, model=raw["model"], options=options,
        settings=raw["settings"], skills=skills, compaction=CompactionSnapshot(**compact),
        session_id=raw["session_id"], run_id=raw["run_id"], run_active=raw["run_active"],
        cwd=raw["cwd"], error=raw["error"],
        interrupted=raw["interrupted"], interrupted_tools=tuple(interrupted_tools),
    )


def decode_snapshot_parts(
    metadata: str | bytes | dict[str, Any], messages: Sequence[AgentMessage],
) -> Snapshot:
    """Decode bounded metadata and separately bounded, detached typed history.

    Metadata uses the same bounded JSON reader and v1 decoder as a whole snapshot,
    but must omit ``messages``. Each message header (or string/custom message) and
    each content part has its own existing JSON byte/item/depth bound. Neither a
    complete history nor a multi-part message is submitted to the whole-document
    JSON copier. Full-history tool pairing and interrupted checks remain identical.
    This is data validation only; no tool, provider or application codec is loaded.
    """
    raw = _read(metadata)
    if "messages" in raw:
        _fail("metadata", "parts metadata must omit messages")
    raw["messages"] = []
    # Recovery references need the actual history, so both routes use the same
    # decoder instead of validating those references against an empty history.
    detached = tuple(_detach_message(message, index) for index, message in enumerate(messages))
    return _decode_snapshot(raw, detached)


def _detach_message_row(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if type(raw) is not dict:
        _fail(f"messages[{index}]", "expected message object")
    content = raw.get("content")
    if type(content) is list:
        header: dict[str, Any] = _json_copy({key: value for key, value in raw.items() if key != "content"})
        header["content"] = [_json_copy(part) for part in content]
        return header
    detached: dict[str, Any] = _json_copy(raw)
    return detached


def _detach_message(message: AgentMessage, index: int) -> AgentMessage:
    return _message(_detach_message_row(_encode_message(message), index), f"messages[{index}]")


def _check_message_boundary(messages: Sequence[AgentMessage], *, interrupted: bool) -> None:
    pending = _tool_pairs(messages, diagnostic=interrupted)
    if pending and not interrupted:
        _fail("messages", "unfinished tool pairs require an interrupted diagnostic boundary")


def encode_snapshot_messages(
    messages: Sequence[AgentMessage], *, interrupted: bool = False,
) -> list[dict[str, Any]]:
    """Encode detached, lossless v1 message rows without a whole-history byte cap.

    Each message header and content part uses the bounded snapshot JSON codec.
    User string content, timestamps and protocol signatures retain their types.
    Full-history pairs are checked, including duplicate call IDs and orphan or
    mismatched results. With interrupted=True, unfinished batches and the SDK's
    diagnostic error boundary are permitted for inspection, never replay. Such
    rows still need matching metadata and decode_snapshot_parts before adoption.
    This is not a codec for arbitrary chunks beginning with orphan tool results.
    """
    rows = [_detach_message_row(_encode_message(message), index) for index, message in enumerate(messages)]
    typed = tuple(_message(row, f"messages[{index}]") for index, row in enumerate(rows))
    _check_message_boundary(typed, interrupted=interrupted)
    return rows


def decode_snapshot_messages(
    rows: Sequence[dict[str, Any]], *, interrupted: bool = False,
) -> tuple[AgentMessage, ...]:
    """Decode v1 message rows with per-header/part bounds and whole-history pairs.

    Returned messages do not alias nested JSON in rows. interrupted=True admits
    only diagnostic boundaries, not authority to resume unconfirmed effects;
    decode_snapshot_parts must still validate the original recovery metadata.
    No executable codec is selected by data, and legacy formats are not inferred.
    """
    messages = tuple(_message(_detach_message_row(row, index), f"messages[{index}]")
                     for index, row in enumerate(rows))
    _check_message_boundary(messages, interrupted=interrupted)
    return messages


def encode_snapshot(
    state: AgentState,
    *,
    base_system_prompt: SystemPrompt,
    options: StreamOptions | None = None,
    settings: Mapping[str, str] | None = None,
    skills: Sequence[SkillSnapshot] = (),
    compaction: CompactionSnapshot = CompactionSnapshot(),
    session_id: str | None = None,
    run_id: str | None = None,
    run_active: bool = False,
    cwd: str | None = None,
    interrupted: bool = False,
    interrupted_tools: Sequence[InterruptedTool] = (),
    omit_messages: bool = False,
) -> dict[str, Any]:
    """Export a checked JSON dict, dropping live state and non-whitelisted options.

    Host must also guard against an active run/tool finalizer: AgentState alone
    cannot establish that boundary. With ``interrupted=True`` uncommitted stream
    fragments are deliberately omitted; committed history is retained and gated.
    All pending IDs need explicit status metadata (default status is unknown).
    With omit_messages, export metadata only but still validate all original
    history through the per-part codec and the same whole-history pair checks.
    """
    if not interrupted and (state.is_streaming or state.stream_message is not None or state.pending_tool_calls):
        _fail("state", "busy state cannot be exported as resumable")
    if not state.pending_tool_calls <= {tool.tool_call_id for tool in interrupted_tools}:
        _fail("state", "pending calls missing interrupted recovery metadata")
    opts = options or StreamOptions()
    option_data: dict[str, Any] = {key: getattr(opts, key) for key in _OPTION_KEYS}
    if opts.stop_sequences is not None:
        option_data["stop_sequences"] = list(opts.stop_sequences)
    for key in ("tool_choice", "response_format"):
        item = getattr(opts, key)
        if item is not None:
            expected = ToolChoice if key == "tool_choice" else JsonSchemaFormat
            if type(item) is not expected:
                _fail(f"options.{key}", "unregistered options class")
            option_data[key] = asdict(item)
    raw = {
        "format": SNAPSHOT_FORMAT, "version": SNAPSHOT_VERSION,
        **({} if omit_messages else {"messages": [_encode_message(message) for message in state.messages]}),
        "base_system_prompt": _encode_prompt(base_system_prompt), "model": state.model,
        "options": option_data, "settings": dict(settings or {}),
        "skills": [asdict(record) for record in skills], "compaction": asdict(compaction),
        "session_id": session_id, "run_id": run_id, "run_active": run_active, "cwd": cwd, "error": state.error,
        "interrupted": interrupted, "interrupted_tools": [asdict(tool) for tool in interrupted_tools],
    }
    # Read-only compatibility aliases for existing dump consumers. system_prompt
    # must remain raw, never the already skill-composed AgentState prompt.
    raw.update(system_prompt=raw["base_system_prompt"], thinking=option_data["thinking"],
               temperature=option_data["temperature"], max_tokens=option_data["max_tokens"])
    # Decode once before returning anything, so encoder and decoder have the same
    # validation boundary. The final copy detaches nested arguments/custom data.
    if omit_messages:
        decode_snapshot_parts(raw, state.messages)
    else:
        decode_snapshot(raw)
    return _read(raw)


def decode_legacy_snapshot(value: str | bytes | dict[str, Any]) -> LegacySnapshot:
    """Explicit safe subset of the old partial restore format, never auto-detected.

    Preserve absent keys and ``messages=None`` (legacy clear-history semantics).
    Unknown fields, invalid messages and unfinished pairs are rejected rather than
    silently repaired. No attempt to infer skills from a composed system prompt.
    Legacy custom types remain inert data for the existing host converter; this
    compatibility route does not claim default-projection fidelity for them.
    """
    raw = _read(value)
    _object(raw, "legacy", set(), {"messages", "system_prompt", "model", "thinking", "temperature", "max_tokens"})
    if "messages" in raw and raw["messages"] is not None:
        _list(raw["messages"], "legacy.messages")
        messages = tuple(_message(message, "legacy.messages", validate_custom=False) for message in raw["messages"])
        if _tool_pairs(messages):
            _fail("legacy.messages", "unfinished tool history is not resumable")
    if "system_prompt" in raw:
        _prompt(raw["system_prompt"], "legacy.system_prompt")
    if "model" in raw:
        _string(raw["model"], "legacy.model", nullable=True)
    _options({key: raw[key] for key in ("thinking", "temperature", "max_tokens") if key in raw})
    return LegacySnapshot(values=raw)
