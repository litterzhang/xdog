"""Read-only extension command artifacts, not model input or host instructions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.agent.extensions.tool_result import normalize_result


def normalize_command_result(value: Any) -> dict[str, Any]:
    result = normalize_result(value)
    if result.get("isError") is True:
        raise ValueError("Tool execution error flags are not command artifacts; "
                         "command failure must cancel host actions")
    return result


@dataclass(frozen=True, slots=True)
class ExtensionCommandResult:
    extension: str
    command: str
    result: dict[str, Any]

    @property
    def summary(self) -> str:
        images = sum(part["type"] == "image" for part in self.result["content"])
        return f"Extension command /{self.command}: rich output ({images} images); read-only artifact, not agent input"

    def to_dict(self) -> dict[str, Any]:
        return {"extension": self.extension, "command": self.command, "result": normalize_command_result(self.result)}

    @classmethod
    def from_dict(cls, value: Any) -> ExtensionCommandResult:
        if (
            not isinstance(value, dict)
            or set(value) != {"extension", "command", "result"}
            or not isinstance(value["extension"], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value["extension"])
            or not isinstance(value["command"], str)
            or len(value["command"]) > 80
            or not re.fullmatch(r"ext-[A-Za-z][A-Za-z0-9_]{0,31}-[A-Za-z][A-Za-z0-9_]{0,31}", value["command"])
            or not value["command"].lower().startswith("ext-" + value["extension"].lower() + "-")
            or not isinstance(value["result"], dict)
        ):
            raise ValueError("Invalid extension command artifact")
        return cls(value["extension"], value["command"], normalize_command_result(value["result"]))

    def text(self) -> str:
        blocks = [part["text"] for part in self.result["content"] if part["type"] == "text"]
        if "structuredContent" in self.result:
            blocks.append(
                "[Extension command structured output]\n"
                + json.dumps(
                    self.result["structuredContent"], ensure_ascii=False, allow_nan=False, separators=(",", ":")
                )
            )
        if "details" in self.result:
            blocks.append(
                "[Extension command details]\n"
                + json.dumps(self.result["details"], ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            )
        images = [part["mimeType"] for part in self.result["content"] if part["type"] == "image"]
        if images:
            blocks.append("[Images: " + ", ".join(images) + "; original data retained in this command artifact]")
        return "\n\n".join(blocks)


ARTIFACT_SCHEMA = "xdog.extension-command/v1"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


def artifact_bytes(result: ExtensionCommandResult) -> bytes:
    payload = result.to_dict()
    canonical = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    envelope = {"schema": ARTIFACT_SCHEMA, "artifact": payload, "sha256": hashlib.sha256(canonical).hexdigest()}
    encoded = (json.dumps(envelope, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ValueError("Command artifact exceeds its retention limit")
    return encoded


def artifact_from_bytes(data: bytes) -> ExtensionCommandResult:
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("Command artifact exceeds its retention limit")
    try:
        envelope = json.loads(data)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema", "artifact", "sha256"}
            or envelope["schema"] != ARTIFACT_SCHEMA
            or not isinstance(envelope["sha256"], str)
        ):
            raise ValueError("Invalid command artifact file")
        result = ExtensionCommandResult.from_dict(envelope["artifact"])
        canonical = json.dumps(
            result.to_dict(), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if hashlib.sha256(canonical).hexdigest() != envelope["sha256"]:
            raise ValueError("Command artifact checksum mismatch")
        return result
    except (UnicodeError, RecursionError):
        raise ValueError("Invalid command artifact encoding") from None


def read_artifact(path: Path) -> ExtensionCommandResult:
    fd = os.open(path.expanduser(), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Command artifact source must be a regular file")
        data = source.read(MAX_ARTIFACT_BYTES + 1)
    return artifact_from_bytes(data)
