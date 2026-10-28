"""Owner-bound access controls; no application-wide runtime access."""
from __future__ import annotations

from typing import Any

from xdog.agent.async_work import run_owned
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController


async def manage_access(runtime: GroupRuntime, controller: GroupController,
                        action: str, expected_session: str) -> dict[str, Any]:
    from xdog.agent.access import AccessState
    if (runtime.get_new_session_id() or "") != expected_session:
        raise ValueError("Access session changed; refresh before retrying")
    if action == "status":
        if runtime._active_session is not None:
            state = runtime._active_session.access.state
        elif expected_session:
            generation = runtime._source_revision
            raw = await run_owned(runtime.transcript_store.get_access, expected_session)
            if (runtime._source_revision != generation
                    or (runtime.get_new_session_id() or '') != expected_session):
                raise ValueError('Access status owner changed; refresh the current group')
            state = AccessState.from_dict(raw)
        else:
            state = AccessState()
    elif action in {"restrict", "release"}:
        async with controller.operation(
            validate=lambda: runtime.require_current(expected_session)):
            if (runtime.get_new_session_id() or "") != expected_session:
                raise ValueError("Access session changed; refresh before retrying")
            session = await runtime.prepare_session(allow_reset=False)
            session._children_idle()
            state = await run_owned(lambda: session.access.set_read_only(action == "restrict"))
            runtime._source_revision += 1
    else:
        raise ValueError("Unknown access action")
    return {"session_id": runtime.get_new_session_id() or "", "access": state.to_dict()}
