"""Runtime permission policy and approval broker for coding-agent tools."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from xdog.agent import BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.tool_effects import ToolEffect

PermissionMode = Literal["ask", "ask-all", "allow-all", "deny"]
PermissionDecision = Literal["allow_once", "allow_session", "deny"]
PermissionRequestHandler = Callable[["PermissionRequest"], None]

_PERMISSION_MODES: tuple[PermissionMode, ...] = ("ask", "ask-all", "allow-all", "deny")
_ALLOW_DECISIONS = ("allow_once", "allow_session")
_READ_ONLY_FS_ACTIONS = frozenset({"read", "ls", "grep", "find"})
_WRITE_FS_ACTIONS = frozenset({"write", "edit", "delete"})
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|authorization|api[_-]?key|credential)", re.I)
_MAX_DISPLAY_STRING = 2_000


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """A tool call waiting for a decision from a user-facing approver."""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    summary: str
    effect: ToolEffect | None = None


@dataclass(slots=True)
class _PendingApproval:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[PermissionDecision]
    rule_key: str


def normalize_permission_mode(raw: object) -> PermissionMode:
    """Return a valid mode, failing closed for malformed configuration."""
    if isinstance(raw, str) and raw in _PERMISSION_MODES:
        return raw
    return "deny"


def classify_tool_call(
    tool_name: str,
    args: Mapping[str, Any],
    workspace: Path,
    *,
    effect: ToolEffect | None = None,
) -> ToolEffect:
    """Describe effects, not authority; *effect* must come from a trusted binding.

    Path resolution performs synchronous filesystem IO. Hosts must run this
    classifier as owned IO, not directly on the agent event loop. The result is
    a policy description, not a filesystem confinement or a TOCTOU guarantee.
    Model-supplied arguments never supply effect metadata.
    """
    if effect is not None:
        return effect
    if tool_name == "current_time":
        return ToolEffect(access="read", scope="tool_data")
    if tool_name == "filesystem":
        action = args.get("action")
        path = args.get("path")
        if (
            isinstance(action, str)
            and action in _READ_ONLY_FS_ACTIONS | _WRITE_FS_ACTIONS
            and isinstance(path, str)
            and path
        ):
            try:
                # Join before resolving: symlink/.. must follow real filesystem
                # semantics rather than a lexical normalization of the path.
                target = (workspace / path).resolve(strict=False)
                root = workspace.resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                pass
            else:
                return ToolEffect(
                    access="read" if action in _READ_ONLY_FS_ACTIONS else "write",
                    scope="workspace" if target.is_relative_to(root) else "outside",
                )
    return ToolEffect(access="execute", scope="unknown")


def tool_call_requires_approval(
    mode: PermissionMode,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    effect: ToolEffect | None = None,
) -> bool:
    """Classify whether a call needs an external decision under *mode*."""
    if mode == "allow-all":
        return False
    if mode == "ask-all":
        return True

    if effect is not None:
        if effect.access == "read":
            return False
        if mode == "ask" and effect.access == "write" and effect.scope == "tool_data":
            return effect.requires_approval
        return True

    # Legacy callers have no owned path classification; preserve the native
    # read-only allowlist without doing synchronous IO in the approval hook.
    # `deny` shares the safe read-only allowlist with `ask`; everything else is
    # blocked without prompting.
    if tool_name == "current_time":
        return False
    if tool_name == "filesystem":
        action = arguments.get("action")
        return not isinstance(action, str) or action not in _READ_ONLY_FS_ACTIONS
    return True


def _redact(value: Any, *, key: str = "") -> Any:
    if key and _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_DISPLAY_STRING:
        omitted = len(value) - _MAX_DISPLAY_STRING
        return f"{value[:_MAX_DISPLAY_STRING]}… <{omitted} chars omitted>"
    return value


def _request_arguments(tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return redacted, bounded arguments suitable for terminals and structured clients."""
    displayed = cast(dict[str, Any], _redact(dict(arguments)))
    if tool_name == "filesystem":
        content = arguments.get("content")
        if isinstance(content, str):
            displayed["content"] = f"<{len(content)} characters>"
    return displayed


