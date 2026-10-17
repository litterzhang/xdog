"""ACP group addressing and durable intake; execution stays in Orchestrator."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

try:
    from acp import PromptResponse, RequestError
    from acp.schema import AgentMessageChunk, TextContentBlock
except ImportError as exc:
    raise ImportError("Claw ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.contracts import JsonValue, Peer, SessionRef, UpdateSink, finite_json, invalid
from xdog.agent.async_work import wait_owned
from xdog.agent.session_input import SessionInput, decode_input, render_input
from xdog.claw.acp.inbox import GroupInbox, InboxRecord
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import ExternalInput

logger = logging.getLogger(__name__)
CompletionSink = Callable[[InboxRecord], Awaitable[None]]


class ClawBackend:
    extensions = {"enqueue": 1, "inbox_status": 1, "session_info": 1}

    def __init__(self, orchestrator: Orchestrator, inbox: GroupInbox,
                 on_complete: CompletionSink | None = None) -> None:
        self.orchestrator, self.inbox, self.on_complete = orchestrator, inbox, on_complete
        self.generation = uuid.uuid4().hex
        self._bindings: dict[tuple[str, str], SessionRef] = {}
        self._closed_peers: set[str] = set()
        self._prompts: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._cancelled_requests: set[tuple[str, str]] = set()
        self._waiters: dict[tuple[str, str], asyncio.Future[None]] = {}
        self._signals: dict[str, asyncio.Event] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._faults: dict[str, str] = {}
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, orchestrator: Orchestrator, path: Path,
                   *, on_complete: CompletionSink | None = None) -> ClawBackend:
        inbox = await GroupInbox.open(path)
        backend = cls(orchestrator, inbox, on_complete)
        for group in orchestrator.get_group_ids():
            backend._wake(group)
        return backend

    def _active(self, peer: Peer) -> None:
        if self._closing or peer.id in self._closed_peers:
            raise invalid("ACP ingress/peer is closed")

    def _target(self, session_id: object, cwd: object | None = None) -> tuple[str, Path]:
        if not isinstance(session_id, str) or not session_id.startswith("group:"):
            raise ValueError("Claw ACP requires an explicit group:<id> address")
        group = session_id.removeprefix("group:")
        workspace = self.orchestrator.get_group_workspace(group)
        if workspace is None:
            raise ValueError("Unknown Claw group")
        if cwd is not None:
            if not isinstance(cwd, (str, Path)):
                raise ValueError("Expected absolute workspace path")
            path = Path(cwd)
            if not path.is_absolute() or path.resolve() != workspace:
                raise ValueError("Group workspace mismatch")
        return group, workspace

    def _bound(self, ref: SessionRef, peer: Peer) -> tuple[str, Path]:
        self._active(peer)
        if self._bindings.get((peer.id, ref.id)) != ref:
            raise invalid("Unknown group binding")
        try:
            return self._target(ref.id, ref.cwd)
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef:
        raise invalid("Claw ACP never creates groups; load an explicit group:<id>")

    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef:
        self._active(peer)
        try:
            self._target(session_id, cwd)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        ref = SessionRef(session_id, cwd.resolve(), self.generation)
        self._bindings[peer.id, session_id] = ref
        return ref

    def _wake(self, group: str) -> None:
        if self._closing:
            return
        signal = self._signals.setdefault(group, asyncio.Event())
        signal.set()
        if group not in self._workers:
            self._workers[group] = asyncio.create_task(self._consume(group), name=f"claw-acp:{group}")

    async def _enqueue(self, group: str, cwd: Path, message: SessionInput) -> tuple[InboxRecord, bool]:
        if group in self._faults:
            raise RequestError(-32000, "Group inbox consumer unavailable; inspect receiver logs")
        try:
            return await self.inbox.enqueue(group, cwd, message)
        finally:
            # Cancellation may have lost the ACK after the disk transaction committed.
            self._wake(group)

    async def _consume(self, group: str) -> None:
        signal = self._signals[group]
        try:
            while not self._closing:
                signal.clear()
                cwd = self.orchestrator.get_group_workspace(group)
                if cwd is None:
                    return
                while not self._closing and (record := await self.inbox.claim(group, cwd)) is not None:
                    try:
                        content = render_input(record.message)
                        message = ExternalInput(group_id=group, content=content, sender="acp", channel="acp",
                                                metadata={"input_origin": "external"}, canonical=record.message)
                        # Receipt-owning callbacks must never be steered/folded into
                        # another turn or dropped by the fire-and-forget collect queue.
                        result = await self.orchestrator.route_message(message, reserved=True)
                        error = result.error if result is not None else "Group did not execute the message"
                        record = await self.inbox.finish(record, "failed" if error else "succeeded",
                                                         response=result.response_text if result else "",
                                                         error=error or "",
                                                         session_id=result.session_id if result else "")
                    except asyncio.CancelledError:
                        # The cancellation-safe disk worker may already have
                        # committed a terminal result before reporting cancellation.
                        current = await self.inbox.get(group, cwd, record.message.id)
                        if current is not None and current.status == "running":
                            await self.inbox.finish(current, "interrupted",
                                                    error="Receiver stopped; inspect side effects")
                        raise
                    except Exception as exc:
                        record = await self.inbox.finish(record, "failed", error=str(exc))
                    self._notify(record)
                    if self.on_complete is not None:
                        try:
                            async with asyncio.timeout(5):
                                await self.on_complete(record)
                        except Exception:
                            logger.exception("Claw ACP completion display failed")
                await signal.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._faults[group] = str(exc)
            logger.exception("Claw ACP group consumer stopped: %s", group)
        finally:
            for (target, _), future in self._waiters.items():
                if target == group and not future.done():
                    future.set_result(None)

    def _notify(self, record: InboxRecord) -> None:
        future = self._waiters.get((record.group_id, record.message.id))
        if future is not None and not future.done():
            future.set_result(None)

    async def prompt(self, ref: SessionRef, peer: Peer, text: str,
                     metadata: dict[str, JsonValue], emit: UpdateSink) -> PromptResponse:
        group, cwd = self._bound(ref, peer)
        key = peer.id, ref.id
        if key in self._prompts:
            raise invalid("A prompt is already pending for this peer/group")
        task = asyncio.current_task()
        assert task is not None
        self._prompts[key] = task
        identifier = uuid.uuid4().hex
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters[group, identifier] = future
        try:
            details = metadata.get("xdog", {})
            if not isinstance(details, dict):
                raise ValueError("Invalid external input metadata")
            message = decode_input({"id": identifier, "origin": "acp", "source": details.get("source", "ACP"),
                                    "instruction": text, "evidence": details.get("evidence", []),
                                    "provenance": details.get("provenance", {})})
            await self._enqueue(group, cwd, message)
            await asyncio.shield(future)
            record = await self.inbox.get(group, cwd, identifier)
            if record is None:
                raise RequestError(-32000, "Accepted input is unavailable; inspect inbox before retry")
            if record.status != "succeeded":
                raise RequestError(-32000, record.error or "Group consumer stopped; inspect inbox status")
            self._active(peer)
            # Bound the serialized JSON notification, including escaping, rather
            # than assuming a one-MiB text value fits a one-MiB protocol frame.
            for offset in range(0, len(record.response), 16_384):
                await emit(AgentMessageChunk(session_update="agent_message_chunk", content=TextContentBlock(
                    type="text", text=record.response[offset:offset + 16_384])))
            return PromptResponse.model_validate({"stopReason": "end_turn", "_meta": {"xdog": {
                "message_id": identifier, "execution_session_id": record.session_id, "status": record.status}}})
        except asyncio.CancelledError:
            if key in self._cancelled_requests and not self._closing and peer.id not in self._closed_peers:
                return PromptResponse.model_validate({"stopReason": "cancelled", "_meta": {"xdog": {
                    "message_id": identifier, "execution_continues": True}}})
            raise
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        finally:
            self._cancelled_requests.discard(key)
            self._prompts.pop(key, None)
            self._waiters.pop((group, identifier), None)
            if not future.done():
                future.cancel()

    async def cancel(self, ref: SessionRef, peer: Peer) -> None:
        self._bound(ref, peer)
        task = self._prompts.get((peer.id, ref.id))
        if task is not None:
            self._cancelled_requests.add((peer.id, ref.id))
            task.cancel()  # Detach waiter only; accepted durable work is not retracted.

    async def disconnect(self, peer: Peer) -> None:
        self._closed_peers.add(peer.id)
        tasks = [task for (owner, _), task in self._prompts.items() if owner == peer.id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in tuple(self._bindings):
            if key[0] == peer.id:
                del self._bindings[key]

    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._active(peer)
        try:
            finite_json(params)
            if method == "xdog/session_info":
                if set(params) != {"session_id"}:
                    raise ValueError("Expected session_id")
                ref = self._bindings.get((peer.id, params["session_id"]))
                if ref is None:
                    raise ValueError("Unknown bound group")
                self._bound(ref, peer)
                return {"session_id": ref.id, "cwd": str(ref.cwd), "generation": ref.generation}
            if method not in {"xdog/enqueue", "xdog/inbox_status"}:
                raise RequestError(-32601, "Unsupported Claw ACP extension")
            group, cwd = self._target(params.get("session_id"), params.get("cwd", ""))
            if method == "xdog/enqueue":
                fields = {"session_id", "cwd", "message_id", "instruction", "source", "evidence", "provenance"}
                if set(params) != fields:
                    raise ValueError("Invalid enqueue fields")
                message = decode_input({"id": params["message_id"], "origin": "acp", "source": params["source"],
                                        "instruction": params["instruction"], "evidence": params["evidence"],
                                        "provenance": params["provenance"]})
                record, duplicate = await self._enqueue(group, cwd, message)
                return {"session_id": params["session_id"], "message_id": message.id, "branch_id": "",
                        "status": "duplicate" if duplicate else "queued", "message_state": record.status}
            if set(params) - {"session_id", "cwd", "message_id", "cursor"}:
                raise ValueError("Invalid inbox status fields")
            selected, cursor = params.get("message_id"), params.get("cursor", 0)
            if (type(cursor) is not int or not 0 <= cursor <= 4096
                    or selected is not None and (not isinstance(selected, str) or "cursor" in params)):
                raise ValueError("Invalid inbox status selection/cursor")
            namespace = await self.inbox.scope(group, cwd)
            messages, next_cursor = await self.inbox.status(group, cwd, selected=selected, cursor=cursor)
            return {"session_id": params["session_id"], "cwd": str(cwd), "branch_id": "", "inbox_id": namespace,
                    "messages": messages, "next_cursor": next_cursor}
        except (ValueError, TypeError, UnicodeError) as exc:
            raise invalid(str(exc)) from exc

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        prompts = list(self._prompts.values())
        workers = list(self._workers.values())
        for task in (*prompts, *workers):
            task.cancel()
        await asyncio.gather(*prompts, *workers, return_exceptions=True)
        await self.inbox.close()
