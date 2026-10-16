"""Tests for coding-agent runtime tool permissions."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.permissions import (
    PermissionManager,
    PermissionRequest,
    normalize_permission_mode,
    tool_call_requires_approval,
)


def _ctx(name: str, arguments: dict[str, Any]) -> BeforeToolCallContext:
    return BeforeToolCallContext(
        assistant_message=AssistantMessage(),
        tool_call=ToolCall(id="call-1", name=name, arguments=arguments),
        args=arguments,
        context=AgentContext()
    )


def test_default_policy_classification() -> None:
    assert not tool_call_requires_approval("ask", "current_time", {})
    assert not tool_call_requires_approval("ask", "filesystem", {"action": "read"})
    assert not tool_call_requires_approval("ask", "filesystem", {"action": "grep"})
    assert tool_call_requires_approval("ask", "filesystem", {"action": "write"})
    assert tool_call_requires_approval("ask", "filesystem", {})
    assert tool_call_requires_approval("ask", "bash", {"command": "pwd"})
    assert tool_call_requires_approval("ask", "extension_tool", {})
    assert tool_call_requires_approval("ask-all", "current_time", {})
    assert not tool_call_requires_approval("allow-all", "bash", {})


def test_invalid_mode_fails_closed() -> None:
    assert normalize_permission_mode("invalid") == "deny"
    assert normalize_permission_mode(None) == "deny"


@pytest.mark.asyncio
async def test_read_only_call_is_automatically_allowed() -> None:
    manager = PermissionManager("ask")
    result = await manager.before_tool_call(_ctx("filesystem", {"action": "read"}))
    assert result is None


@pytest.mark.asyncio
async def test_call_without_approver_is_blocked() -> None:
    manager = PermissionManager("ask")
    result = await manager.before_tool_call(_ctx("bash", {"command": "pwd"}))
    assert result is not None
    assert result.block
    assert "no permission approver" in result.reason


@pytest.mark.asyncio
async def test_allow_once_and_redaction() -> None:
    manager = PermissionManager("ask")
    requests: list[PermissionRequest] = []

    def approve(request: PermissionRequest) -> None:
        requests.append(request)
        assert manager.resolve(request.id, "allow_once")

    manager.set_request_handler(approve)
    result = await manager.before_tool_call(_ctx("extension", {
        "token": "secret-value",
        "value": "shown",
    }))

    assert result is None
    assert requests[0].arguments == {"token": "<redacted>", "value": "shown"}


@pytest.mark.asyncio
async def test_allow_session_matches_only_exact_call() -> None:
    manager = PermissionManager("ask")
    requests: list[PermissionRequest] = []

    def approve(request: PermissionRequest) -> None:
        requests.append(request)
        manager.resolve(request.id, "allow_session")

    manager.set_request_handler(approve)
    same = _ctx("bash", {"command": "pytest"})
    different = _ctx("bash", {"command": "rm -rf build"})

    assert await manager.before_tool_call(same) is None
    assert await manager.before_tool_call(same) is None
    assert await manager.before_tool_call(different) is None
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_deny_and_cancel() -> None:
    manager = PermissionManager("ask")
    cancel = asyncio.Event()

    def deny(request: PermissionRequest) -> None:
        manager.resolve(request.id, "deny")

    manager.set_request_handler(deny)
    denied = await manager.before_tool_call(_ctx("bash", {"command": "pwd"}))
    assert denied is not None and denied.block
    assert "User denied" in denied.reason

    def cancel_request(_request: PermissionRequest) -> None:
        cancel.set()

    manager.set_request_handler(cancel_request)
    cancelled = await manager.before_tool_call(
        _ctx("bash", {"command": "whoami"}),
        cancel,
    )
    assert cancelled is not None and cancelled.block
    assert "cancelled" in cancelled.reason


def test_late_decision_for_closed_loop_fails_closed_without_crashing_ui() -> None:
    from xdog.coding.core.permissions import _PendingApproval
    manager = PermissionManager("ask")
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    manager._pending["stale"] = _PendingApproval(loop=loop, future=future, rule_key="stale-rule")
    loop.close()
    assert manager.resolve("stale", "allow_session") is False
    manager.close()
    assert not manager._pending
    assert "stale-rule" not in manager._session_rules


@pytest.mark.parametrize('name,args', [
    ('filesystem', {'action': 'write', 'path': 'not-executed', 'content': 'not executed'}),
    ('bash', {'command': 'not-executed'}),
    ('extension_tool', {}),
    ('mcp_tool', {}),
])
async def test_deny_overrides_and_revokes_prior_exact_session_approval(name, args):
    manager = PermissionManager('ask')
    requests = []

    def approve(request):
        requests.append(request)
        manager.resolve(request.id, 'allow_session')

    manager.set_request_handler(approve)
    try:
        assert await manager.before_tool_call(_ctx(name, args)) is None
        manager.set_mode('deny')
        result = await manager.before_tool_call(_ctx(name, args))
        assert result is not None and result.block
        assert len(requests) == 1 and not manager._session_rules
        assert await manager.before_tool_call(_ctx('filesystem', {'action': 'read'})) is None
    finally:
        manager.close()


@pytest.mark.parametrize('decision', ['allow_once', 'allow_session'])
@pytest.mark.parametrize('return_to_ask', [False, True])
async def test_policy_change_during_pending_approval_cannot_admit_a_stale_allow(decision, return_to_ask):
    manager = PermissionManager('ask')
    requests = []
    manager.set_request_handler(requests.append)
    work = asyncio.create_task(manager.before_tool_call(_ctx('bash', {'command': 'not-executed'})))
    try:
        async with asyncio.timeout(2):
            while not requests:
                await asyncio.sleep(0)
        # Queue an affirmative reply, then change policy before its callback
        # runs. Even switching back cannot revive this old approval admission.
        assert manager.resolve(requests[0].id, decision)
        manager.set_mode('deny')
        if return_to_ask:
            manager.set_mode('ask')
        result = await asyncio.wait_for(work, 2)
        assert result is not None and result.block
        assert not manager._session_rules and not manager.has_pending_requests
    finally:
        manager.close()
        await asyncio.gather(work, return_exceptions=True)
