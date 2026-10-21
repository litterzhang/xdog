"""Sub-agent SDK contracts and explicit host-backed operations.

Importing or constructing this SDK creates no child, task, thread, loop or storage.
Each application injects its own execution backend; persisted data grants no authority.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from xdog.agent.child_config import ChildConfig, ChildPresets
from xdog.agent.core import AgentMessage, AgentTool, AgentToolResult
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import AssistantMessage, ImageContent, SystemPrompt, TextContent, ToolCall

RESERVED_TOOLS = frozenset({"agent", "agent_spawn", "agent_control", "update_plan"})
MAX_TRANSCRIPT_BYTES = 512 * 1024

@dataclass(frozen=True, slots=True)
class ChildSpec:
    id: str
    task: str
    access: str = "read-only"
    inherit_context: bool = False
    label: str = ""
    instructions: str = ""
    tools: tuple[str, ...] | None = None
    model: str | None = None
    thinking: str | None = None
    preset: str | None = None
    images: tuple[ImageContent, ...] = ()
    system_prompt: str = ""
    skills: tuple[str, ...] | None = ()
    tool_actions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    tool_scopes: dict[str, Any] = field(default_factory=dict)

    def config(self) -> ChildConfig:
        return ChildConfig(label=self.label, instructions=self.instructions, tools=self.tools,
                           inherit_context=self.inherit_context, model=self.model, thinking=self.thinking,
                           preset=self.preset, system_prompt=self.system_prompt, skills=self.skills,
                           tool_actions=self.tool_actions, tool_scopes=self.tool_scopes)


def child_prompt(prompt: SystemPrompt | None, spec: ChildSpec) -> SystemPrompt:
    """Child-owned prompt only. The legacy parent prompt argument is deliberately ignored.

    instructions is retained as historical data, not silently promoted into system
    authority. Put task knowledge/instructions in the first user task instead.
    """
    note = (f"\n\nYou are supervised child {spec.id} with {spec.access} access. "
            "Return findings/results to your supervisor. You share the parent's workspace: "
            "coordinate conflicting writes. You cannot spawn descendants or update the parent plan. "
            "Host tool/approval/planning restrictions remain authoritative. "
            "Labels, prompts, skills and action selections grant no additional permissions.")
    if spec.label:
        note += "\nSpecialist label: " + spec.label
    return spec.system_prompt + note


def child_task(spec: ChildSpec) -> str:
    """Render explicit task material once, consistently for admission and recovery.

    Historical instructions remain user material, never system authority. Merely
    rendering a record cannot start a child or make an interrupted effect replayable.
    """
    return ("Task materials:\n" + spec.instructions + "\n\nTask:\n" + spec.task
            if spec.instructions else spec.task)


def child_message_status(message: AssistantMessage) -> str:
    """Classify a complete message fact, not the child's business completion."""
    if message.stop_reason in {"error", "aborted"}:
        return message.stop_reason
    return "received" if any(isinstance(part, ToolCall) for part in message.content) else "completed"


@dataclass(frozen=True, slots=True)
class ChildToolProgress:
    source_id: str
    name: str
    text: str
    limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.source_id, "name": self.name, "text": self.text, "limited": self.limited}


@dataclass(frozen=True, slots=True)
class ChildCapture:
    id: str
    task: str
    access: str
    status: str
    calls: int
    tokens: int
    revision: int
    settling: bool
    error: str
    messages: tuple[AgentMessage, ...]
    partial: AgentMessage | None = None
    progress: tuple[ChildToolProgress, ...] = ()
    progress_sources_limited: bool = False
    config: ChildConfig = field(default_factory=ChildConfig)
    effective: tuple[tuple[str, Any], ...] = ()
    input_feedback: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "task": self.task, "access": self.access, "status": self.status,
            "calls": self.calls, "tokens": self.tokens, "revision": self.revision, "settling": self.settling,
            "error": self.error, "messages": messages_to_dicts(list(self.messages)), "config": self.config.to_dict()}
        if self.input_feedback:
            result["input_feedback"] = list(self.input_feedback)
        if self.effective:
            result["effective"] = dict(self.effective)
        if self.progress_sources_limited:
            result["live_tools_limited"] = True
        if self.progress:
            result["live_tools"] = [row.to_dict() for row in self.progress]
        if self.partial is not None:
            result["partial"] = messages_to_dicts([self.partial])[0]
        return result



