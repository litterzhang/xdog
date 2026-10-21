"""Session-bound public execution/input ports for installed capabilities."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.inbox_models import InboxMessage, message_data
from xdog.coding.core.sessions.inbox_store import InboxStore, _read
from xdog.coding.core.sessions.store import InputStore

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


logger = logging.getLogger(__name__)


class SessionCapabilityHost:
    scope = 'session'
    owner_prefix = 'coding'

    def __init__(self, session: AgentSession, inbox: InboxStore, namespace: str) -> None:
        self._session, self._inbox = session, inbox
        self._children = session.children
        self._child_runtime = self._children.runtime
        self.identity = session.session_id
        self.branch = session.session_data.active_branch
        self.workspace = session.working_dir
        self.storage_root = session.session_manager._dir.parent
        self.namespace = namespace
        self._loop = asyncio.get_running_loop()
        self._controller = session.runtime.controller if session.runtime is not None else session._sdk_controller
        self._generation = self._controller.generation if self._controller is not None else uuid.uuid4().hex
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._remove_events: Callable[[], None] | None = None
        self._remove_children: Callable[[], None] | None = None
        self._retiring = False

    @classmethod
    async def open(cls, session: AgentSession) -> SessionCapabilityHost:
        inbox = InboxStore(session.session_manager._dir, session.session_manager._dir.parent / 'inbox')
        namespace = await run_owned(inbox.scope_id, session.session_id, session.working_dir)
        return cls(session, inbox, namespace)

    def valid(self) -> bool:
        current = self._session
        return (current.children is self._children and current.session_id == self.identity
                and current.session_data.active_branch == self.branch and current.working_dir == self.workspace)

    def accepting(self) -> bool:
        return self.valid()

    def owns_parent(self, parent: ParentPort) -> bool:
        session = self._session
        return (self.valid() and parent.agent is session.agent and parent.extensions is session.extensions
                and parent.children is self._children.capabilities)

    async def prepare_parent(self) -> ParentPort:
        if not self.valid():
            raise ValueError('Capability session owner changed')
        return self.parent()

    def parent(self) -> ParentPort:
        session = self._session
        extensions = session.extensions
        return ParentPort(session.agent, extensions, self._children.capabilities,
            valid=lambda: self.valid() and not self._retiring and not session._sdk_disposing
                and session.extensions is extensions,
            user_authorized=lambda: session._user_operation_authorized)

    def _message(self, message: SessionInput) -> InboxMessage:
        return InboxMessage(self.identity, self.workspace, message.id, message.instruction, message.evidence,
                            message.source, message.provenance)

    async def enqueue(self, message: SessionInput) -> str:
        if not self.valid():
            raise ValueError('Capability session owner changed')
        value = await run_owned(self._inbox.enqueue, self._message(message), expected_branch_id=self.branch)
        if not self.valid():
            raise ValueError('Capability session owner changed during input commit')
        return 'duplicate' if value.duplicate else 'accepted'

    async def receipt(self, message: SessionInput) -> str | None:
        if not self.valid():
            raise ValueError('Capability session owner changed')
        rows = await run_owned(self._inbox.records, self.identity, self.workspace)
        if not self.valid():
            raise ValueError('Capability session owner changed during input read')
        found = next((row for row in rows if row.message.message_id == message.id), None)
        if found is None:
            return None
        if found.branch_id != self.branch or message_data(found.message) != message_data(self._message(message)):
            raise ValueError('Capability input receipt owner/content conflict')
        return 'accepted'

    def _on_owner(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError('Capability execution facts require the original owner loop')

    def execution_snapshot(self) -> dict[str, Any]:
        self._on_owner()
        controller = self._controller
        snapshot = (controller.execution_snapshot() if controller is not None else
                    {'state': 'unavailable', 'user_pending': False, 'children_active': False,
                     'generation': self._generation})
        if self._retiring or self._session._sdk_disposing:
            snapshot['state'] = 'closing'
        elif not self.valid() or controller is None or controller.generation != self._generation:
            snapshot['state'] = 'unavailable'
        snapshot['generation'] = self._generation
        return snapshot

    async def latest_execution(self) -> dict[str, Any] | None:
        """Read original-owner durable execution facts through the Core store.

        Legacy records return explicit unavailable; no time/order is backfilled.
        A running fact has started_at but no settled_at and never authorizes replay.
        """
        def check(*, admitting: bool = False) -> bool:
            self._on_owner()
            controller = self._controller
            current = (self._session.runtime.controller if self._session.runtime is not None
                       else self._session._sdk_controller)
            if (not self.valid() or current is not controller
                    or controller is not None and controller.generation != self._generation):
                raise ValueError('Capability execution owner changed')
            if self._retiring or self._session._sdk_disposing or controller is not None and controller._closing:
                if admitting:
                    return False
                raise RuntimeError('Capability execution owner is closing')
            return True

        if not check(admitting=True):
            return None
        def read() -> dict[str, Any] | None:
            _, branch = self._inbox.identity(self.identity, self.workspace)
            if branch != self.branch:
                raise ValueError('Capability execution branch changed')
            # Unlike scope_id(), this cannot create a replacement namespace.
            namespace = _read(self._inbox.root / '.store-id', 4096)
            if (not isinstance(namespace, dict) or set(namespace) != {'version', 'id'}
                    or type(namespace['version']) is not int or namespace['version'] != 1
                    or not isinstance(namespace['id'], str)
                    or uuid.UUID(hex=namespace['id']).hex != namespace['id']):
                raise ValueError('Invalid durable inbox namespace')
            key = hashlib.sha256((namespace['id'] + '\0' + self.identity).encode()).hexdigest()
            if key != self.namespace:
                raise ValueError('Capability input owner namespace changed')
            path = self._inbox.sessions_dir / f'session-{self.identity}.inputs.json'
            return InputStore.latest_execution(path, self.branch)

        result = await run_owned(read)
        check()
        return result

    def _event(self, event: dict[str, Any]) -> None:
        if event['generation'] != self._generation:
            return
        value = {**event, 'snapshot': self.execution_snapshot()}
        for callback in tuple(self._listeners):
            try:
                callback(copy.deepcopy(value))
            except Exception:
                logger.warning('Capability execution listener failed', exc_info=True)

    def _child_event(self, event: dict[str, Any]) -> None:
        # This subscription belongs to the actual captured child runtime, not its
        # replaceable UI publisher. Payload/status labels confer no authority.
        if event.get('type') != 'child_status' or self._child_runtime.active:
            return
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._children_available)

    def _children_available(self) -> None:
        self._on_owner()
        current = (self._session.runtime.controller if self._session.runtime is not None
                   else self._session._sdk_controller)
        if (not self._listeners or self._retiring or self._session._sdk_disposing or not self.valid()
                or current is not self._controller or current is None or current._closing
                or current.generation != self._generation or self._children.runtime is not self._child_runtime
                or self._children.capabilities.runtime is not self._child_runtime or self._child_runtime.active):
            return
        # Merely prompt subscribers to re-read the original owner's current
        # snapshot. A child completing is never a main execution settlement.
        self._event({'type': 'owner.available', 'execution_id': '', 'status': '',
                     'generation': self._generation})

    def subscribe_events(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._on_owner()
        self._listeners.append(callback)
        if self._remove_events is None and self._controller is not None:
            self._remove_events = self._controller.subscribe_execution(self._event)
        if self._remove_children is None and not self._retiring:
            try:
                self._remove_children = self._children.capabilities.subscribe(self._child_event)
            except BaseException:
                self._listeners.remove(callback)
                if not self._listeners and self._remove_events is not None:
                    self._remove_events()
                    self._remove_events = None
                raise
        self._event({'type': 'owner.retiring' if self._retiring else 'owner.available',
                     'execution_id': '', 'status': '', 'generation': self._generation})
        def cancel() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)
            if not self._listeners and self._remove_events is not None:
                self._remove_events()
                self._remove_events = None
            if not self._listeners and self._remove_children is not None:
                self._remove_children()
                self._remove_children = None
        return cancel

    def subscribe_completed(self, callback: Callable[[], None]) -> Callable[[], None]:
        return self.subscribe_events(lambda event: callback() if event['type'] == 'execution.settled' else None)

    def retire(self) -> None:
        if self._retiring:
            return
        self._retiring = True
        def publish() -> None:
            self._event({'type': 'owner.retiring', 'execution_id': '', 'status': '', 'generation': self._generation})
            if self._remove_children is not None:
                self._remove_children()
                self._remove_children = None
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(publish)

    async def acp_target(self) -> dict[str, Any] | None:
        self._on_owner()
        runtime = self._session.runtime
        if (not self.valid() or self._retiring or self._session._sdk_disposing or runtime is None
                or runtime.disposing or not runtime._started
                or runtime.controller is not self._controller or runtime.controller.generation != self._generation
                or runtime.controller._closing):
            return None  # A plain SDK owner has no receiver; never construct a second one.
        await runtime.open_endpoint()
        server = runtime.server
        if (not self.valid() or self._retiring or runtime.disposing or runtime.controller._closing
                or runtime.controller.generation != self._generation or runtime.inbox.branch != self.branch
                or server is None or server.closing or server.server is None):
            return None
        namespace = await run_owned(runtime.inbox.store.scope_id, self.identity, self.workspace)
        if namespace != self.namespace or not self.valid() or runtime.controller.generation != self._generation:
            return None
        return {'endpoint': str(runtime.record.address), 'session_id': self.identity, 'cwd': str(self.workspace),
                'inbox_id': namespace, 'branch_id': self.branch}

    async def child_record(self, session: str, branch: str, identifier: str) -> dict[str, Any] | None:
        if session != self.identity or branch != (self.branch or 'head'):
            raise ValueError('Child execution belongs to another session/branch')
        return next((row for row in await run_owned(self._children.store.load) if row['id'] == identifier), None)
