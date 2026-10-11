"""Command-only editor request/answer codec. Not agent/tool permission authority."""

from __future__ import annotations

import re
from typing import Any


def text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Editor text must be a string")
    try:
        if len(value.encode()) > 65536:
            raise ValueError("Editor text exceeds 64 KiB")
    except UnicodeError:
        raise ValueError("Invalid editor text encoding") from None
    return value


def validate_editor_request(value: dict[str, Any]) -> None:
    if value.get('operation') == 'propose':
        from xdog.agent.extensions.editor_request import validate_request
        validate_request(value)
        return
    if value.get("operation") not in ("read", "replace"):
        raise ValueError("Unknown editor host operation")
    if value["operation"] == "replace":
        text(value.get("text"))
        if not isinstance(value.get("revision"), str) or not re.fullmatch("[a-f0-9]{64}", value["revision"]):
            raise ValueError("Replace requires a captured editor revision")


def validate_editor_answer(request: dict[str, Any], value: Any) -> Any:
    if request.get('operation') == 'propose':
        from xdog.agent.extensions.editor_request import validate_answer
        return validate_answer(value)
    if value is None:
        return None
    if request["operation"] == "read":
        if (
            not isinstance(value, dict)
            or set(value) != {"text", "cursor", "image_count", "revision"}
            or type(value["cursor"]) is not int
            or type(value["image_count"]) is not int
            or not 0 <= value["image_count"] <= 64
            or not isinstance(value["revision"], str)
            or not re.fullmatch("[a-f0-9]{64}", value["revision"])
        ):
            raise ValueError("Invalid editor snapshot")
        captured = text(value["text"])
        if not 0 <= value["cursor"] <= len(captured):
            raise ValueError("Invalid captured cursor")
        return dict(value)
    if (
        not isinstance(value, dict)
        or set(value) != {"applied", "reason"}
        or type(value["applied"]) is not bool
        or not isinstance(value["reason"], str)
        or len(value["reason"].encode()) > 1000
    ):
        raise ValueError("Invalid editor replacement receipt")
    return dict(value)