def _summary(
    tool_name: str, arguments: Mapping[str, Any], *, effect: ToolEffect | None = None,
) -> str:
    summary = _call_summary(tool_name, arguments)
    if effect is None:
        return summary
    scope_labels = {
        "workspace": "workspace",
        "outside": "outside workspace",
        "tool_data": "tool-owned data",
        "external": "external system",
        "unknown": "unknown scope",
    }
    label = f"Effect: {effect.access} / {scope_labels[effect.scope]} ({effect.scope})"
    if effect.requires_approval:
        label += "; explicit approval required"
    return f"{summary}\n{label}"


def _call_summary(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == "bash":
        command = str(arguments.get("command", "")).strip()
        if len(command) > 500:
            command = command[:500] + "…"
        return f"Run shell command:\n{command}" if command else "Run a shell command"
    if tool_name == "filesystem":
        action = str(arguments.get("action", "unknown"))
        path = str(arguments.get("path", ""))
        if action == "write":
            size = len(str(arguments.get("content", "")))
            return f"Write {size} characters to {path or '(unknown path)'}"
        if action == "edit":
            return f"Edit {path or '(unknown path)'}"
        if action == "delete":
            return f"Delete {path or '(unknown path)'}"
        return f"Filesystem {action}: {path}".rstrip()
    try:
        rendered = json.dumps(_redact(dict(arguments)), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = repr(arguments)
    return f"Run {tool_name}: {rendered}"


def _rule_key(
    tool_name: str, arguments: Mapping[str, Any], *, effect: ToolEffect | None = None,
) -> str:
    """Build a conservative exact-call session rule key."""
    try:
        encoded = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        encoded = repr(sorted((str(k), repr(v)) for k, v in arguments.items()))
    key = f"{tool_name}\0{encoded}"
    if effect is not None:
        # An exact-call grant must not cross a change in the binding's effects.
        key += f"\0{effect.access}\0{effect.scope}\0{effect.requires_approval}"
    return key


class PermissionManager:
    """Enforce tool policy and bridge async tool calls to a UI approver.

    The hook runs in the agent's asyncio loop, which is a background thread in
    interactive mode. Request handlers may run on that thread, but decisions may
    be submitted from any thread; :meth:`resolve` schedules completion on the
    future's owning event loop.
    """

    def __init__(self, mode: object = "ask") -> None:
        self._mode = normalize_permission_mode(mode)
        self._handler: PermissionRequestHandler | None = None
        self._pending: dict[str, _PendingApproval] = {}
        self._session_rules: set[str] = set()
        self._lock = threading.RLock()
        self._closed = False
        self._admission_revision = 0
        self._policy_revision = 0

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @property
    def policy_revision(self) -> int:
        """Live host policy epoch; child approvals cannot cross revoke/regrant cycles."""
        with self._lock:
            return self._policy_revision

    @property
    def has_approver(self) -> bool:
        with self._lock:
            return self._handler is not None and not self._closed

    def set_mode(self, mode: object) -> None:
        selected = normalize_permission_mode(mode)
        with self._lock:
            if selected == self._mode:
                return
            self._mode = selected
            self._policy_revision += 1
            if selected == "deny":
                self._session_rules.clear()  # Restriction is not a temporary mask over old grants.
            pending = tuple(self._pending)
        # Retire the old approval admissions; never transfer a late affirmative
        # reply across a policy change, even if the host later switches back.
        for identifier in pending:
            self.resolve(identifier, "deny")

    def set_request_handler(self, handler: PermissionRequestHandler | None) -> None:
        with self._lock:
            self._handler = handler

    def clear_session_rules(self) -> None:
        with self._lock:
            self._session_rules.clear()

    async def before_tool_call(
        self,
        ctx: BeforeToolCallContext,
        cancel: asyncio.Event | None = None,
        *,
        effect: ToolEffect | None = None,
    ) -> BeforeToolCallResult | None:
        """Agent hook that permits, waits for approval, or blocks a tool call."""
        args = dict(ctx.args) if isinstance(ctx.args, Mapping) else {}
        tool_name = ctx.tool_call.name

        rule_key = _rule_key(tool_name, args, effect=effect)
        with self._lock:
            policy_revision = self._policy_revision
            if not tool_call_requires_approval(self._mode, tool_name, args, effect=effect):
                return None
            if self._mode == "deny":
                return BeforeToolCallResult(block=True, reason="Tool call denied by permission policy")
            if rule_key in self._session_rules:
                return None
            handler = self._handler
            closed = self._closed
        if closed or handler is None:
            return BeforeToolCallResult(
                block=True,
                reason="Tool call requires approval, but no permission approver is available",
            )

        request = PermissionRequest(
            id=uuid.uuid4().hex,
            tool_name=tool_name,
            arguments=_request_arguments(tool_name, args),
            summary=_summary(tool_name, args, effect=effect),
            effect=effect,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionDecision] = loop.create_future()
        pending = _PendingApproval(loop=loop, future=future, rule_key=rule_key)
        with self._lock:
            if self._closed:
                return BeforeToolCallResult(block=True, reason="Permission manager is closed")
            if policy_revision != self._policy_revision:
                return BeforeToolCallResult(block=True, reason="Permission policy changed before approval admission")
            self._admission_revision += 1
            self._pending[request.id] = pending

        cancel_task: asyncio.Task[PermissionDecision] | None = None
        try:
            handler(request)
            if cancel is not None:
                async def _wait_for_cancel() -> PermissionDecision:
                    await cancel.wait()
                    return "deny"

                cancel_task = asyncio.create_task(_wait_for_cancel())
                waiters: set[asyncio.Future[PermissionDecision]] = {future, cancel_task}
                done, _ = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done and future not in done:
                    return BeforeToolCallResult(
                        block=True,
                        reason="Tool approval was cancelled",
                    )
            decision = await future
        except Exception as exc:
            return BeforeToolCallResult(
                block=True,
                reason=f"Could not obtain tool approval: {exc}",
            )
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            with self._lock:
                self._pending.pop(request.id, None)

        with self._lock:
            if self._closed or self.mode == "deny" or policy_revision != self._policy_revision:
                return BeforeToolCallResult(block=True, reason="Permission policy changed; old approval is not valid")
            if decision == "allow_session":
                self._session_rules.add(rule_key)
            if decision in _ALLOW_DECISIONS:
                return None
        return BeforeToolCallResult(block=True, reason="User denied this tool call")

    def resolve(self, request_id: str, decision: PermissionDecision) -> bool:
        """Resolve a pending request from any thread. Return False if unknown."""
        if decision not in ("allow_once", "allow_session", "deny"):
            return False
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False

        def _set_result() -> None:
            if not pending.future.done():
                pending.future.set_result(decision)

        try:
            pending.loop.call_soon_threadsafe(_set_result)
        except RuntimeError:
            # The worker can close its loop between lookup and scheduling.
            # A late decision must neither crash the UI nor grant a session rule.
            with self._lock:
                if self._pending.get(request_id) is pending:
                    self._pending.pop(request_id, None)
            return False
        return True

    @property
    def admission_revision(self) -> int:
        """Monotonic approval admission marker; closing an approval never erases it."""
        with self._lock:
            return self._admission_revision

    @property
    def has_pending_requests(self) -> bool:
        """Authoritative admission check, including approvals not yet painted by a UI."""
        with self._lock:
            return any(not item.future.done() for item in self._pending.values())

    def has_pending(self, request_id: str) -> bool:
        with self._lock:
            item = self._pending.get(request_id)
            return item is not None and not item.future.done()

    def deny_all(self) -> None:
        """Deny every pending request without closing the manager."""
        with self._lock:
            request_ids = list(self._pending)
        for request_id in request_ids:
            self.resolve(request_id, "deny")

    def close(self) -> None:
        """Fail all pending requests and reject future interactive approvals."""
        with self._lock:
            self._closed = True
            self._handler = None
        self.deny_all()
