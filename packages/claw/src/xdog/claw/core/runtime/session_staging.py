"""A prepared target AgentSession with buffered entry/access persistence, never an active owner."""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.claw.core.persistence.transcript_store import TranscriptStore

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.session import AgentSession


T = TypeVar('T')


def owned_run(work: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """The synchronous API also runs inside SDK event loops; never nest asyncio.run."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='xdog-claw-stage') as pool:
        return pool.submit(lambda: asyncio.run(work())).result()


class BufferedTranscriptStore(TranscriptStore):
    def __init__(self, original: TranscriptStore, session_id: str, source: list[dict[str, Any]],
                 access: Any, entries: Any, branch_id: str, active_tools: tuple[str, ...] | None = None) -> None:
        self.__dict__.update(original.__dict__)
        self._original = original
        self._target = session_id
        self._source = copy.deepcopy(source)
        self._promoted = False
        self._selected_tools = active_tools
        self._lock = threading.RLock()
        self._catalog = copy.deepcopy(original._catalog)
        self._index = copy.deepcopy(original._index)
        self._access = copy.deepcopy(original._access)
        self._extension_entries = copy.deepcopy(original._extension_entries)
        self._access[self._path(session_id).with_suffix('.access.json').name] = copy.deepcopy(access)
        self._extension_entries[self._path(session_id).with_suffix('.extensions.json').name] = copy.deepcopy(entries)
        self._catalog['active_branches'][session_id] = branch_id

    def get_active_tools(self, session_id: str, branch_id: str = "") -> tuple[str, ...] | None:
        if not self._promoted and session_id == self._target and not branch_id:
            return self._selected_tools
        return self._original.get_active_tools(session_id, branch_id)

    def save_active_tools(self, session_id: str, selected: tuple[str, ...] | None, *,
                          before_replace: Callable[[], None] | None = None) -> None:
        if not self._promoted:
            raise ValueError('Unadmitted staged tool selection')
        self._original.save_active_tools(session_id, selected, before_replace=before_replace)

    def load_transcript(self, session_id: str) -> list[dict[str, Any]]:
        if not self._promoted and session_id == self._target:
            return copy.deepcopy(self._source)
        return self._original.load_transcript(session_id)

    def save_extension_entries(self, session_id: str, value: dict[str, Any]) -> None:
        if self._promoted:
            self._original.save_extension_entries(session_id, value)
        elif session_id == self._target:
            self._extension_entries[self._path(session_id).with_suffix('.extensions.json').name] = copy.deepcopy(value)
        else:
            raise ValueError('Staged entry owner mismatch')

    def save_access(self, session_id: str, access: dict[str, Any]) -> None:
        if self._promoted:
            self._original.save_access(session_id, access)
        elif session_id == self._target:
            self._access[self._path(session_id).with_suffix('.access.json').name] = copy.deepcopy(access)
        else:
            raise ValueError('Staged access owner mismatch')

    def _atomic(self, *args: Any, **kwargs: Any) -> None:
        raise ValueError('Unadmitted staged persistence operation')

    def promote(self) -> None:
        self._promoted = True


@dataclass
class PreparedSession:
    session: AgentSession
    store: BufferedTranscriptStore
    runtime: GroupRuntime

    async def aclose(self) -> None:
        await self.session.children.supervisor.aclose()
        await self.session.extensions.aclose()
        self.session.dispose()

    def close(self) -> None:
        # Preparation/navigation runs in a disk owner thread, not the gateway reader loop.
        owned_run(self.aclose)


def prepare(runtime: GroupRuntime, session_id: str, source: list[dict[str, Any]], access: Any,
            entries: Any, branch_id: str, alive: Callable[[], bool], *,
            active_tools: tuple[str, ...] | None = None) -> PreparedSession:
    from xdog.claw.core.runtime.session import AgentSession
    from xdog.claw.core.types import SessionMeta
    if not alive():
        raise asyncio.CancelledError
    original = runtime.transcript_store
    buffered = BufferedTranscriptStore(original, session_id, source, access, entries, branch_id, active_tools)
    shadow = copy.copy(runtime)
    shadow.transcript_store = buffered
    shadow._active_session = None
    shadow._retired_extensions = []
    shadow.model = original.get_model(session_id) or runtime._configured_model
    shadow.context_window, shadow.max_prompt_tokens = runtime._configured_limits
    row = next((row for row in original.list_sessions(runtime.group.id) if row['session_id'] == session_id), None)
    if row is None:
        raise ValueError('Staged session belongs to another group or is unavailable')
    meta = SessionMeta(session_id=session_id, group_id=runtime.group.id, created_at=row['created_at'],
                       last_active=row.get('last_active', row['created_at']), turn_count=row.get('turn_count', 0))
    staged = AgentSession(runtime=shadow, session_meta=meta)
    result = PreparedSession(staged, buffered, shadow)
    async def initialize() -> PreparedSession:
        try:
            staged.extensions.context = lambda: {
                'session_id': session_id, 'group_id': runtime.group.id, 'cwd': str(runtime.workspace_dir),
                'model': shadow.model, 'read_only': staged.access.state.read_only, 'permission_mode': 'configured',
                'staged': True}
            await staged.initialize_extensions(require_model=True)
            if not alive():
                raise asyncio.CancelledError
            if staged.extensions._failed - staged.extensions._unloaded:
                raise ValueError('Target extension activation failed; current group context retained')
            if shadow.model.startswith('ext-'):
                profile = staged.extensions.provider_model(shadow.model)
                if profile is None:
                    raise ValueError('Saved extension model unavailable in target runtime; no fallback')
                if not profile.reasoning:
                    from dataclasses import replace
                    staged.agent.set_options(replace(staged.agent.options, thinking=None))
            staged._restore_messages()
            return result
        except BaseException:
            await result.aclose()
            raise
    return owned_run(initialize)
