"""Native model/access request handlers; execution remains application-owned."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.claw.core.runtime.orchestrator import Orchestrator


async def reasoning_control(application: Orchestrator | None, request: dict[str, Any],
                            write: Callable[[dict[str, Any]], Awaitable[None]],
                            jobs: set[asyncio.Task[None]], *, reasoning_supported: bool = False) -> None:
    rid, group, session = (request.get(key, "") for key in ("request_id", "group_id", "session_id"))
    level = request.get("thinking")
    reasoning_response = {"type": "reasoning_result", "request_id": rid, "group_id": group,
                          "success": False}
    reserved = None
    try:
        if (not reasoning_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                or not isinstance(group, str) or not isinstance(session, str)
                or level is not None and (not isinstance(level, str) or len(level) > 16)
                or application is None or len(jobs) >= 8):
            raise ValueError("Updated gateway and valid reasoning control required")
        reserved = application.try_reserve_group(group)
        if not reserved:
            raise ValueError("Reasoning controls require an idle owner")
    except (ValueError, RuntimeError):
        if reserved and application is not None:
            application.release_group_reservation(reserved)
        reasoning_response["error"] = ("Reasoning controls require an updated gateway "
                                       "and idle session owner")
        await write(reasoning_response)
        return
    async def reasoning_job(gid: str, sid: str, target: str | None, result: dict[str, Any]) -> None:
        try:
            assert application is not None
            result.update(success=True, **await application.reasoning(gid, sid, target))
        except (ValueError, RuntimeError, OSError) as exc:
            result["error"] = str(exc) or "Reasoning owner changed; no fallback/retry"
        await write(result)
    job = application.start_group_operation(reserved, reasoning_job(group, session, level, reasoning_response))
    jobs.add(job)
    job.add_done_callback(jobs.discard)
    return


async def extension_model_control(application: Orchestrator | None, request: dict[str, Any],
                                  write: Callable[[dict[str, Any]], Awaitable[None]],
                                  jobs: set[asyncio.Task[None]]) -> None:
    rid, group, session, selected = (request.get(key, "") for key in
                                      ("request_id", "group_id", "session_id", "model"))
    result = {"type": "extension_model_result", "request_id": rid, "group_id": group,
              "success": False}
    reserved = None
    try:
        if (not isinstance(rid, str) or not 0 < len(rid) <= 128
                or not isinstance(group, str) or not isinstance(session, str)
                or not isinstance(selected, str) or len(selected) > 256 or application is None
                or len(jobs) >= 8):
            raise ValueError("Invalid extension model control")
        reserved = application.try_reserve_group(group)
        if not reserved:
            raise ValueError("Extension model selection/catalog requires an idle group")
    except (ValueError, RuntimeError, OSError):
        result['error'] = 'Extension model control failed; explicit active model/idle owner required'
        await write(result)
        return
    async def model_job(gid: str, sid: str, target: str, response: dict[str, Any]) -> None:
        try:
            assert application is not None
            response.update(success=True, **await application.extension_models(gid, sid, target))
        except (ValueError, RuntimeError, OSError):
            response['error'] = 'Model owner/catalog changed; no fallback'
        await write(response)
    job = application.start_group_operation(reserved, model_job(group, session, selected, result))
    jobs.add(job)
    job.add_done_callback(jobs.discard)
    return


async def access_control(application: Orchestrator | None, request: dict[str, Any],
                         write: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    identifier, group = request.get("request_id"), request.get("group_id")
    action, session = request.get("action"), request.get("session_id")
    access_reply = {"type": "access_control_result", "request_id": identifier,
             "group_id": group, "success": False}
    reserved = None
    try:
        if (set(request) != {"type", "request_id", "group_id", "action", "session_id"}
                or not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                or not isinstance(group, str) or not isinstance(session, str)
                or not isinstance(action, str) or action not in {"status", "restrict", "release"}
                or application is None):
            raise ValueError("Invalid access control")
        if action != "status":
            reserved = application.try_reserve_group(group)
            if not reserved:
                raise ValueError("Access changes require an idle group")
        async with application.operation_scope(reserved):
            result = await application.manage_access(group, action, session)
        access_reply.update(success=True, **result)
    except (ValueError, RuntimeError, OSError) as exc:
        access_reply["error"] = str(exc)
    finally:
        if reserved and application is not None:
            assert isinstance(group, str)
            application.release_group_reservation(reserved)
    await write(access_reply)
    return