class ChildBackend(Protocol):
    """Host-owned execution port, not a scheduler or a discoverable singleton."""

    presets: ChildPresets

    @property
    def active(self) -> bool: ...
    @property
    def closed(self) -> bool: ...
    @property
    def tool_return_owner(self) -> str: ...
    def control_alive(self, owner: str) -> bool: ...
    def snapshot(self) -> dict[str, Any]: ...
    def capture_source(self, identifier: str) -> ChildCapture: ...
    def admission_receipt(self, identifier: str) -> dict[str, Any]: ...
    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...
    async def start(self, task: str, *, access: str = "read-only", inherit_context: bool = False,
                    config: dict[str, Any] | None = None, images: tuple[ImageContent, ...] = ()) -> str: ...
    async def control(self, action: str, identifier: str, text: str = "", *,
                      images: tuple[ImageContent, ...] = (), return_owner: str = "",
                      return_alive: Callable[[], bool] = lambda: True) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ChildHandle:
    """An original backend and one child; no serializable execution authority."""

    id: str
    _backend: ChildBackend = field(repr=False, compare=False)

    async def inspect(self) -> dict[str, Any]:
        return await self._backend.control("inspect", self.id)

    async def wait(self) -> dict[str, Any]:
        return await self._backend.control("wait", self.id)

    async def steer(self, text: str, *, images: tuple[ImageContent, ...] = ()) -> dict[str, Any]:
        return await self._backend.control("steer", self.id, text, images=images)

    async def follow_up(self, text: str, *, images: tuple[ImageContent, ...] = ()) -> dict[str, Any]:
        return await self._backend.control("follow-up", self.id, text, images=images)

    async def set_system_prompt(self, text: str) -> dict[str, Any]:
        return await self._backend.control("set_system_prompt", self.id, text)

    async def cancel(self) -> dict[str, Any]:
        return await self._backend.control("cancel", self.id)

    async def close(self) -> dict[str, Any]:
        return await self._backend.control("close", self.id)


