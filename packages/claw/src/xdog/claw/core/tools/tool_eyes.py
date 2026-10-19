"""Thin group-scoped Eyes management; the host binds endpoints and storage."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action

if TYPE_CHECKING:
    from xdog.claw.core.observers.manager import ManagedObservers


def _service(ctx: dict[str, Any]) -> tuple[ManagedObservers, str]:
    service, group = ctx.get('_eyes'), ctx.get('group_id')
    if service is None or not isinstance(group, str) or not group.strip():
        raise ValueError('Eyes requires a running gateway with eyes_enabled: true and current group context')
    from xdog.claw.core.observers.manager import ManagedObservers
    if not isinstance(service, ManagedObservers):
        raise ValueError('Eyes requires the gateway-owned observer service')
    return service, group


def _render(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ValueError('Observer inspection exceeds the bounded tool response; inspect one observer at a time')
    return text


class EyesTool(ToolDef):
    name = 'eyes'
    description = ('Manage current-group Eyes observers; timing uses native scheduler sources. '
                   'Intake, execution and channel delivery are distinct outcomes.')

    @action('create', description='Create an observer using an Eyes scheduler or workspace-file source',
            source=Param('object', required=True, description='Eyes source object, e.g. '
                         '{"type":"scheduler","schedule":{"at":"2030-01-01T09:00:00+08:00"}}'),
            instruction=Param('string', required=True, description='What the group agent should do and report'))
    async def create(self, ctx: dict[str, Any], name: str, source: dict[str, Any], instruction: str) -> str:
        service, group = _service(ctx)
        return _render(await service.create(group, name, source, instruction, reply=ctx.get('_reply_reference')))

    @action('list', description='List only this group/workspace observers; does not create an agent turn')
    async def list(self, ctx: dict[str, Any]) -> str:
        service, group = _service(ctx)
        return _render(await service.list(group))

    @action('status', description='Inspect receipt, execution and channel delivery independently',
            observer_id=Param('string', required=True, description='Observer ID in this group'))
    async def status(self, ctx: dict[str, Any], observer_id: str) -> str:
        service, group = _service(ctx)
        return _render(await service.status(group, observer_id))

    @action('cancel', description='Stop future collection; leave accepted input and report delivery unchanged',
            observer_id=Param('string', required=True, description='Observer ID in this group'))
    async def cancel(self, ctx: dict[str, Any], observer_id: str) -> str:
        service, group = _service(ctx)
        return _render(await service.cancel(group, observer_id))

    @action('cancel_notifications', description='Stop unsent reports, not observation or execution',
            observer_id=Param('string', required=True, description='Observer ID in this group'))
    async def cancel_notifications(self, ctx: dict[str, Any], observer_id: str) -> str:
        service, group = _service(ctx)
        return _render(await service.cancel_notifications(group, observer_id))


def create_eyes_tool() -> AgentTool:
    return EyesTool().build()
