"""Coding-owned child factory: independent approvals/extensions, dynamic parent gates."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from xdog.agent import Agent, AgentConfig, AgentMessage, BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.access import AccessScope, read_only_call
from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.children import ChildPort
from xdog.agent.child_compaction import ArchivedSummaryStream, preserve_model_metadata
from xdog.agent.child_config import ChildPresets
from xdog.agent.child_model import resolve_child_model
from xdog.agent.child_policy import ChildDelegation, ChildSkills, ParentHandle, _matches
from xdog.agent.children import RESERVED_TOOLS, ChildSpec, child_prompt
from xdog.agent.core import AgentTool, AgentToolResult, StreamFn, ToolExecutionMode
from xdog.agent.cross_loop import settle_on
from xdog.agent.skills.manager import SkillManager
from xdog.agent.skills.tool import _Reader
from xdog.agent.tool_effects import ToolEffect
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, HandledInput
from xdog.ai.types import AssistantMessage, Context, StreamOptions, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.child_extensions import ChildExtensions
from xdog.coding.core.child_runtime import CodingChildRuntime
from xdog.coding.core.child_store import ChildStore
from xdog.coding.core.permissions import PermissionDecision, PermissionManager, PermissionRequest

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


@dataclass(frozen=True)
class _ParentMetadata:
    model: str
    stream: StreamFn
    options: StreamOptions
    tool_execution: ToolExecutionMode
    context_window: int
    max_prompt_tokens: int
    supports_tool_calls: bool | None
    extension_owners: tuple[tuple[str, str], ...] = ()


def _argument_fingerprint(params: dict[str, Any]) -> str:
    """Fingerprint finite JSON without conflating non-JSON Python shapes/keys."""
    def finite_json(value: Any) -> None:
        if type(value) in {str, bool, int, float, type(None)}:
            return
        if type(value) is list:
            for item in value:
                finite_json(item)
            return
        if type(value) is dict and all(type(key) is str for key in value):
            for item in value.values():
                finite_json(item)
            return
        raise ValueError("Child tool arguments must be finite JSON")
    try:
        finite_json(params)
        value = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Child tool arguments must be finite JSON") from exc


@dataclass(frozen=True)
class _EffectAdmission:
    context: BeforeToolCallContext
    executor: AgentTool
    call: ToolCall
    assistant: AssistantMessage
    params: dict[str, Any]
    original_args: dict[str, Any]
    call_id: str
    tool_name: str
    params_fingerprint: str
    original_fingerprint: str
    permission_revision: int
    tool_selection_revision: int
    access: AccessScope
    access_revision: int
    effect: ToolEffect | None = None
    claim: object | None = None
    started: bool = False


class CodingChildren:
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.token = uuid.uuid4().hex
        branch = session.session_data.active_branch or "head"
        self.store = ChildStore.scoped(session.session_manager._dir, session.session_id, branch)
        self.capabilities = ChildPort(session.session_id, branch,
            lambda: (*session.capabilities.owned_tools, *session.capabilities.tools),
            parent_valid=lambda identifier: identifier in self.parents and self.parents[identifier].live)
        self._closed = False
        self.snapshots: dict[str, _ParentMetadata] = {}
        self.parents: dict[str, ParentHandle] = {}
        self.skill_limits: dict[str, ChildSkills] = {}
        self.delegations: dict[str, ChildDelegation] = {}
        self.scoped_tools: dict[str, dict[str, AgentTool]] = {}
        self.tool_limits: dict[str, tuple[str, ...] | None] = {}
        self.extensions = ChildExtensions(session.extensions, self.store)
        self.permissions: dict[str, PermissionManager] = {}
        self.approver: Callable[[str, PermissionRequest], None] | None = None
        self.approval_alive: Callable[[], bool] | None = None
        self.preset_error = ""
        try:
            presets = ChildPresets(session.settings.child_presets)
        except (ValueError, TypeError) as exc:
            presets = ChildPresets()
            self.preset_error = "Invalid child preset catalog: " + str(exc)
        diagnostic = ""
        try:
            records = self.store.load()
        except (ValueError, TypeError, OSError, UnicodeError) as exc:
            records = []
            diagnostic = "Child catalog unavailable; repair saved records before delegation: " + str(exc)[:500]
        self.runtime = CodingChildRuntime(self.create, self.store.save, capture=self.capture, begin=self.store.begin,
            persist_inputs=self.store.save_inputs, persist_return=self.store.save_return,
            persist_admission=self.store.save_admission, archive_compaction=self.store.archive_compaction,
                                          forget=self.store.forget, finalize=self.finish,
            on_event=self.extensions.event, records=records, presets=presets)
        self.capabilities.runtime = self.runtime
        self.runtime.diagnostic = diagnostic
        self.tools = self.runtime.tools()
        self._control_execute = self.tools[0].execute
        self._control_validate = self.tools[0].validate_arguments

    def approval_free_control(self, ctx: BeforeToolCallContext) -> bool:
        """Queries and messages to existing children need no default approval.

        This is for the live host binding, never a tool-name or permission grant.
        Child effects retain their own approval gates. Queue mutation/recovery
        is not exempt. Extensions may rewrite arguments, so callers must classify
        the final validated call rather than the original model input.
        """
        args, session = ctx.args, self.session
        if type(args) is not dict or ctx.tool_call.name != "agent":
            return False
        action = args.get("action")
        if not isinstance(action, str):
            return False
        if action in {"list", "presets"}:
            valid = set(args) <= {"action", "text"} and isinstance(args.get("text", ""), str)
        elif action in {"inspect", "wait"}:
            valid = (not set(args) - {"action", "id", "text"}
                     and isinstance(args.get("id"), str) and bool(args["id"])
                     and isinstance(args.get("text", ""), str))
        elif action in {"steer", "follow-up"}:
            valid = (set(args) == {"action", "id", "text"}
                     and isinstance(args["id"], str) and bool(args["id"])
                     and isinstance(args["text"], str))
        else:
            return False
        tool = self.tools[0]
        active = [item for item in session.agent.active_tools if item.name == "agent"]
        supplied = [item for item in ctx.context.tools or () if item.name == "agent"]
        return (valid and not self._closed and not self.runtime.closed
                and not session._sdk_disposing and session.children is self
                and tool.name == "agent" and tool.execute is self._control_execute
                and tool.validate_arguments is self._control_validate
                and len(active) == len(supplied) == 1
                and active[0] is tool and supplied[0] is tool)

    def delegable_skills(self) -> list[dict[str, str]]:
        """Read current parent catalog metadata on the caller's owned IO worker.

        No activation, model, permission grant or parent manager escapes this
        boundary. Retirement during the filesystem read is unavailable, not [].
        """
        session = self.session
        original, permission = session.agent, session.permissions
        identifier, branch = session.session_id, session.session_data.active_branch or "head"
        manager = original._skills
        def live() -> bool:
            return (not self._closed and not self.runtime.closed and session.children is self
                    and session.agent is original and session.permissions is permission
                    and session.session_id == identifier
                    and (session.session_data.active_branch or "head") == branch
                    and original._skills is manager and not session.extensions._closed)
        if not live():
            raise ValueError("Original Coding parent skill owner is unavailable")
        if type(manager) is SkillManager and "list_skills" not in vars(manager):
            listed = _Reader(manager, None, 262144, 512).listing(threading.Event())
            if listed["unavailable"] or listed["truncated"]:
                raise ValueError("Parent skill catalog is unsafe/unavailable or exceeds selection capacity")
            rows = [{"slug": row["slug"], "description": row["description"]} for row in listed["skills"]]
        else:
            rows = [{"slug": row.slug, "description": row.description}
                    for row in manager.list_skills()] if manager is not None else []
        if not live():
            raise ValueError("Original Coding parent skill owner changed during catalog read")
        return rows

    def capture(self, spec: ChildSpec) -> Awaitable[ChildSpec]:
        session = self.session
        spec = replace(spec, tool_scopes=spec.config().to_dict()["tool_scopes"])
        if session._context_changes:
            raise RuntimeError("Session context/policy is changing; refresh before starting child work")
        original = session.agent
        permission = session.permissions
        identifier = session.session_id
        branch = session.session_data.active_branch or "head"
        def delegable_tools() -> tuple[AgentTool, ...]:
            live_extensions = session.extensions.tools()
            return tuple(tool for tool in original.active_tools
                         if tool.name not in RESERVED_TOOLS and tool is not session._session_info_tool
                         and (self.capabilities.inherited(tool) or (spec.tools is not None
                              and tool.name in spec.tool_scopes
                              and session.capabilities.can_delegate_child_tool(tool)))
                         and (tool.name not in session.extensions.aliases
                              or any(tool is value for value in live_extensions)))
        parent = ParentHandle(
            alive=lambda: (not self._closed and not self.runtime.closed
                           and session.agent is original and session.children is self
                           and session.session_id == identifier
                           and (session.session_data.active_branch or "head") == branch
                           and session.permissions is permission and not session.extensions._closed),
            tools=delegable_tools,
            skills=lambda: original._skills)
        try:
            grant = parent.capture_tools(spec.tools, spec.tool_actions)
        except ValueError as exc:
            raise ValueError("Child tool profile exceeds captured parent delegable tools: " + str(exc)) from exc
        metadata = _ParentMetadata(
            model=original.state.model, stream=original.stream_fn, options=original.options,
            tool_execution=original.tool_execution, context_window=original.context_window,
            max_prompt_tokens=original.max_prompt_tokens, supports_tool_calls=original.supports_tool_calls,
            extension_owners=tuple((tool.name, session.extensions.tool_owner(tool.name)) for tool in grant.tools
                                   if tool.name in session.extensions.aliases))
        async def admit() -> ChildSpec:
            skills = await run_owned(parent.capture_skills, spec.skills)
            current = parent.capture_tools(None).tools
            if any(not any(tool is value for value in current) for tool in grant.tools):
                raise ValueError("Parent tool delegation changed during child admission")
            if session._context_changes:
                raise RuntimeError("Session context/policy changed during child admission")
            allowed = self.capabilities.capture(spec, grant.tools, grant.names)
            selected = ChildDelegation.capture(grant.tools, allowed, spec.tool_actions)
            ceilings = self.runtime.delegation_schemas(spec.id)
            if ceilings:
                if set(selected.names) != set(ceilings):
                    raise ValueError("Saved child delegation is unavailable; no implicit grant on continuation")
                selected = replace(selected, schemas=tuple(copy.deepcopy(ceilings[name]) for name in selected.names))
            scoped = {}
            for name, scope in spec.tool_scopes.items():
                actual = next((tool for tool in selected.tools if tool.name == name), None)
                if actual is None:
                    raise ValueError("Scoped tool is outside the original child delegation")
                scoped[name] = await session.capabilities.delegate_child_tool(actual, spec, scope)
                selected.scoped_parameters(name, scoped[name].parameters)
            selected = replace(selected, schemas=tuple(
                selected.scoped_parameters(tool.name, scoped[tool.name].parameters)
                if tool.name in scoped else selected.schemas[index]
                for index, tool in enumerate(selected.tools)))
            current = parent.capture_tools(None).tools
            if any(not any(tool is value for value in current) for tool in selected.tools):
                raise ValueError("Parent tool delegation changed during scope validation")
            self.parents[spec.id] = parent
            self.skill_limits[spec.id] = skills
            self.scoped_tools[spec.id] = scoped
            self.delegations[spec.id] = selected
            self.tool_limits[spec.id] = selected.names  # Never None, even without a parent selection.
            self.snapshots[spec.id] = metadata
            self.extensions.reserve(spec.id)
            return replace(spec, tools=selected.names, skills=skills.slugs)
        return admit()

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None]) -> Agent:
        session = self.session
        state = self.snapshots[spec.id]
        parent, grant = self.parents[spec.id], self.delegations[spec.id]
        if not parent.live:
            raise ValueError("Original parent owner retired before child construction")
        skills = self.skill_limits[spec.id]
        scoped = self.scoped_tools[spec.id]
        await run_owned(parent.check_skills, skills)
        role_tools = await self.capabilities.tools(spec)
        allowed_tools = (*grant.names, *(tool.name for tool in role_tools))
        extension_spec = replace(spec, tools=allowed_tools, skills=skills.slugs)
        selected = await run_owned(resolve_child_model, state.stream, state.model, state.options,
            spec.config(), state.context_window, state.supports_tool_calls,
            parent_max_prompt_tokens=state.max_prompt_tokens)
        extension = await self.extensions.create(extension_spec, publish, model=selected.id,
                                                 thinking=selected.options.thinking or "off", initialize=False)
        manager = PermissionManager(session.permissions.mode)
        self.permissions[spec.id] = manager
        def request(value: PermissionRequest) -> None:
            if self.approver is None:
                manager.resolve(value.id, "deny")
            else:
                self.approver(spec.id, value)
        manager.set_request_handler(request)
        original_permissions = session.permissions
        original_agent = session.agent
        last_authority: tuple[int, int, AccessScope, int] | None = None
        def check(ctx: BeforeToolCallContext | AfterToolCallContext) -> None:
            bound = self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ())
            parent.check_tool(grant, BeforeToolCallContext(ctx.assistant_message, ctx.tool_call, ctx.args, ctx.context),
                              capability_bound=bound)

        def effect(name: str, params: dict[str, Any]) -> ToolEffect | None:
            if name not in scoped:
                return None
            actual = next(tool for tool in grant.tools if tool.name == name)
            if not session.capabilities.can_delegate_child_tool(actual):
                raise ValueError("Original scoped capability tool is no longer delegable")
            if not _matches(scoped[name].parameters, params):
                return ToolEffect("execute", "unknown")
            return session.capabilities.tool_effect(actual, params) or ToolEffect("execute", "unknown")

        def safe_call(name: str, params: dict[str, Any]) -> bool:
            classified = effect(name, params)
            return classified.access == "read" if classified is not None else read_only_call(name, params)

        # Provider IDs are correlation labels, never permission identities. Keep
        # each exact proxy + prepared argument object alive through finalization.
        # Distinct calls with the same string ID cannot overwrite/consume a lease.
        admissions: dict[tuple[int, int], _EffectAdmission] = {}

        def selected_executor(ctx: BeforeToolCallContext | AfterToolCallContext) -> AgentTool:
            matches = [tool for tool in ctx.context.tools or () if tool.name == ctx.tool_call.name]
            if len(matches) != 1:
                raise ValueError("Child tool execution identity is unavailable or ambiguous")
            return matches[0]

        def check_binding(admission: _EffectAdmission, executor: AgentTool, call_id: str,
                          params: dict[str, Any]) -> None:
            prepared = admission.context
            if (admission.executor is not executor or admission.params is not params
                    or prepared.args is not params or admission.call_id != call_id
                    or admission.tool_name != executor.name
                    or prepared.tool_call is not admission.call or prepared.assistant_message is not admission.assistant
                    or admission.call.id != admission.call_id or admission.call.name != admission.tool_name
                    or admission.call.arguments is not admission.original_args
                    or not any(part is admission.call for part in admission.assistant.content)
                    or selected_executor(prepared) is not executor
                    or _argument_fingerprint(params) != admission.params_fingerprint
                    or _argument_fingerprint(admission.original_args) != admission.original_fingerprint):
                raise ValueError("Child tool invocation identity or approved arguments changed")

        def check_epochs(admission: _EffectAdmission) -> None:
            if (session.permissions is not original_permissions
                    or original_permissions.policy_revision != admission.permission_revision):
                manager.clear_session_rules()
                raise ValueError("Parent permission policy changed before child tool execution")
            if (session.agent is not original_agent
                    or original_agent.tool_selection_revision != admission.tool_selection_revision):
                manager.clear_session_rules()
                raise ValueError("Parent tool selection changed before child tool execution")
            if session.access is not admission.access or session.access.state.revision != admission.access_revision:
                manager.clear_session_rules()
                raise ValueError("Parent access scope changed before child tool execution")

        def check_effect(executor: AgentTool, call_id: str, params: dict[str, Any], *,
                         claim: object | None = None, after_effect: bool = False) -> _EffectAdmission:
            admission = admissions.get((id(executor), id(params)))
            if admission is None:
                raise ValueError("Child tool has no original permission admission for this invocation")
            check_binding(admission, executor, call_id, params)
            if not after_effect and admission.claim is not claim:
                raise ValueError("Child tool permission admission is already consumed")
            check(replace(admission.context, args=params))
            check_epochs(admission)
            safe = safe_call(admission.tool_name, params)
            if effect(admission.tool_name, params) != admission.effect:
                raise ValueError("Delegated capability effect changed after permission admission")
            if original_permissions.mode == "deny" and not safe:
                raise ValueError("Parent permission policy denies child tool execution")
            if (spec.access == "read-only" or session.access.state.read_only) and not safe:
                raise ValueError("Child/parent access became read-only before child tool execution")
            return admission

        def protect(tool: AgentTool, source: AgentTool | None = None) -> AgentTool:
            async def validate(params: dict[str, Any]) -> None:
                # A fresh child worker's executor does not replace the original
                # parent's registration fence. Validate both without executing either.
                seen: set[int] = set()
                for candidate in (None if tool.name in scoped else source, tool):
                    if (candidate is not None and candidate.validate_arguments is not None
                            and id(candidate.validate_arguments) not in seen):
                        seen.add(id(candidate.validate_arguments))
                        checked = candidate.validate_arguments(params)
                        if inspect.isawaitable(checked):
                            await checked
            async def invoke(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                             on_update: Any = None, *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
                admission = check_effect(protected, call_id, params)
                claim = object()
                key = id(protected), id(params)
                admissions[key] = replace(admission, claim=claim)
                await validate(params)
                admission = check_effect(protected, call_id, params, claim=claim)
                if skills.slugs:
                    await run_owned(parent.check_skills, skills)
                    admission = check_effect(protected, call_id, params, claim=claim)
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                if tool.execute is None:
                    raise ValueError("Delegated child tool has no executor")
                admissions[key] = replace(admission, started=True)
                return await tool.execute(call_id, params, cancel, on_update, ctx=ctx)
            async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
                if tool.name in scoped:
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError
                    caller = asyncio.get_running_loop()
                    async def update(result: AgentToolResult) -> None:
                        if on_update is not None:
                            async def deliver() -> None:
                                await on_update(result)
                            await settle_on(caller, deliver())
                    return await settle_on(session.capabilities.owner_loop,
                                           invoke(call_id, params, None, update, ctx=ctx))
                return await invoke(call_id, params, cancel, on_update, ctx=ctx)
            protected = replace(tool, execute=execute, validate_arguments=validate)
            return protected

        # Proxies keep the schema fixed even if a parent's mutable parameter dict
        # changes. Admission still checks the original source's live identity/schema.
        delegated_tools = tuple(replace(scoped.get(tool.name, tool), parameters=(
                                    grant.scoped_parameters(tool.name, scoped[tool.name].parameters)
                                    if tool.name in scoped else grant.provider_parameters(tool.name)))
                                for tool in grant.tools)
        delegated_tools = tuple(protect(skills.tool(tool, lambda: parent.live) if tool.name == "skill"
                                        else tool, source)
                                for tool, source in zip(delegated_tools, grant.tools, strict=True))
        extension_bindings: dict[str, tuple[AgentTool, AgentTool]] = {}
        extension_names = dict(state.extension_owners)

        def sync_tools() -> None:
            try:
                current = parent.capture_tools(None).tools if parent.live else ()
            except (ValueError, RuntimeError):
                current = ()  # Concurrent retirement removes inventory, not completed effect facts.
            current_extensions = extension.tools()
            selected = []
            for source, proxy in zip(grant.tools, delegated_tools, strict=True):
                if not any(source is value for value in current):
                    continue
                if source.name in extension_names:
                    binding = extension_bindings.get(source.name)
                    if binding is not None and any(binding[0] is value for value in current_extensions):
                        selected.append(binding[1])
                else:
                    selected.append(proxy)
            selected.extend(tool for tool in role_tools if parent.live
                            and self.capabilities.permits(spec.id, tool.name, (tool,)))
            agent.set_tools(tuple(selected))

        tools = tuple(tool for tool in delegated_tools if tool.name not in extension_names) + role_tools
        async def before(ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None
                         ) -> BeforeToolCallResult | None:
            nonlocal last_authority
            try:
                revision = original_permissions.policy_revision
                selection_revision = original_agent.tool_selection_revision
                access = session.access
                access_revision = access.state.revision
                authority = revision, selection_revision, access, access_revision
                if authority != last_authority:
                    manager.clear_session_rules()  # Revoke old child session approvals, even after restore-to-same.
                    last_authority = authority
                check(ctx)
                await run_owned(parent.check_skills, skills)
                def bound_call() -> bool:
                    return self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ())
                if (spec.access == "read-only" or session.access.state.read_only) and not (
                        bound_call() or safe_call(ctx.tool_call.name, ctx.args)):
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                blocked = await self.extensions.pre(extension, ctx)
                if blocked is not None:
                    return blocked
                check(ctx)  # Parent/child hooks may have rewritten action arguments.
                if (spec.access == "read-only" or session.access.state.read_only) and not (
                        bound_call() or safe_call(ctx.tool_call.name, ctx.args)):
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                if bound_call():
                    return None  # Actual host-bound context/report capability, not a name exemption.
                if session.permissions is not original_permissions:
                    return BeforeToolCallResult(block=True, reason="Parent permission owner changed")
                manager.set_mode(original_permissions.mode)
                approval_alive = self.approval_alive
                executor = selected_executor(ctx)
                key = id(executor), id(ctx.args)
                if key in admissions or any(row.params is ctx.args for row in admissions.values()):
                    raise ValueError("Child tool invocation already has a permission admission")
                admission = _EffectAdmission(
                    context=ctx, executor=executor, call=ctx.tool_call, assistant=ctx.assistant_message,
                    params=ctx.args, original_args=ctx.tool_call.arguments, call_id=ctx.tool_call.id,
                    tool_name=ctx.tool_call.name, params_fingerprint=_argument_fingerprint(ctx.args),
                    original_fingerprint=_argument_fingerprint(ctx.tool_call.arguments), permission_revision=revision,
                    tool_selection_revision=selection_revision, access=access, access_revision=access_revision,
                    effect=effect(ctx.tool_call.name, ctx.args))
                check_epochs(admission)
                result = await manager.before_tool_call(ctx, cancel, effect=admission.effect)
                check(ctx)
                await run_owned(parent.check_skills, skills)
                if (session.permissions is not original_permissions
                        or original_permissions.policy_revision != revision):
                    manager.clear_session_rules()
                    return BeforeToolCallResult(block=True, reason="Parent permission policy changed during approval")
                if (result is None and approval_alive is not None and not approval_alive()
                        and session.permissions.mode != "allow-all"
                        and not safe_call(ctx.tool_call.name, ctx.args)):
                    manager.clear_session_rules()
                    return BeforeToolCallResult(block=True, reason="Child approval UI owner changed during review")
                if session.permissions.mode == "deny" and not safe_call(ctx.tool_call.name, ctx.args):
                    return BeforeToolCallResult(block=True, reason="Parent permission policy changed to deny")
                if session.access.state.read_only and not safe_call(ctx.tool_call.name, ctx.args):
                    return BeforeToolCallResult(block=True, reason="Parent entered read-only scope during approval")
                if result is None:
                    check(ctx)
                    check_binding(admission, executor, ctx.tool_call.id, ctx.args)
                    check_epochs(admission)
                    if key in admissions or any(row.params is ctx.args for row in admissions.values()):
                        raise ValueError("Child tool invocation permission admission changed during approval")
                    admissions[key] = admission
                return result
            except (ValueError, RuntimeError) as exc:
                return BeforeToolCallResult(block=True, reason=str(exc))
            finally:
                sync_tools()

        async def after(ctx: AfterToolCallContext, cancel: asyncio.Event | None = None) -> AfterToolCallResult | None:
            try:
                try:
                    if self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ()):
                        check(ctx)
                    else:
                        admission = check_effect(selected_executor(ctx), ctx.tool_call.id, ctx.args,
                                                 after_effect=True)
                        if admission.call is not ctx.tool_call or admission.assistant is not ctx.assistant_message:
                            return None
                        if not admission.started:
                            return None
                except (ValueError, RuntimeError):
                    # Revocation after a started effect is not rollback or a failed
                    # effect. Preserve its real result, skip optional postprocessing.
                    return None
                return await self.extensions.post(extension, ctx)
            finally:
                for key, admission in tuple(admissions.items()):
                    if (admission.call is ctx.tool_call and admission.params is ctx.args
                            and admission.assistant is ctx.assistant_message
                            and admission.context.context is ctx.context):
                        admissions.pop(key, None)
                try:
                    sync_tools()
                except (ValueError, RuntimeError):
                    agent.set_tools(())  # Cleanup cannot replace an already completed tool result.

        agent: Agent
        async def context(messages: list[AgentMessage]) -> list[AgentMessage]:
            try:
                if not parent.live:
                    raise ValueError("Original parent owner retired before child model request")
                await run_owned(parent.check_skills, skills)
                result = await self.extensions.context(extension, messages)
                return result
            finally:
                sync_tools()

        async def input_transform(message: UserMessage, source: str,
                                  cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
            try:
                self.extensions.check_parent()
                return await extension.input(message, source, channel="child")
            finally:
                sync_tools()

        from xdog.agent.extensions.providers import ExtensionProviderStream
        base_stream = state.stream
        if isinstance(base_stream, ExtensionProviderStream):
            base_stream = base_stream.base
        routed_stream = selected.stream(ExtensionProviderStream(extension, base_stream))
        async def model_admission() -> None:
            if not parent.live:
                raise ValueError("Original parent owner retired before child model execution")
            await run_owned(parent.check_skills, skills)
            if not parent.live:
                raise ValueError("Original parent owner retired during child model admission")
        def guarded(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            def start_request() -> EventStream[AssistantMessage]:
                sync_tools()
                names = {tool.name for tool in agent.active_tools}
                request = replace(context, tools=tuple(tool for tool in context.tools or () if tool.name in names))
                return routed_stream(model, request, options)
            return ArchivedSummaryStream(start_request, model_admission)
        child_stream = preserve_model_metadata(guarded, routed_stream)
        agent = Agent(child_stream, parent=parent,
            config=AgentConfig(model=selected.id,
            system_prompt=selected.prompt(child_prompt("", spec)),
            tool_execution=state.tool_execution,
            context_window=selected.context_window, max_prompt_tokens=selected.max_prompt_tokens,
            supports_tool_calls=selected.supports_tool_calls, options=selected.options,
            compaction=selected.compaction(child_stream)), tools=tools,
            tool_ctx={"fs_workspace": str(session.working_dir)}, skills=skills, active_skills=skills.slugs,
            transform_context=context,
            before_tool_call=before, after_tool_call=after, input_transform=input_transform)
        if allowed_tools is not None:
            allowed = set(allowed_tools)
            agent.set_tools(tuple(tool for tool in agent.state.tools if tool.name in allowed))
        from xdog.agent.extensions.inspection import capture
        inspection_session = str(extension.context().get("session_id", ""))
        def inspection() -> dict[str, Any] | None:
            if (extension._closed or self.extensions.parent._closed
                    or extension.context().get("session_id") != inspection_session):
                return None
            metadata = (extension.provider_model(agent.state.model) if agent.state.model.startswith("ext-")
                        else selected.metadata)
            return capture(agent, inspection_session, spec.id, metadata, usage_floor=0)
        extension.inspection = inspection
        def inventory() -> dict[str, Any] | None:
            from xdog.agent.extensions.tool_inventory import capture as capture_tools
            if (extension._closed or self.extensions.parent._closed
                    or extension.context().get('session_id') != inspection_session):
                return None
            return capture_tools(agent, inspection_session, spec.id)
        extension.tool_inventory = inventory

        self.extensions.bind_pending_messages(spec.id, agent)
        await self.extensions.start(extension_spec, extension)
        for tool in extension.tools():
            if (tool.name in extension_names
                    and extension.tool_owner(tool.name) == extension_names[tool.name]):
                index = grant.names.index(tool.name)
                extension_bindings[tool.name] = tool, protect(replace(tool,
                    parameters=grant.provider_parameters(tool.name)), grant.tools[index])
        sync_tools()
        return agent

    async def finish(self, spec: ChildSpec) -> None:
        self.snapshots.pop(spec.id, None)
        self.parents.pop(spec.id, None)
        self.skill_limits.pop(spec.id, None)
        self.delegations.pop(spec.id, None)
        self.scoped_tools.pop(spec.id, None)
        self.tool_limits.pop(spec.id, None)
        manager = self.permissions.pop(spec.id, None)
        if manager is not None:
            manager.close()
        await self.extensions.close(spec)

    def resolve(self, child_id: str, request_id: str, decision: PermissionDecision) -> bool:
        manager = self.permissions.get(child_id)
        return manager is not None and manager.resolve(request_id, decision)

    def has_pending(self, child_id: str, request_id: str) -> bool:
        manager = self.permissions.get(child_id)
        return manager is not None and manager.has_pending(request_id)

    def abort(self) -> None:
        for manager in tuple(self.permissions.values()):
            manager.deny_all()
        self.runtime.abort()

    def close(self) -> None:
        self._closed = True
        self.session._retire_capabilities()
        for manager in tuple(self.permissions.values()):
            manager.close()
        self.runtime.close()
