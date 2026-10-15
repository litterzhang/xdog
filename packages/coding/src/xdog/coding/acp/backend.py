"""Translate ACP requests into owned coding session submissions, never UI calls."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from acp import PromptResponse, RequestError
from acp.schema import AgentMessageChunk, TextContentBlock, UserMessageChunk
from xdog.agent.acp.contracts import JsonValue, Peer, SessionRef, UpdateSink, invalid
from xdog.agent.acp.events import EventProjector
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.coding.acp.client import AcpAttachment
from xdog.coding.acp.ingress_backend import IngressBackend
from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.models import SessionEvent, Submission
from xdog.coding.core.sessions.ownership import OwnerRegistry, runtime_directory
from xdog.coding.core.sessions.router import SessionRouter
from xdog.coding.core.sessions.runtime import SessionRuntime

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class CodingBackend:
    extensions = {"session_info": 1, "resume": 1, "enqueue": 1, "inbox_status": 1}

    def __init__(
        self,
        router: SessionRouter | None = None,
        registry: OwnerRegistry | None = None,
        *,
        fixed_session: str | None = None,
        factory: Callable[[Path], AgentSession] | None = None,
        inbox_store: InboxStore | None = None,
    ) -> None:
        from xdog.coding.config import get_sessions_dir

        self.router = router or SessionRouter()
        self.registry = registry or OwnerRegistry(runtime_directory(get_sessions_dir()))
        self.fixed_session, self.factory = fixed_session, factory
        from xdog.coding.config import get_inbox_dir
        self._inbox = IngressBackend(inbox_store or InboxStore(get_sessions_dir(), get_inbox_dir()), self.registry)
        self._owned: dict[str, tuple[str, SessionRuntime, Any]] = {}
        self._remote: dict[tuple[str, str], AcpAttachment] = {}
        self._refs: dict[tuple[str, str], SessionRef] = {}
        self._running: dict[tuple[str, str], tuple[SessionController, Submission]] = {}
        self._remote_busy: set[tuple[str, str]] = set()

    def _target(self, session_id: str) -> None:
        if self.fixed_session is not None and session_id != self.fixed_session:
            raise invalid("ACP bridge has a fixed target session")

    def _local(self, ref: SessionRef) -> SessionController:
        self._target(ref.id)
        controller = self.router.resolve(ref.id, ref.cwd)
        if controller.generation != ref.generation:
            raise invalid("Session owner generation is stale")
        return controller

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef:
        if self.fixed_session is not None:
            raise invalid("Cannot create a session through a fixed target bridge")

        def construct() -> AgentSession:
            if self.factory is not None:
                return self.factory(cwd)
            from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

            return create_agent_session(CreateSessionOptions(working_dir=cwd)).session

        task = asyncio.create_task(asyncio.to_thread(construct))
        try:
            session = await asyncio.shield(task)
        except asyncio.CancelledError:
            session = await task
            session.dispose()
            raise
        runtime = SessionRuntime(session, bound=False, router=self.router)
        runtime.start()
        try:
            from xdog.coding.acp.server import serve_local

            server = await serve_local(runtime)
        except BaseException:
            await asyncio.wrap_future(runtime.close())
            raise
        self._owned[session.session_id] = peer.id, runtime, server
        ref = SessionRef(session.session_id, session.working_dir, runtime.controller.generation)
        self._refs[peer.id, ref.id] = ref
        return ref

    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef:
        self._target(session_id)
        try:
            controller = self.router.resolve(session_id, cwd)
        except RuntimeError as exc:
            if "workspace" in str(exc) or "stale" in str(exc):
                raise invalid(str(exc)) from exc
            record = self.registry.lookup(session_id)
            if record.cwd.resolve() != cwd:
                raise invalid("Session workspace mismatch")
            attachment = AcpAttachment(record)
            await attachment.connect(emit)
            previous = self._remote.pop((peer.id, session_id), None)
            if previous is not None:
                await previous.close()
            self._remote[peer.id, session_id] = attachment
            ref = SessionRef(session_id, cwd, record.generation)
        else:
            ref = SessionRef(session_id, cwd, controller.generation)
            # Explicit load replays to this peer only; never back into the session event bus.
            for message in controller.session.messages:
                if isinstance(message, (AssistantMessage, UserMessage)):
                    content = message.content
                    text = (
                        content
                        if isinstance(content, str)
                        else "".join(part.text for part in content if isinstance(part, TextContent))
                    )
                    if text:
                        kind = "agent_message_chunk" if isinstance(message, AssistantMessage) else "user_message_chunk"
                        cls = AgentMessageChunk if isinstance(message, AssistantMessage) else UserMessageChunk
                        await emit(
                            cls.model_validate(
                                {"sessionUpdate": kind, "content": TextContentBlock(type="text", text=text)}
                            )
                        )
        self._refs[peer.id, session_id] = ref
        return ref

    async def prompt(
        self, ref: SessionRef, peer: Peer, text: str, metadata: dict[str, JsonValue], emit: UpdateSink
    ) -> PromptResponse:
        key = peer.id, ref.id
        if key in self._running or key in self._remote_busy:
            raise invalid("A prompt is already pending on this connection/session")
        remote = self._remote.get(key)
        if remote is not None:
            self._remote_busy.add(key)
            remote.sink = emit
            try:
                return PromptResponse.model_validate(
                    await remote.conn.prompt(
                        session_id=ref.id, prompt=[TextContentBlock(type="text", text=text)], **metadata
                    )
                )
            finally:
                self._remote_busy.discard(key)
                remote.sink = None
        controller = self._local(ref)
        details = metadata.get("xdog", {})
        if not isinstance(details, dict):
            raise invalid("Expected xdog metadata object")
        if set(details) - {"source", "evidence", "provenance"}:
            raise invalid("Unknown xdog metadata fields; permissions and origin cannot be overridden")
        source = details.get("source", "ACP client")
        evidence, provenance = details.get("evidence", []), details.get("provenance", {})
        if not isinstance(source, str) or not isinstance(evidence, list) or not isinstance(provenance, dict):
            raise invalid("Invalid external input metadata")
        value = SessionInput(uuid.uuid4().hex, "acp", source, text, tuple(evidence), provenance)
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()
        failure: asyncio.Future[BaseException] = loop.create_future()
        buffered = 0
        projector = EventProjector()

        def receive(event: SessionEvent) -> None:
            if event.request_id != value.id or event.kind != "agent_event":
                return
            updates = projector.project(event.data)  # type: ignore[arg-type]
            for update in updates:

                def enqueue(item: Any = update) -> None:
                    nonlocal buffered
                    size = len(item.model_dump_json().encode())
                    if queue.full() or buffered + size > 1024 * 1024:
                        if not failure.done():
                            failure.set_result(RuntimeError("ACP output exceeds capacity"))
                    else:
                        buffered += size
                        queue.put_nowait((item, size))

                loop.call_soon_threadsafe(enqueue)

        async def drain() -> None:
            nonlocal buffered
            try:
                while True:
                    item, size = await queue.get()
                    try:
                        await emit(item)
                    finally:
                        buffered -= size
                        queue.task_done()
            except Exception as exc:
                if not failure.done():
                    failure.set_result(exc)

        async def checked(future: asyncio.Future[Any]) -> Any:
            await asyncio.wait({future, failure}, return_when=asyncio.FIRST_COMPLETED)
            if failure.done():
                future.cancel()
                await asyncio.gather(future, return_exceptions=True)
                raise failure.result()
            return await future

        unsubscribe = controller.subscribe(receive)
        ticket = controller.submit(value, peer_id=peer.id)
        self._running[key] = controller, ticket
        consumer = asyncio.create_task(drain())
        try:
            await checked(asyncio.wrap_future(ticket.accepted))
            result = await checked(asyncio.wrap_future(ticket.result))
            await checked(asyncio.create_task(queue.join()))
            if result.status == "failed":
                raise RequestError(-32000, result.error or "Session task failed")
            return PromptResponse.model_validate(
                {
                    "stopReason": "end_turn" if result.status == "succeeded" else "cancelled",
                    "_meta": {"xdog": {"request_id": ticket.id, "status": result.status}},
                }
            )
        finally:
            unsubscribe()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            self._running.pop(key, None)
            await asyncio.wrap_future(controller.cancel(ticket.id, peer_id=peer.id))

    async def cancel(self, ref: SessionRef, peer: Peer) -> None:
        remote = self._remote.get((peer.id, ref.id))
        if remote is not None:
            await remote.conn.cancel(session_id=ref.id)
            return
        running = self._running.get((peer.id, ref.id))
        if running is not None:
            await asyncio.wrap_future(running[0].cancel(running[1].id, peer_id=peer.id))

    async def disconnect(self, peer: Peer) -> None:
        await self._inbox.disconnect(peer)
        controllers = {row[0] for key, row in self._running.items() if key[0] == peer.id}
        for controller in controllers:
            await asyncio.wrap_future(controller.disconnect(peer.id))
        for key, remote in tuple(self._remote.items()):
            if key[0] == peer.id:
                await remote.close()
                del self._remote[key]
        for identifier, (owner, runtime, server) in tuple(self._owned.items()):
            if owner == peer.id:
                await server.close()
                await asyncio.wrap_future(runtime.close())
                del self._owned[identifier]
        for key in tuple(self._refs):
            if key[0] == peer.id:
                del self._refs[key]

    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in {'xdog/enqueue', 'xdog/inbox_status'}:
            target = params.get('session_id')
            if not isinstance(target, str):
                raise invalid('Expected explicit session_id')
            self._target(target)
            return await self._inbox.extension(peer, method, params)
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or set(params) != {"session_id"}:
            raise invalid("Expected a bound session_id")
        ref = self._refs.get((peer.id, session_id))
        if ref is None:
            raise invalid("Unknown bound session")
        remote = self._remote.get((peer.id, session_id))
        if remote is not None:
            return cast(dict[str, Any], await remote.conn.ext_method(method, params))
        controller = self._local(ref)
        if method == "xdog/session_info":
            return {
                "session_id": session_id,
                "cwd": str(ref.cwd),
                "generation": ref.generation,
                "branch_id": controller.branch,
            }
        if method == "xdog/resume":
            return {"resumed": await asyncio.wrap_future(controller.resume(peer.id))}
        raise RequestError(-32601, "Unsupported extension")
