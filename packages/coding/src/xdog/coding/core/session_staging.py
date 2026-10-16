"""Prepare trusted extension workers against buffered target state before navigation publication."""
from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.access import AccessState
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries
from xdog.ai.types import Model, ThinkingLevel
from xdog.coding.core.model_catalog import validated
from xdog.coding.core.settings_manager import SettingsManager

T = TypeVar('T')


@dataclass(frozen=True)
class StagedExtensions:
    runtime: ExtensionRuntime
    model: Model | None
    thinking: ThinkingLevel | None

    def close(self) -> None:
        settle(lambda: self.runtime.aclose())


def settle(work: Callable[[], Coroutine[Any, Any, T]]) -> T:
    # Called from the synchronous session API, including its owned command loop.
    # One bootstrap thread owns worker preparation/cleanup, never nested asyncio.run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='xdog-session-stage') as pool:
        return pool.submit(lambda: asyncio.run(work())).result()


def prepare(settings: SettingsManager, directory: Path, session_id: str, model: str,
            thinking: ThinkingLevel | None, entries: Any, access: Any, permission: str,
            native: Model | None, alive: Callable[[], bool]) -> StagedExtensions:
    state = AccessState.from_dict(access)
    staged = ExtensionRuntime(settings.trusted_extensions, settings.extension_root, directory,
        settings=settings.extension_settings,
        context=lambda: {'session_id': session_id, 'cwd': str(directory), 'model': model,
                         'read_only': state.read_only, 'permission_mode': permission, 'staged': True},
        entries=ExtensionEntries(entries, lambda value: None))
    async def initialize() -> StagedExtensions:
        try:
            if not alive():
                raise asyncio.CancelledError
            await staged.initialize()
            if not alive():
                raise asyncio.CancelledError
            if staged._failed - staged._unloaded:
                raise ValueError('Target extension activation failed; current context retained')
            profile = staged.provider_model(model) if model.startswith('ext-') else native
            if model.startswith('ext-') and profile is None:
                raise ValueError('Saved extension model unavailable in target runtime; no fallback')
            if profile is not None:
                profile = validated(profile)
                if not profile.reasoning:
                    selected_thinking = None
                else:
                    selected_thinking = thinking
                    if (thinking is not None and profile.supported_efforts is not None
                            and thinking not in profile.supported_efforts):
                        raise ValueError('Target extension model does not advertise saved effort; no fallback')
            else:
                selected_thinking = thinking
            return StagedExtensions(staged, profile, selected_thinking)
        except BaseException:
            await staged.aclose()
            raise
    return settle(initialize)
