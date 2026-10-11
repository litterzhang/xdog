"""Explicit submitted-input decisions; no implicit tool/command/model dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def input_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Extension input text must be a string")
    try:
        if len(value.encode()) > 1024 * 1024:
            raise ValueError("Extension input text exceeds 1 MiB")
    except UnicodeError:
        raise ValueError("Invalid extension input encoding") from None
    return value


@dataclass(frozen=True, slots=True)
class InputDecision:
    action: str
    text: str = ""
    reason: str = ""
    feedback: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            **({"text": self.text} if self.action == "transform" else {}),
            **({"reason": self.reason} if self.action == "reject" else {}),
            **({"feedback": self.feedback} if self.action == "handled" else {}),
        }


class InputRejected(RuntimeError):
    """A pre-admission input was refused; original text/images still belong to its caller."""


def decision(value: Any) -> InputDecision:
    if value is None:
        return InputDecision("continue")
    if isinstance(value, str):
        return InputDecision("transform", input_text(value))
    if not isinstance(value, dict) or value.get("action") not in ("continue", "transform", "reject", "handled"):
        raise ValueError("Invalid extension input decision")
    keys = {"continue": {"action"}, "transform": {"action", "text"}, "reject": {"action", "reason"},
            "handled": {"action", "feedback"}}[value["action"]]
    if set(value) != keys:
        raise ValueError("Input decisions cannot silently change images, execute commands or add host actions")
    if value["action"] == "handled":
        feedback = input_text(value["feedback"])
        if not feedback.strip() or len(feedback.encode()) > 65536:
            raise ValueError("Handled input requires explicit feedback up to 64 KiB")
        return InputDecision("handled", feedback=feedback)
    if value["action"] == "transform":
        return InputDecision("transform", input_text(value["text"]))
    if value["action"] == "reject":
        reason = input_text(value["reason"])
        if not reason.strip() or len(reason.encode()) > 2000:
            raise ValueError("Invalid extension input rejection reason")
        return InputDecision("reject", reason=reason)
    return InputDecision("continue")
