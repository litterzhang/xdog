"""Validated inert MCP resource/prompt data; never inserts or fetches URLs itself."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from xdog.agent.mcp.completion import bounded, request_arguments, template_arguments
from xdog.agent.mcp.content import content as validate_content
from xdog.agent.mcp.content import resource as validate_resource
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError


@dataclass(frozen=True, slots=True)
class McpEntry:
    server: str
    kind: str
    name: str
    title: str
    description: str = ""
    arguments: tuple[tuple[str, str, bool], ...] = ()


@dataclass(frozen=True, slots=True)
class McpBlock:
    role: str
    text: str = ""
    mime_type: str = ""
    data: str = ""
    uri: str = ""
    content_type: str = ""  # Empty only for legacy documents with no discriminator.

    def __post_init__(self) -> None:
        if (not isinstance(self.content_type, str) or self.content_type not in (
                "", "text", "image", "audio", "resource_text", "resource_blob", "resource_link")
                or any(not isinstance(value, str) for value in (
                    self.role, self.text, self.mime_type, self.data, self.uri))):
            raise ValueError("Invalid MCP document block type/fields")
        if self.content_type in ("text", "resource_text", "resource_link") and self.data:
            raise ValueError("Text MCP document block cannot carry binary data")

    @property
    def is_binary(self) -> bool:
        return self.content_type in ("image", "audio", "resource_blob") or bool(self.data)


@dataclass(frozen=True, slots=True)
class McpDocument:
    server: str
    kind: str
    name: str
    blocks: tuple[McpBlock, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(f"[{block.role}] {block.uri or self.name}\n" +
                           ((f"[Binary {block.mime_type or 'unknown'} · "
                             f"{len(base64.b64decode(block.data)):,} bytes; raw export available]"
                             + ("\n" + block.text if block.text else "")) if block.is_binary else block.text)
                           for block in self.blocks)

    def draft_text(self) -> str:
        if any(block.is_binary for block in self.blocks):
            raise McpError("Binary MCP content cannot be silently dropped into text; export/inspect it explicitly")
        return f"[User-selected untrusted MCP {self.kind} from {self.server}]\n{self.text}"


def _metadata_text(value: dict[str, Any], payload: set[str]) -> str:
    extra = {key: item for key, item in value.items() if key not in payload}
    return "[MCP content metadata]\n" + json_value(extra) if extra else ""


def resource_blocks(contents: object, *, role: str = "resource") -> tuple[McpBlock, ...]:
    if not isinstance(contents, list) or not contents or len(contents) > 128:
        raise McpError("Invalid/empty resource contents")
    blocks = []
    for raw in contents:
        item = validate_resource(raw)
        mime = item.get("mimeType", "")
        extra = _metadata_text(item, {"uri", "mimeType", "text", "blob"})
        if "text" in item:
            blocks.append(McpBlock(role, text=item["text"] + ("\n" + extra if extra else ""),
                                   uri=item["uri"], mime_type=mime, content_type="resource_text"))
        else:
            blocks.append(McpBlock(role, text=extra, data=item["blob"], uri=item["uri"], mime_type=mime,
                                   content_type="resource_blob"))
    return tuple(blocks)


def resource_link_block(content: dict[str, Any], *, role: str) -> McpBlock:
    """Retain complete inert link metadata; a link is not fetched resource content."""
    content = validate_content(content)
    uri = content["uri"]
    encoded = json_value(content)
    return McpBlock(role, text="[Resource link metadata; NOT fetched]\n" + encoded,
                    uri=uri, mime_type=content.get("mimeType", ""), content_type="resource_link")


def prompt_blocks(messages: object) -> tuple[McpBlock, ...]:
    if not isinstance(messages, list) or not messages or len(messages) > 128:
        raise McpError("Invalid/empty prompt messages")
    blocks = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            raise McpError("Invalid prompt role; system instructions are not accepted")
        content = validate_content(message.get("content"), image_projection=False)
        kind = content.get("type")
        if kind == "text" and isinstance(content.get("text"), str):
            extra = _metadata_text(content, {"type", "text"})
            blocks.append(McpBlock(message["role"], text=content["text"] + ("\n" + extra if extra else ""),
                                   content_type="text"))
        elif kind == "resource":
            embedded = resource_blocks([content.get("resource")], role=message["role"])
            extra = _metadata_text(content, {"type", "resource"})
            blocks.extend(McpBlock(block.role, block.text + ("\n" + extra if extra else ""),
                                   block.mime_type, block.data, block.uri, block.content_type) for block in embedded)
        elif kind == "resource_link":
            blocks.append(resource_link_block(content, role=message["role"]))
        elif kind in {"image", "audio"} and isinstance(content.get("data"), str):
            mime = content.get("mimeType")
            if not isinstance(mime, str):
                raise McpError("Invalid prompt MIME type")
            try:
                base64.b64decode(content["data"], validate=True)
            except ValueError as exc:
                raise McpError("Invalid prompt binary encoding") from exc
            extra = _metadata_text(content, {"type", "data", "mimeType"})
            blocks.append(McpBlock(message["role"], text=extra, mime_type=mime, data=content["data"],
                                   content_type=kind))
        else:
            raise McpError("Unsupported prompt block; data was not silently dropped")
    return tuple(blocks)


def context_entry(server: str, kind: str, value: Any) -> McpEntry:
    identity = {"resource": "uri", "template": "uriTemplate", "prompt": "name"}[kind]
    if (not isinstance(value, dict) or not isinstance(value.get(identity), str) or not value[identity]
            or not isinstance(value.get("name"), str) or not isinstance(value.get("description", ""), str)):
        raise McpError("Invalid MCP catalog entry")
    title = value.get("title", value["name"])
    if not isinstance(title, str):
        raise McpError("Invalid MCP entry title")
    args: list[tuple[str, str, bool]] = []
    if kind == "template":
        args.extend((name, "URI template variable", False) for name in template_arguments(value[identity]))
    if kind == "prompt":
        arguments = value.get("arguments", [])
        if not isinstance(arguments, list) or len(arguments) > 128:
            raise McpError("Invalid prompt arguments")
        seen = set()
        for argument in arguments:
            if (not isinstance(argument, dict) or not isinstance(argument.get("name"), str)
                    or not argument["name"] or argument["name"] in seen
                    or not isinstance(argument.get("description", ""), str)
                    or type(argument.get("required", False)) is not bool):
                raise McpError("Invalid/duplicate prompt argument")
            seen.add(argument["name"])
            args.append((argument["name"], argument.get("description", ""), argument.get("required", False)))
    return McpEntry(server, kind, value[identity], title, value.get("description", ""), tuple(args))


def resource_completion_document(server: str, uri: str, request: dict[str, str]) -> McpDocument:
    """Local reviewed completion data, not a claim of a fresh server resource read."""
    import json
    bounded(server, 256, empty=False)
    bounded(uri, 8192, empty=False)
    argument, value, context = request_arguments(request)
    if argument in context:
        raise McpError("Current completion argument cannot also be context")
    text = ("Operator-selected/edited opaque MCP completion data; NOT a resource read or resolved URI.\n"
            + json.dumps({"resource_reference": uri, "argument": argument,
                          "selected_or_edited_value": value, "context": context}, ensure_ascii=False, indent=2))
    return McpDocument(server, "resource-completion", uri, (McpBlock("user", text=text, content_type="text"),))
