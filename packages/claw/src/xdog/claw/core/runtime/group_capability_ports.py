"""Schema-neutral group execution and durable input ports for installed providers."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.child_store import ChildStore
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import ExternalInput

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.session import AgentSession


class GroupCapabilityHost:
    scope = 'group'
    owner_prefix = 'claw'
    branch = ''

    def __init__(self, runtime: GroupRuntime, inputs: InputService, namespace: str) -> None:
        self._runtime, self._inputs = runtime, inputs
        self.identity = runtime.group.id
        self.workspace = runtime.workspace_dir
        self.storage_root = runtime.data_dir / 'groups' / self.identity
        self.namespace = namespace
        self._scope = InputScope(self.identity, self.workspace)
        self._loop = asyncio.get_running_loop()
        self._generation = str(inputs.execution_snapshot(self._scope)["generation"])
        self._retiring = False
        self._children_binding: tuple[AgentSession, object, object, object] | None = None
        self._children_unsubscribe: Callable[[], None] | None = None

    @classmethod
    async def open(cls, runtime: GroupRuntime, inputs: InputService) -> GroupCapabilityHost:
        namespace = await inputs.store.namespace(InputScope(runtime.group.id, runtime.workspace_dir))
        return cls(runtime, inputs, namespace)

    def valid(self) -> bool:
        return (self._runtime.group.id == self.identity and self._runtime.workspace_dir == self.workspace)

    def accepting(self) -> bool:
        return self._inputs.accepting

    def parent(self, session: AgentSession) -> ParentPort:
        extensions = session.extensions
        parent = ParentPort(session.agent, session.extensions, session.children.capabilities,
            valid=lambda: self.valid() and not self._retiring and not self._runtime._closing
                and self._runtime._active_session is session
                and session.extensions is extensions,
            user_authorized=lambda: session._user_operation_authorized)
        self._watch_children(session, parent)
        return parent

    def _watch_children(self, session: AgentSession, parent: ParentPort) -> None:
        """Wake generic consumers after actual child settlement, not UI publication."""
        self._on_owner()
        children = session.children.runtime
        binding = (session, children, session.agent, session.extensions)
        previous = self._children_binding
        if (previous is not None and all(old is new for old, new in zip(previous, binding, strict=True))
                and parent.valid() and not children.closed):
            return  # Reattachment must not discard an already queued settlement hint.
        if self._children_unsubscribe is not None:
            self._children_unsubscribe()
            self._children_unsubscribe = None
        self._children_binding = binding
        if not parent.valid() or children.closed:
            return

        def available() -> None:
            self._on_owner()
            if (self._children_binding is not binding or not parent.valid() or not self.accepting()
                    or session.children.runtime is not children or session.agent is not parent.agent):
                return
            # Lifecycle payloads are hints only. Re-read original-owner facts
            # after cleanup/persistence; a terminal label alone is not idle.
            snapshot = self.execution_snapshot()
            if snapshot['state'] == 'idle' and not snapshot['children_active']:
                self._inputs.publish_available(self._scope)

        def changed(_event: dict[str, Any]) -> None:
            # Independent of children.publish (the replaceable UI sink). Always
            # dispatch to the pinned loop and recheck retirement after dispatch.
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(available)

        self._children_unsubscribe = children.subscribe(changed)

    def owns_parent(self, parent: ParentPort) -> bool:
        session = self._runtime._active_session
        return (self.valid() and session is not None and parent.agent is session.agent
                and parent.extensions is session.extensions and parent.children is session.children.capabilities)

    async def prepare_parent(self) -> ParentPort:
        session = await self._runtime.prepare_session(allow_reset=False)
        return self.parent(session)

    def _envelope(self, message: SessionInput) -> Any:
        value = ExternalInput(group_id=self.identity, content='', sender=message.source, channel='',
                              canonical=message, metadata={'input_origin': 'external'})
        return freeze_input(value, self.workspace, message_id=message.id)

    async def _check(self) -> None:
        if not self.valid() or await self._inputs.store.namespace(self._scope) != self.namespace:
            raise ValueError('Capability input owner namespace changed')

    async def enqueue(self, message: SessionInput) -> str:
        await self._check()
        _, duplicate = await self._inputs.submit(self._envelope(message),
            max_pending=self._runtime.group.config.max_queued_messages)
        return 'duplicate' if duplicate else 'accepted'

    async def receipt(self, message: SessionInput) -> str | None:
        await self._check()
        record = await self._inputs.store.get(self._scope, message.id)
        if record is None:
            return None
        if record.envelope != self._envelope(message):
            raise ValueError('Capability input receipt owner/content conflict')
        return 'accepted'

    def subscribe_completed(self, callback: Callable[[], None]) -> Callable[[], None]:
        def completed(record: Any) -> None:
            if record.envelope.scope == self._scope and self.accepting():
                callback()
        return self._inputs.subscribe_completed(completed)

    def _on_owner(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError('Capability execution facts require the original owner loop')

    def execution_snapshot(self) -> dict[str, Any]:
        self._on_owner()
        snapshot = self._inputs.execution_snapshot(self._scope)
        if self._retiring or self._runtime._closing:
            snapshot['state'] = 'closing'
        elif not self.valid() or snapshot['generation'] != self._generation:
            snapshot['state'] = 'unavailable'
        snapshot['generation'] = self._generation
        return snapshot

    async def latest_execution(self) -> dict[str, Any] | None:
        """Read original-group attempts, including earlier attempts of retried inputs.

        A scope admits one execution at a time, so attempt rowid is the durable
        start order. Intake sequence is NOT execution order after explicit retry.
        Times come only from actual Core commits, never from file mtimes.
        """
        def check(*, admitting: bool = False) -> bool:
            self._on_owner()
            if not self.valid() or self._inputs.execution_snapshot(self._scope)['generation'] != self._generation:
                raise ValueError('Capability execution owner changed')
            if self._retiring or self._runtime._closing or not self._inputs.accepting:
                if admitting:
                    return False
                raise RuntimeError('Capability execution owner is closing')
            return True

        if not check(admitting=True):
            return None
        # Core retains the existing lease/lock and cancellation-settled off-loop IO.
        result = await self._inputs.store.latest_execution(self._scope, namespace=self.namespace)
        check()
        return result

    def subscribe_events(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._on_owner()
        def event(value: dict[str, Any]) -> None:
            if value['generation'] == self._generation:
                callback({**value, 'snapshot': self.execution_snapshot()})
        return self._inputs.subscribe_events(self._scope, event)

    def retire(self) -> None:
        self._on_owner()
        if self._retiring:
            return
        self._retiring = True
        self._children_binding = None
        if self._children_unsubscribe is not None:
            self._children_unsubscribe()
            self._children_unsubscribe = None
        self._inputs._execution_event(self._scope, 'owner.retiring')

    async def acp_target(self) -> dict[str, Any] | None:
        self._on_owner()
        def available() -> bool:
            return self.valid() and self.accepting() and not self._retiring and not self._runtime._closing
        if not available():
            return None
        try:
            await self._check()
            if not available():
                return None
            target = await self._inputs.acp_target(self._scope)
        except RuntimeError:
            # Retirement may begin while namespace/endpoint IO is settling.
            # Genuine live-owner transport/storage failures remain visible.
            if not available():
                return None
            raise
        if not available() or target is None or target['inbox_id'] != self.namespace:
            return None
        return dict(target)

    async def child_record(self, session: str, branch: str, identifier: str) -> dict[str, Any] | None:
        def original() -> dict[str, Any] | None:
            store = self._runtime.transcript_store
            if not any(record['session_id'] == session for record in store.list_sessions(self.identity)):
                raise ValueError('Child execution belongs to another group or missing original session')
            children = ChildStore.scoped(store._sessions_dir, session, branch)
            return next((row for row in children.load() if row['id'] == identifier), None)
        return await run_owned(original)
