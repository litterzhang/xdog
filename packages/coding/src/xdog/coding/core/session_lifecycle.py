"""One Coding session close receipt and resource settlement, independent of UI."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import settle_cleanup, wait_owned

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.sessions.controller import SessionController


class _CloseReceipt(Future[None]):
    def cancel(self) -> bool:
        return False  # A detached waiter cannot revoke shared cleanup ownership.


def _publish(receipt: Future[None], completed: Future[Any] | asyncio.Future[Any]) -> None:
    try:
        completed.result()
    except BaseException as exc:
        receipt.set_exception(exc)
    else:
        receipt.set_result(None)


def settle_sync(*steps: Callable[[], object], message: str) -> None:
    """Synchronous finalizers have no asynchronous work left to schedule/join."""
    errors: list[BaseException] = []
    for step in steps:
        try:
            step()
        except BaseException as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(message, errors)


async def close_resources(session: AgentSession) -> None:
    with session._sdk_lock:
        receipt = session._resource_closing
        if receipt is None:
            receipt = _CloseReceipt()
            session._resource_closing = receipt
            async def close() -> None:
                async def stop_dialogs() -> None:
                    session.extension_dialogs.close()
                    session.permissions.deny_all()
                async def capability_publication() -> None:
                    if session._goal_start is not None:
                        await wait_owned(session._goal_start)
                async def goal() -> None:
                    if session.children.goal_host is not None:
                        await session.children.goal_host.aclose()
                async def mcp() -> None:
                    if session.mcp is not None:
                        await session.mcp.aclose()
                await settle_cleanup(stop_dialogs, capability_publication, session.agent.aclose,
                    session.children.supervisor.aclose, goal, session._settle_retired_goals,
                    session._settle_retired_resources, session.extensions.aclose, mcp,
                    message='Coding session resource cleanup failed')
            task = asyncio.create_task(close(), name='coding-session-resources-close')
            task.add_done_callback(lambda done: _publish(receipt, done))
    await wait_owned(asyncio.wrap_future(receipt))


async def close_input_owner(session: AgentSession, controller: SessionController, *, resources: bool) -> None:
    async def intake() -> None:
        if controller.inbox is not None:
            await controller.inbox.retire()
    async def owned_resources() -> None:
        if resources:
            await close_resources(session)
    async def retired_leases() -> None:
        session._release_retired_leases()
    await settle_cleanup(intake, owned_resources, retired_leases, message='Coding input owner cleanup failed')


def request_close(session: AgentSession, *, asynchronous: bool = False) -> Future[None]:
    """Fence once, settle the real owner, then finalize/release its actual leases."""
    with session._sdk_lock:
        if session._close_receipt is not None:
            return session._close_receipt
        runtime = session.runtime
        controller = runtime.controller if runtime is not None else session._sdk_controller
        if controller is not None and controller.owns_current_task:
            raise RuntimeError('Cannot close a session from its own operation; ask the host to close it')
        session._sdk_disposing = True
        receipt = _CloseReceipt()
        session._close_receipt = receipt
        previous = session._sdk_closing

    def finalize(completed: Future[None]) -> None:
        def finish() -> None:
            errors: list[BaseException] = []
            try:
                completed.result()
            except BaseException as exc:
                errors.append(exc)
            if controller is not None and controller._thread is not None:
                try:
                    controller._thread.join()
                except BaseException as exc:
                    errors.append(exc)
            try:
                session._dispose_resources()
            except BaseException as exc:
                errors.append(exc)
            if len(errors) == 1:
                receipt.set_exception(errors[0])
            elif errors:
                receipt.set_exception(BaseExceptionGroup('Coding session close failed', errors))
            else:
                receipt.set_result(None)
        if controller is not None and controller._thread is threading.current_thread():
            # The owner cannot join itself. This finite joiner runs no model or
            # event loop and retains the receipt/lease until that owner exits.
            threading.Thread(target=finish, name='coding-session-finalize').start()
        else:
            finish()

    if runtime is not None:
        runtime._close_owner().add_done_callback(finalize)
    elif controller is not None:
        controller.close().add_done_callback(finalize)
    else:
        async def cold_close() -> None:
            async def prior_owner() -> None:
                if previous is not None:
                    await wait_owned(asyncio.wrap_future(previous))
            async def finalize_cold() -> None:
                session._dispose_resources()
            await settle_cleanup(prior_owner, lambda: close_resources(session), finalize_cold,
                                 message='Coding cold session cleanup failed')
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            asynchronous = True  # Never block a caller loop that may own capability callbacks.
        if asynchronous:
            task = asyncio.create_task(cold_close(), name='coding-cold-session-close')
            task.add_done_callback(lambda done: _publish(receipt, done))
        else:
            # Preserve synchronous construction/disposal without creating a new
            # controller, reading a damaged inbox or abandoning an asyncio.run.
            from xdog.coding.core.session_staging import settle
            try:
                settle(cold_close)
            except BaseException as exc:
                receipt.set_exception(exc)
            else:
                receipt.set_result(None)
    return receipt