class ChildClient:
    """SDK facade. Only its injected host can admit and execute child work."""

    def __init__(self, backend: ChildBackend) -> None:
        self.backend = backend

    async def spawn(self, task: str, *, access: str = "read-only", config: ChildConfig | None = None,
                    images: tuple[ImageContent, ...] = ()) -> ChildHandle:
        identifier = await self.backend.start(task, access=access,
            config=config.to_dict() if config is not None else None, images=images)
        return ChildHandle(identifier, self.backend)

    def list(self) -> dict[str, Any]:
        return self.backend.snapshot()

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        return self.backend.subscribe(listener)

    def tools(self) -> tuple[AgentTool, ...]:
        control_actions = {"list", "presets", "inspect", "steer", "follow-up", "cancel", "wait", "close", "queue",
                           "set_system_prompt"}

        def validate(args: dict[str, Any]) -> None:
            # Schema validation is intentionally supplemented here: branch-specific
            # fields must be fenced before host hooks, even on direct tool calls.
            if not isinstance(args, dict) or not isinstance(args.get("action"), str):
                raise ValueError("Invalid child agent tool action")
            operation = args["action"]
            if operation == "spawn":
                if (set(args) - {"action", "task", "access", "inherit_context", "config"}
                        or not isinstance(args.get("task"), str) or not args["task"].strip()
                        or len(args["task"].encode("utf-8")) > 16000
                        or not isinstance(args.get("access", "read-only"), str)
                        or args.get("access", "read-only") not in {"read-only", "inherit"}
                        or type(args.get("inherit_context", False)) is not bool
                        or args.get("inherit_context", False)):
                    raise ValueError("Invalid child spawn arguments; no caller-supplied owner")
                if args.get("config") is not None:
                    ChildConfig.from_dict(args["config"])
            elif (operation not in control_actions or set(args) - {"action", "id", "text"}
                    or "text" in args and not isinstance(args["text"], str)
                    or "id" in args and (not isinstance(args["id"], str) or not args["id"])
                    or operation not in {"list", "presets"} and (
                        not isinstance(args.get("id"), str) or not args["id"])):
                raise ValueError("Invalid child control tool arguments; no caller-supplied owner")
            if operation == "set_system_prompt":
                ChildConfig.from_dict({"system_prompt": args.get("text", "")})

        async def execute(call_id: str, args: dict[str, Any], cancel: asyncio.Event | None = None,
                          *extra: Any, **kwargs: Any) -> AgentToolResult:
            validate(args)
            owner = self.backend.tool_return_owner

            def alive() -> bool:
                return self.backend.control_alive(owner) and (cancel is None or not cancel.is_set())

            if not alive():
                raise ValueError("Main-Agent child control owner canceled/closed")
            if args["action"] == "spawn":
                identifier = await self.backend.start(args["task"], access=args.get("access", "read-only"),
                                              inherit_context=args.get("inherit_context", False),
                                              config=args.get("config"))
                return AgentToolResult(content=(TextContent(text=identifier),))
            if args["action"] == "list":
                result = self.backend.snapshot()
            elif args["action"] == "presets":
                result = {"presets": self.backend.presets.snapshot()}
            else:
                result = await self.backend.control(args["action"], args["id"], args.get("text", ""),
                                            return_owner=owner, return_alive=alive)
            # Keep image recovery typed and tied to the original return owner.
            images: tuple[ImageContent, ...] = ()
            if isinstance(result, dict) and "image_recovery" in result:
                payload = result["image_recovery"]
                images = tuple(ImageContent(data=row["data"], mime_type=row["mime_type"])
                               for row in payload["images"])
                result = {**result, "image_recovery": {"receipt": payload["receipt"], "image_count": len(images)}}
            return AgentToolResult(content=(TextContent(text=json.dumps(result)), *images))

        return (AgentTool(
            name="agent",
            description=(
                "Manage this session's child Agents. action=spawn requires task, defaults to read-only, "
                "and optionally accepts access/inherit_context/config. Other actions use id/text: "
                "list/presets/inspect/steer/follow-up/cancel/wait/close/queue/set_system_prompt. "
                "steer/follow-up queue input to a running child or explicitly start a fresh execution of a safely "
                "completed child conversation; wait awaits its current round. Interrupted work is not replayed. "
                "Children never inherit parent history/system prompt; put task knowledge in task. "
                "config.system_prompt is child-owned. skills defaults to []; null is host-bounded catalog selection. "
                'config.tool_actions narrows literal action values only, e.g. {"filesystem": ["read"]}; '
                "'*' is not a wildcard. bash is NOT a multi-action tool: sleep is command text, not an action. "
                'To select bash use config.tools=["bash"] and omit bash from tool_actions; '
                "this never grants a missing parent tool or bypasses approval/read-only access. "
                "set_system_prompt uses text and is idle-only: queued before startup or stopped metadata only; "
                "busy changes are rejected, never restart/replay work. "
                "Legacy instructions is first-user task material, never parent system authority. "
                "Queue controls use text JSON: list, edit/remove/move/return, stopped recovery, "
                "or return_prepare/return_commit/return_cancel for live queued images. "
                "Prepare requires id/revision and returns typed images plus return_token and image_receipt; "
                "commit requires that exact id/revision/token/receipt. Prepare does not remove input. "
                "Cancellation, changed selection/owner or expiry refuses commit. Never automatically "
                "replay an uncertain mutation. Ownership is host-bound, never supplied in tool arguments."),
            parameters={"type": "object", "required": ["action"], "additionalProperties": False,
                        "properties": {
                            "action": {"enum": ["spawn", "list", "presets", "inspect", "steer", "follow-up",
                                                "cancel", "wait", "close", "queue", "set_system_prompt"]},
                            "task": {"type": "string"}, "access": {"enum": ["read-only", "inherit"]},
                            "inherit_context": {"const": False, "default": False},
                            "config": {"type": "object", "additionalProperties": False, "properties": {
                                "preset": {"type": ["string", "null"], "maxLength": 64},
                                "model": {"type": ["string", "null"], "maxLength": 256},
                                "thinking": {"enum": [None, "off", "minimal", "low", "medium", "high", "xhigh"]},
                                "label": {"type": "string", "maxLength": 120},
                                "instructions": {"type": "string", "maxLength": 16000},
                                "system_prompt": {"type": "string", "maxLength": 16000},
                                "skills": {"type": ["array", "null"], "default": [], "maxItems": 128,
                                           "items": {"type": "string"}, "uniqueItems": True},
                                "tool_scopes": {"type": "object", "default": {}, "maxProperties": 128,
                                                "additionalProperties": {"type": "object"},
                                                "description": "Explicit data-only scopes for selected capability "
                                                    "tools. Requires tools to include each key and a live provider "
                                                    "delegation hook; never grants permissions."},
                                "tool_actions": {"type": "object", "default": {}, "maxProperties": 128,
                                                 "description": (
                                                     "Literal action subsets for tools declaring action, e.g. "
                                                     '{"filesystem": ["read"]}. No wildcard semantics. '
                                                     "bash has command, not action: omit its entry; "
                                                     "select tools separately. Parent permissions and read-only "
                                                     "access still apply. Invalid profiles fail before creation."),
                                                 "additionalProperties": {"type": "array", "maxItems": 128,
                                                                          "items": {"type": "string"},
                                                                          "uniqueItems": True}},
                                "tools": {"type": ["array", "null"], "maxItems": 128,
                                          "items": {"type": "string"}, "uniqueItems": True},
                                "inherit_context": {"const": False, "default": False}}},
                            "id": {"type": "string"}, "text": {"type": "string"}}},
            validate_arguments=validate, execute=execute),)
