"""Detached session data: workers own disk IO, never Agent construction/publication."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.agent import AgentMessage
from xdog.agent.access import AccessState
from xdog.agent.extensions.compaction import saved_record
from xdog.agent.skills.types import SkillPlacement
from xdog.ai.types import SystemPromptBlock
from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
from xdog.claw.core.persistence.transcript_store import TranscriptStore
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


def history(store: TranscriptStore, identifier: str) -> PreparedHistory:
    rows = store.load_transcript(identifier)
    metadata = next((saved_record(row.get('compaction')) for row in reversed(rows)
                     if row.get('is_compaction') is True), None)
    return PreparedHistory(tuple(transcript_to_messages(rows)),
        tuple((index, str(row.get('channel', ''))) for index, row in enumerate(rows)),
        bool(rows and rows[0].get('is_compaction') is True), json.dumps(metadata) if metadata is not None else None)


def read(store: TranscriptStore, meta: SessionMeta, workspace: Path, configured_model: str,
         *, include_history: bool = True, skills: SkillManager | None = None) -> PreparedSession:
    with store._lock:
        model = store.get_model(meta.session_id)
        active_skills = tuple(sorted(store.get_active_skills(meta.session_id)))
        return PreparedSession(meta, workspace, store._sessions_dir, model,
            store.get_reasoning(meta.session_id, model or configured_model or 'test/dummy'),
            store.get_active_tools(meta.session_id), active_skills,
            AccessState.from_dict(store.get_access(meta.session_id)),
            json.dumps(store.get_extension_entries(meta.session_id)),
            history(store, meta.session_id) if include_history else None, SkillPlacement.read(skills, active_skills))


@dataclass(frozen=True)
class SessionCheck:
    meta: SessionMeta | None
    reset_due: bool


def check(store: TranscriptStore, group: str, daily_hour: int, idle_seconds: int, *, allow_reset: bool) -> SessionCheck:
    with store._lock:
        meta = store.get_active_session(group)
        restricted = meta is not None and AccessState.from_dict(store.get_access(meta.session_id)).read_only
        due = allow_reset and not restricted and (store.needs_daily_reset(group, daily_hour)
                                                  or store.needs_idle_reset(group, idle_seconds))
        return SessionCheck(meta, due)


def prepare(store: TranscriptStore, group: str, workspace: Path, configured_model: str,
            expected: SessionMeta | None, *, reset: bool, skills: SkillManager | None = None) -> PreparedSession:
    with store._lock:
        if store.get_active_session(group) != expected:
            raise ValueError('Session preparation owner changed before persistence')
        meta = store.reset_session(group) if reset else expected or store.create_session(group)
        return read(store, meta, workspace, configured_model, skills=skills)


@dataclass(frozen=True)
class PromptTool:
    name: str
    description: str


def prompt(workspace: Path, tools: tuple[PromptTool, ...], model: str,
           *, bootstrap: bool) -> tuple[SystemPromptBlock, ...]:
    from xdog.claw.core.prompt import build_system_prompt, run_bootstrap
    return build_system_prompt(workspace, tools=list(tools), model=model,
                               bootstrap_content=run_bootstrap(workspace) if bootstrap else None)
