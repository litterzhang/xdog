"""Connection-owned parent prompt hook data; never automatic prompt/key replay."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from xdog.agent.extensions.terminal_input import event_decision
from xdog.coding.config import get_config_dir
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.sessions.operations import run_control
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.terminal_data import editable_data
from xdog.tui.prompt_keymap import PromptKeymap


class RpcTerminal:
    def __init__(self, session: AgentSession, navigation: RpcNavigation,
                 emit: Callable[[dict[str, Any]], None]) -> None:
        self.session, self.navigation, self.emit = session, navigation, emit
        self.keymap = PromptKeymap.load(get_config_dir() / "keybindings.json")
        self.enabled = False
        self.release_enabled = False
        self.closed = False
        self.owner = uuid.uuid4().hex
        self.used: set[str] = set()
        self.task: asyncio.Task[None] | None = None
        self.scope: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def negotiate(self, packet: dict[str, Any]) -> None:
        fields = ('terminal_input', 'terminal_release_input')
        if not any(key in packet for key in fields):
            return
        for key in fields:
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('Terminal capability requires exact integer 0 or 1')
        enabled = packet.get('terminal_input', int(self.enabled)) == 1
        release_enabled = packet.get('terminal_release_input', int(self.release_enabled)) == 1
        if self.enabled != enabled or self.release_enabled != release_enabled:
            self.owner = uuid.uuid4().hex
            if self.busy and self.task is not None and not self.task.cancelling():
                self.task.cancel()
        self.enabled, self.release_enabled = enabled, release_enabled
        runtime = getattr(self.session, 'extensions', None)
        self.emit({'type': 'terminal_capabilities', 'terminal_input': int(self.enabled),
                   'terminal_release_input': int(self.release_enabled),
                   'terminal_events': list(runtime.terminal_events) if runtime else [],
                   'session_id': self.session.session_id, 'session_revision': self.navigation.revision,
                   'terminal_owner': self.owner, 'worker_generation': runtime.generation if runtime else 0,
                   'active': bool(runtime and runtime.terminal_input_available), 'max_input_bytes': 16000})

    def start(self, packet: dict[str, Any], *, blocked: bool = False) -> None:
        session, runtime = self.session, self.session.extensions
        identifier = packet.get('request_id')
        if (not self.enabled or self.closed or self.busy or blocked or self.navigation.busy
                or not isinstance(identifier, str) or not identifier or len(identifier.encode()) > 128
                or identifier in self.used or len(self.used) >= 256
                or packet.get('session_id') != session.session_id
                or packet.get('session_revision') != self.navigation.revision
                or packet.get('terminal_owner') != self.owner
                or type(packet.get('worker_generation')) is not int
                or packet['worker_generation'] != runtime.generation or not runtime.terminal_input_available):
            raise ValueError('Terminal capability/context/worker unavailable or busy')
        kind = packet.get('kind')
        if not isinstance(kind, str):
            raise ValueError('Terminal input kind must be text')
        data = editable_data(packet.get('data'), kind, keymap=self.keymap, shortcuts=runtime.shortcuts(),
                             release=self.release_enabled and 'release' in runtime.terminal_events)
        scope = {key: packet[key] for key in ('request_id', 'session_id', 'session_revision',
                                             'terminal_owner', 'worker_generation')}
        approval_owner = session.permissions
        approval_revision = approval_owner.admission_revision
        child_approvals = tuple(session.children.permissions.items())
        child_revisions = tuple((name, manager, manager.admission_revision)
                                for name, manager in child_approvals)
        def alive() -> bool:
            return (self.enabled and not self.closed and self.owner == scope['terminal_owner']
                    and session.session_id == scope['session_id']
                    and self.navigation.revision == scope['session_revision']
                    and session.extensions is runtime and runtime.generation == scope['worker_generation']
                    and not session.extension_dialogs.has_pending
                    and not session.children.extensions.has_pending_dialogs
                    and session.permissions is approval_owner and approval_owner.admission_revision == approval_revision
                    and tuple(session.children.permissions.items()) == child_approvals
                    and all(manager.admission_revision == revision for _name, manager, revision in child_revisions)
                    and not session.permissions.has_pending_requests
                    and not any(manager.has_pending_requests for manager in session.children.permissions.values())
                    and not session._context_changes and not session._auth_changing and not session._compacting
                    and (kind != 'release' or self.release_enabled and 'release' in runtime.terminal_events))
        if not alive():
            raise ValueError('Approval/dialog or session lifecycle owns terminal input')
        self.used.add(identifier)
        self.scope = scope
        admitted = [False]
        async def run() -> None:
            admitted[0] = True
            result = {'type': 'terminal_result', **scope, 'kind': kind, 'success': False}
            try:
                if not alive():
                    raise ValueError('Terminal owner changed before callback')
                async with asyncio.timeout(5):
                    raw = await runtime.terminal_input(data, kind=kind, surface='prompt')
                if not alive():
                    raise ValueError('Terminal owner changed after callback')
                value = event_decision(raw, kind)
                if not value.consume and kind != 'release':
                    result['data'] = editable_data(data if value.data is None else value.data, kind,
                                                   keymap=self.keymap, shortcuts=runtime.shortcuts())
                result.update(success=True, consume=value.consume or kind == 'release')
            except asyncio.CancelledError:
                result.update(canceled=True, error='Terminal input canceled; original not replayed')
            except (ValueError, RuntimeError, OSError, TimeoutError):
                result['error'] = 'Terminal input unavailable/unsafe; original not replayed'
            if not self.closed:
                self.emit(result)
        self.task = asyncio.create_task(run_control(self.session, run, name="rpc-terminal"))
        def done(task: asyncio.Task[None]) -> None:
            if task.cancelled() and not admitted[0] and not self.closed:
                self.emit({'type': 'terminal_result', **scope, 'kind': kind, 'success': False,
                           'canceled': True, 'error': 'Canceled before terminal worker admission; no replay'})
            if not task.cancelled():
                task.exception()
        self.task.add_done_callback(done)

    def cancel(self, packet: dict[str, Any]) -> None:
        if not self.busy or self.scope is None or any(packet.get(key) != value for key, value in self.scope.items()):
            raise ValueError('Unknown/stale terminal cancellation owner')
        assert self.task is not None
        if not self.task.cancelling():
            self.task.cancel()

    async def aclose(self) -> None:
        self.closed = True
        if self.task is not None:
            if self.busy and not self.task.cancelling():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
