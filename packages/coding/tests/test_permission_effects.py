"""Effect classification and permission policy, without host/UI integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.agent.tool_effects import ToolEffect
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.permissions import (
    PermissionManager,
    PermissionMode,
    PermissionRequest,
    classify_tool_call,
    tool_call_requires_approval,
)

_ACTIONS = ("read", "ls", "grep", "find", "write", "edit", "delete")


def _ctx(name: str, args: dict[str, Any]) -> BeforeToolCallContext:
    return BeforeToolCallContext(
        assistant_message=AssistantMessage(),
        tool_call=ToolCall(id="effect-call", name=name, arguments=args),
        args=args,
        context=AgentContext(),
    )


def _needs_approval(mode: PermissionMode, effect: ToolEffect) -> bool:
    if mode == "allow-all":
        return False
    if mode == "ask-all":
        return True
    if effect.access == "read":
        return False
    return not (
        mode == "ask" and effect.access == "write" and effect.scope == "tool_data" and not effect.requires_approval
    )


@pytest.mark.parametrize('action', ['read', 'write'])
@pytest.mark.parametrize('path', ['资料/你好.txt', '子目录/../你好.txt'])
def test_native_paths_remain_workspace_not_tool_data(tmp_path: Path, action: str, path: str) -> None:
    effect = classify_tool_call("filesystem", {"action": action, "path": path}, tmp_path)
    assert effect == ToolEffect(access="read" if action in _ACTIONS[:4] else "write", scope="workspace")


@pytest.mark.parametrize('action', ['read', 'write'])
@pytest.mark.parametrize("relative", [False, True])
def test_outside_paths_and_sibling_prefix(tmp_path: Path, action: str, relative: bool) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = "../workspace-other/你好.txt" if relative else str(tmp_path / "workspace-other/你好.txt")
    effect = classify_tool_call("filesystem", {"action": action, "path": path}, workspace)
    assert effect == ToolEffect(access="read" if action in _ACTIONS[:4] else "write", scope="outside")


@pytest.mark.parametrize('action', ['read', 'write'])
@pytest.mark.parametrize('path', ['escape/new.txt', 'dangling'])
def test_symlinks_resolve_outside_including_parent_and_missing_targets(
    tmp_path: Path, action: str, path: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (workspace / "dangling").symlink_to(outside / "not-created")
    (outside / "existing").write_text("external", encoding="utf-8")
    (workspace / "file-link").symlink_to(outside / "existing")
    effect = classify_tool_call("filesystem", {"action": action, "path": path}, workspace)
    assert effect.scope == "outside"
    assert effect.access == ("read" if action in _ACTIONS[:4] else "write")


def test_workspace_symlink_and_inbound_symlink_use_resolved_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "real"
    workspace.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    inbound = tmp_path / "inbound"
    inbound.symlink_to(workspace / "new")
    for path in ("new", str(workspace / "new"), str(inbound)):
        assert classify_tool_call("filesystem", {"action": "read", "path": path}, alias) == ToolEffect(
            access="read", scope="workspace",
        )


@pytest.mark.parametrize('action', ['read', 'write'])
@pytest.mark.parametrize('path', [None, 'bad\0path'])
def test_invalid_or_missing_path_is_unknown_not_read_exempt(tmp_path: Path, action: str, path: Any) -> None:
    args = {"action": action} if path is None else {"action": action, "path": path}
    effect = classify_tool_call("filesystem", args, tmp_path)
    assert effect == ToolEffect(access="execute", scope="unknown")
    assert tool_call_requires_approval("ask", effect)
    assert tool_call_requires_approval("deny", effect)


@pytest.mark.parametrize('error', [OSError('unavailable')])
@pytest.mark.parametrize("failing_call", [1, 2])
def test_target_or_root_resolution_failure_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, failing_call: int,
) -> None:
    original = Path.resolve
    calls = 0

    def resolve(path: Path, strict: bool = False) -> Path:
        nonlocal calls
        calls += 1
        if calls == failing_call:
            raise error
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert classify_tool_call("filesystem", {"action": "read", "path": "file"}, tmp_path) == ToolEffect(
        access="execute", scope="unknown",
    )


@pytest.mark.parametrize('action', [None, 'READ'])
def test_undeclared_filesystem_actions_are_unknown(tmp_path: Path, action: Any) -> None:
    assert classify_tool_call("filesystem", {"action": action, "path": "file"}, tmp_path) == ToolEffect(
        access="execute", scope="unknown",
    )


@pytest.mark.parametrize('command', ['cat README.md'])
def test_shell_is_never_inferred_read_only(tmp_path: Path, command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, tmp_path) == ToolEffect(
        access="execute", scope="unknown",
    )


@pytest.mark.parametrize('name', ['goal', 'mcp.read'])
@pytest.mark.parametrize('action', ['report'])
def test_undeclared_tool_names_or_argument_effects_grant_no_exemption(tmp_path: Path, name: str, action: str) -> None:
    args = {
        "action": action,
        "path": ".xdog/goals/tasks/a.json",
        "effect": {"access": "read", "scope": "tool_data"},
        "requires_approval": False,
    }
    effect = classify_tool_call(name, args, tmp_path)
    assert effect == ToolEffect(access="execute", scope="unknown")
    assert tool_call_requires_approval("ask", None)
    assert tool_call_requires_approval("deny", effect)


def test_trusted_effect_and_clock_do_not_resolve_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Path:
        pytest.fail("No path IO is needed for trusted metadata or clock facts")

    monkeypatch.setattr(Path, "resolve", fail)
    effect = ToolEffect(access="write", scope="tool_data")
    assert classify_tool_call("filesystem", {"action": "delete"}, tmp_path, effect=effect) is effect
    assert classify_tool_call("current_time", {}, tmp_path) == ToolEffect(access="read", scope="tool_data")


@pytest.mark.parametrize("mode,effect", [
    ("ask", ToolEffect(access="read", scope="outside")),
    ("ask", ToolEffect(access="write", scope="workspace")),
    ("ask", ToolEffect(access="write", scope="tool_data")),
    ("ask", ToolEffect(access="write", scope="tool_data", requires_approval=True)),
    ("ask", ToolEffect(access="write", scope="external")),
    ("ask", ToolEffect(access="execute", scope="unknown")),
    ("ask-all", ToolEffect(access="read", scope="workspace")),
    ("allow-all", ToolEffect(access="execute", scope="unknown")),
    ("deny", ToolEffect(access="read", scope="workspace")),
    ("deny", ToolEffect(access="write", scope="workspace")),
])
async def test_manager_effect_mode_matrix_without_host_integration(mode: PermissionMode, effect: ToolEffect) -> None:
    manager = PermissionManager(mode)
    requests: list[PermissionRequest] = []

    def deny(request: PermissionRequest) -> None:
        requests.append(request)
        manager.resolve(request.id, "deny")

    manager.set_request_handler(deny)
    try:
        result = await manager.before_tool_call(_ctx("extension", {}), effect=effect)
        requires = _needs_approval(mode, effect)
        assert (result is not None and result.block) == requires
        assert len(requests) == int(requires and mode != "deny")
        if requests:
            assert requests[0].effect is effect
            assert effect.access in requests[0].summary
            assert effect.scope in requests[0].summary
            assert "\x1b" not in requests[0].summary
    finally:
        manager.close()


@pytest.mark.parametrize('action', ['report'])
async def test_goal_owned_writes_approved_by_binding_not_tool_name(action: str) -> None:
    manager = PermissionManager("ask")
    effect = ToolEffect(access="write", scope="tool_data")
    try:
        assert await manager.before_tool_call(_ctx("goal", {"action": action}), effect=effect) is None
        unknown = await manager.before_tool_call(_ctx("goal", {"action": action}))
        assert unknown is not None and unknown.block
        guarded = await manager.before_tool_call(
            _ctx("goal", {"action": action}),
            effect=ToolEffect(access="write", scope="tool_data", requires_approval=True),
        )
        assert guarded is not None and guarded.block
    finally:
        manager.close()


@pytest.mark.parametrize('action', ['read'])
def test_missing_effect_or_path_never_borrows_a_read_exemption(tmp_path: Path, action: str) -> None:
    effect = classify_tool_call("filesystem", {"action": action}, tmp_path)
    for mode in ("ask", "deny"):
        assert tool_call_requires_approval(mode, None)
        assert tool_call_requires_approval(mode, effect)


def test_permission_request_has_no_implied_effect() -> None:
    request = PermissionRequest("id", "tool", {}, "summary")
    assert request.effect is None
    assert request.summary == "summary"
