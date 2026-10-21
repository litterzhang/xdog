"""Detached session data: workers own disk IO, never Agent construction/publication."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.agent import AgentMessage, StreamFn
from xdog.agent.access import AccessState
from xdog.agent.extensions.compaction import saved_record
from xdog.agent.extensions.location import ExtensionRoot
from xdog.agent.skills.types import SkillPlacement
from xdog.ai.types import Model, SystemPromptBlock
from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.child_store import ChildStore
from xdog.claw.core.types import SessionMeta

if TYPE_CHECKING:
    from xdog.agent.skills.manager import SkillManager


@dataclass(frozen=True)
class PreparedHistory:
    messages: tuple[AgentMessage, ...]
    channels: tuple[tuple[int, str], ...]
    has_compaction: bool
    compaction_json: str | None


@dataclass(frozen=True)
class PreparedChildren:
    branch: str
    records_json: str
    diagnostic: str = ""


def children(store: TranscriptStore, identifier: str) -> PreparedChildren:
    branch = store.active_branch(identifier) or "head"
    reader = ChildStore.scoped(store._sessions_dir, identifier, branch)
    diagnostic = ""
    try:
        records = reader.load()
    except (ValueError, TypeError, OSError, UnicodeError) as exc:
        records = []
        diagnostic = "Child catalog unavailable; repair saved records before delegation: " + str(exc)[:500]
    return PreparedChildren(branch, json.dumps(records), diagnostic)


@dataclass(frozen=True)
class PreparedModel:
    model_id: str
    metadata: Model | None
    supports_tool_calls: bool | None


def default_stream() -> StreamFn:
    """Unpublished provider adapter resource; never a model call or Agent loop."""
    import xdog.ai as ai
    from xdog.agent.helpers import stream_fn_from_provider
    return stream_fn_from_provider(ai.load())


def model_info(model_id: str, stream: StreamFn) -> PreparedModel:
    from xdog.agent.core import ModelStreamFn
    from xdog.agent.helpers import model_supports_tool_calls
    metadata = (stream.resolve_model(model_id) if isinstance(stream, ModelStreamFn)
                and not model_id.startswith("ext-") else None)
    return PreparedModel(model_id, metadata,
        metadata.supports_tool_calls if metadata is not None else model_supports_tool_calls(model_id))


@dataclass(frozen=True)
class PreparedSession:
    meta: SessionMeta
    workspace: Path
    store_root: Path
    saved_model: str
    thinking: str | None
    active_tools: tuple[str, ...] | None
    active_skills: tuple[str, ...]
    access: AccessState
    entries_json: str
    history: PreparedHistory | None
    skills: SkillPlacement
    children: PreparedChildren
    extension_root: ExtensionRoot
    model: PreparedModel


def history(store: TranscriptStore, identifier: str) -> PreparedHistory:
    rows = store.load_transcript(identifier)
    metadata = next((saved_record(row.get('compaction')) for row in reversed(rows)
                     if row.get('is_compaction') is True), None)
    return PreparedHistory(tuple(transcript_to_messages(rows)),
        tuple((index, str(row.get('channel', ''))) for index, row in enumerate(rows)),
        bool(rows and rows[0].get('is_compaction') is True), json.dumps(metadata) if metadata is not None else None)


def read(store: TranscriptStore, meta: SessionMeta, workspace: Path, configured_model: str,
         *, include_history: bool = True, skills: SkillManager | None = None,
         extension_root: Path, stream_fn: StreamFn) -> PreparedSession:
    with store._lock:
        model = store.get_model(meta.session_id)
        active_skills = tuple(sorted(store.get_active_skills(meta.session_id)))
        return PreparedSession(meta, workspace, store._sessions_dir, model,
            store.get_reasoning(meta.session_id, model or configured_model or 'test/dummy'),
            store.get_active_tools(meta.session_id), active_skills,
            AccessState.from_dict(store.get_access(meta.session_id)),
            json.dumps(store.get_extension_entries(meta.session_id)),
            history(store, meta.session_id) if include_history else None, SkillPlacement.read(skills, active_skills),
            children(store, meta.session_id), ExtensionRoot.read(extension_root),
            model_info(model or configured_model, stream_fn))


@dataclass(frozen=True)
class SessionCheck:
    meta: SessionMeta | None
    reset_due: bool


def check(store: TranscriptStore, group: str, daily_hour: int, idle_seconds: int, *, allow_reset: bool) -> SessionCheck:
    with store._lock:
        meta = store.get_active_session(group)
        if meta is None and store.list_sessions(group):
            raise ValueError('Committed current pointer missing; archives are readonly data, not an executable context')
        if meta is not None:
            store.load_transcript(meta.session_id)  # Check before reset can replace its pointer.
        restricted = meta is not None and AccessState.from_dict(store.get_access(meta.session_id)).read_only
        due = allow_reset and not restricted and (store.needs_daily_reset(group, daily_hour)
                                                  or store.needs_idle_reset(group, idle_seconds))
        return SessionCheck(meta, due)


def prepare(store: TranscriptStore, group: str, workspace: Path, configured_model: str,
            expected: SessionMeta | None, *, reset: bool, skills: SkillManager | None = None,
            extension_root: Path, stream_fn: StreamFn) -> PreparedSession:
    with store._lock:
        if store.get_active_session(group) != expected:
            raise ValueError('Session preparation owner changed before persistence')
        meta = store.reset_session(group) if reset else expected or store.create_session(group)
        return read(store, meta, workspace, configured_model, skills=skills,
                    extension_root=extension_root, stream_fn=stream_fn)


@dataclass(frozen=True)
class PromptTool:
    name: str
    description: str


def prompt(workspace: Path, tools: tuple[PromptTool, ...], model: str,
           *, bootstrap: bool) -> tuple[SystemPromptBlock, ...]:
    from xdog.claw.core.prompt import build_system_prompt, run_bootstrap
    return build_system_prompt(workspace, tools=list(tools), model=model,
                               bootstrap_content=run_bootstrap(workspace) if bootstrap else None)


def reset(store: TranscriptStore, group: str, expected: SessionMeta | None) -> SessionMeta:
    """Original binding only; no Runtime/Session/Agent mutation in its worker."""
    with store._lock:
        if store.get_active_session(group) != expected or store._load_index() != store._index:
            raise ValueError('Reset pointer owner changed before persistence')
        if expected is None and store.list_sessions(group):
            raise ValueError('Committed current pointer missing; readonly archives cannot authorize reset')
        return store.reset_session(group)
