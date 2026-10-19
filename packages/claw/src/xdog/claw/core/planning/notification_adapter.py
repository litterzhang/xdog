"""Temporary legacy Goal hint projection, composed by the capability, never ACP.

This compatibility of application composition is not a persisted-format migration.
It is removed with the separately planned standalone Goal host adapter.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.types import ExternalInput, GroupInput

if TYPE_CHECKING:
    from xdog.claw.core.observers.journal import ObserverJournal
    from xdog.claw.core.runtime.orchestrator import Orchestrator


async def project_notification(application: Orchestrator, journal: ObserverJournal,
                               receipt: InputRecord, value: GroupInput) -> GroupInput:
    if not isinstance(value, ExternalInput):
        return value
    binding = await journal.occurrence(receipt)
    if binding is None or binding['cancelled']:
        return value
    scope = receipt.envelope.scope
    observer = await journal.get(scope.group_id, scope.workspace, str(binding['observer_id']))
    if observer is None or not observer.owner or not observer.notify_only:
        return value
    if await journal.message(observer, receipt.envelope.message.id) != receipt.envelope.message:
        return value
    if await journal.receipts.namespace(scope) != observer.inbox_id:
        return value
    runtime = application._runtimes.get(scope.group_id)
    if runtime is None:
        return value
    goal = runtime.goal_manager.tracker.get_goal(observer.owner)
    if goal is not None and goal.group_id == scope.group_id and goal.observer_id in {'', observer.id}:
        return replace(value, metadata={**value.metadata, '_goal_id': goal.id})
    return value
