"""Best-effort Anthropic-to-neutral projection diagnostics."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

_MAX_PATH_LENGTH = 256
_MAX_HEADER_LENGTH = 4096

_PROJECTED_CONTENT = frozenset({
    "text",
    "image",
    "thinking",
    "redacted_thinking",
    "tool_use",
    "tool_result",
})
_PROJECTED_CUSTOM_TOOL_FIELDS = frozenset({
    "type",
    "name",
    "description",
    "input_schema",
})


def _content_issues(messages: Any) -> set[str]:
    issues: set[str] = set()
    if not isinstance(messages, list):
        return issues
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system":
            issues.add("messages.role.system")
        if "clear_at" in message:
            issues.add("messages.clear_at")
        if "output_config" in message:
            issues.add("messages.output_config")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if not isinstance(kind, str):
                continue
            if kind not in _PROJECTED_CONTENT:
                issues.add(f"messages.content.{kind}")
                continue
            if "cache_control" in block:
                issues.add(f"messages.content.{kind}.cache_control")
            if kind == "image":
                source = block.get("source")
                source_type = source.get("type") if isinstance(source, dict) else None
                if source_type != "base64":
                    suffix = source_type if isinstance(source_type, str) else "unknown"
                    issues.add(f"messages.content.image.source.{suffix}")
            elif kind == "thinking" and "signature" in block:
                issues.add("messages.content.thinking.signature")
            elif kind == "redacted_thinking":
                issues.add("messages.content.redacted_thinking")
            elif kind == "tool_result":
                if block.get("is_error") is True:
                    issues.add("messages.content.tool_result.is_error")
                result_content = block.get("content")
                if isinstance(result_content, list):
                    for result_block in result_content:
                        if not isinstance(result_block, dict):
                            continue
                        result_type = result_block.get("type")
                        if result_type not in ("text", "image"):
                            suffix = result_type if isinstance(result_type, str) else "unknown"
                            issues.add(f"messages.content.tool_result.{suffix}")
    return issues


def _system_issues(system: Any) -> set[str]:
    issues: set[str] = set()
    if not isinstance(system, list):
        return issues
    for block in system:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind != "text":
            suffix = kind if isinstance(kind, str) else "unknown"
            issues.add(f"system.{suffix}")
        if "cache_control" in block:
            issues.add("system.text.cache_control")
    return issues


def _tool_issues(tools: Any) -> set[str]:
    issues: set[str] = set()
    if not isinstance(tools, list):
        return issues
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type")
        if kind not in (None, "custom"):
            suffix = kind if isinstance(kind, str) else "unknown"
            issues.add(f"tools.{suffix}")
            continue
        for field in tool:
            if field not in _PROJECTED_CUSTOM_TOOL_FIELDS:
                issues.add(f"tools.custom.{field}")
    return issues


def _safe_issues(issues: set[str]) -> tuple[str, ...]:
    encoded = tuple(sorted(
        quote(issue[:_MAX_PATH_LENGTH], safe="._-[]")
        for issue in issues
    ))
    kept: list[str] = []
    length = 0
    for issue in encoded:
        added = len(issue) + (1 if kept else 0)
        if length + added > _MAX_HEADER_LENGTH:
            break
        kept.append(issue)
        length += added
    return tuple(kept)


def projection_issues(
    body: dict[str, Any],
    best_effort_parameters: frozenset[str],
) -> tuple[str, ...]:
    """Return stable paths whose Anthropic semantics are lost or approximated."""
    issues = {
        key
        for key in body
        if key not in best_effort_parameters
    }
    issues.update(_content_issues(body.get("messages")))
    issues.update(_system_issues(body.get("system")))
    issues.update(_tool_issues(body.get("tools")))

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        if "display" in thinking:
            issues.add("thinking.display")
        if "block_binding" in thinking:
            issues.add("thinking.block_binding")

    output_config = body.get("output_config")
    if isinstance(output_config, dict) and "task_budget" in output_config:
        issues.add("output_config.task_budget")

    if body.get("service_tier") == "standard_only":
        issues.add("service_tier")
    return _safe_issues(issues)


def target_projection_issues(
    protocol: str | None,
    body: dict[str, Any],
) -> tuple[str, ...]:
    """Return supplied controls unsupported by one selected wire protocol."""
    issues: set[str] = set()
    if protocol == "openai-responses" and "stop_sequences" in body:
        issues.add("stop_sequences")
    if protocol == "openai-completions":
        tools = body.get("tools")
        if isinstance(tools, list) and any(
            isinstance(tool, dict)
            and isinstance(tool.get("type"), str)
            and tool["type"].startswith("web_search_")
            for tool in tools
        ):
            issues.add("tools.web_search")
    return _safe_issues(issues)
