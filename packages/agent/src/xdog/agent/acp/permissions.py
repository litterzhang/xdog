"""Fail-closed permission hook for the independent ACP agent CLI."""

from __future__ import annotations

import asyncio

from xdog.agent.types import BeforeToolCallContext, BeforeToolCallResult


async def deny_tools(context: BeforeToolCallContext, cancel: asyncio.Event | None = None) -> BeforeToolCallResult:
    return BeforeToolCallResult(block=True, reason="ACP tool execution requires an explicit harness permission policy")
