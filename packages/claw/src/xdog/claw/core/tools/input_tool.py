"""Inert contract for a host tool bound to one admitted input, not a registry."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent import AgentTool
from xdog.claw.core.types import GroupInput

InputReader = Callable[[], Awaitable[dict[str, Any]]]


class InputTool(AgentTool):
    """Build inert handles only; IO starts at execution under the current owner.

    The host supplies live input/channel handles; snapshots supply no authority.
    """

    def bind_input(self, original: GroupInput, channel: object | None,
                   current: Callable[[], bool]) -> InputReader | None:
        raise NotImplementedError

    def read_only(self, arguments: dict[str, Any]) -> bool:
        return False
