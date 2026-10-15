"""Owned native RPC navigation; extension requests do not bypass this idle/restore path."""
from __future__ import annotations

import asyncio
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from xdog.agent.extensions.catalog import page
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.navigation import navigation_entries
from xdog.coding.core.sessions.operations import run_control

EmitFn = Callable[[dict[str, Any]], None]


class RpcNavigation:
    def __init__(self, session: AgentSession, emit: EmitFn) -> None:
        self.session, self.emit = session, emit
        self.revision = secrets.token_hex(32)
        self.enforced = False
        self.task: asyncio.Task[None] | None = None
        self.owner: tuple[str, str] | None = None
        self.cancel_event: threading.Event | None = None
        self.used: set[str] = set()
        self.status_before: dict[str, Any] = {}
        self._action = ""
        self._admitted = False

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def compacting(self) -> bool:
        return self.busy and self._action == 'compact'

    def prompt_owner(self, packet: dict[str, Any]) -> None:
        if self.enforced and (packet.get('session_id') != self.session.session_id
                              or packet.get('session_revision') != self.revision):
            raise ValueError('RPC conversation changed; inspect navigation receipt before submitting a new prompt')

    def refresh_revision(self) -> dict[str, str]:
        self.revision = secrets.token_hex(32)
        return {'session_id': self.session.session_id, 'session_revision': self.revision}

    def start(self, packet: dict[str, Any], *, turn_running: bool, extension_running: bool,
              legacy_compact: bool = False, compact_alive: Callable[[], bool] | None = None,
              name_alive: Callable[[], bool] | None = None) -> None:
        action, key, expected = packet.get('action'), packet.get('request_id'), packet.get('expected_session')
        if (not isinstance(action, str)
                or action not in {'list', 'tree', 'new', 'resume', 'fork', 'branch', 'goto', 'rename', 'compact'}
                or not isinstance(key, str) or not key or len(key.encode()) > 128 or key in self.used
                or len(self.used) >= 256 or not isinstance(expected, str) or expected != self.session.session_id):
            raise ValueError('Invalid/stale/reused RPC session owner, action or request capacity')
        if self.busy or extension_running:
            raise RuntimeError('Another RPC control owns the session')
        if action not in {'list', 'tree'} and (turn_running or self.session.agent.is_streaming
                                             or self.session.children.supervisor.active):
            raise RuntimeError('Session navigation requires an idle parent and children')
        if self.enforced and packet.get('session_revision') != self.revision:
            raise ValueError('Conversation revision changed; refresh navigation state')
        target, branch, index = packet.get('target_session', ''), packet.get('branch_id', ''), packet.get('index', -1)
        for value in (target, branch):
            if (not isinstance(value, str) or len(value) > 128
                    or value and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value) is None):
                raise ValueError('Invalid session/branch identity')
        if type(index) is not int or not -1 <= index <= 1000000:
            raise ValueError('Invalid zero-based checkpoint index')
        if action == 'resume' and (not target or branch or index != -1):
            raise ValueError('Resume requires an explicit saved session only')
        if action == 'fork' and target and target != expected:
            raise ValueError('Fork saves the current live head only; target_session must be current')
        if action == 'branch' and (not branch or index != -1):
            raise ValueError('Branch requires an explicit saved branch and no checkpoint index')
        if action == 'goto' and index < 0:
            raise ValueError('Goto requires an explicit zero-based checkpoint')
        if action not in {'tree', 'resume', 'branch', 'goto', 'fork'} and (target or branch or index != -1):
            raise ValueError('Session action cannot silently substitute a navigation target')
        if action == 'fork' and branch:
            raise ValueError('Fork uses the actual current head only')
        if 'expected_name' in packet:
            if action != 'rename' or not isinstance(packet['expected_name'], str):
                raise ValueError('Expected name CAS requires an explicit naming action/text')
            from xdog.agent.session_name import session_name
            if session_name(packet['expected_name']) != packet['expected_name']:
                raise ValueError('Expected name must be canonical text')
        instructions = ''
        if action == 'compact':
            from xdog.agent.summarization import summary_instructions
            instructions = summary_instructions(packet.get('instructions', ''))
        elif 'instructions' in packet or legacy_compact:
            raise ValueError('Compaction instructions require an explicit compaction action')
        self.status_before = {'session_id': expected, 'model': self.session.model,
            'permission_mode': self.session.permissions.mode, 'messages': len(self.session.messages),
            'working_dir': str(self.session.working_dir), 'thinking': self.session.agent.options.thinking or 'off'}
        self.used.add(key)
        self.owner = key, expected
        self._action, self._admitted = action, False
        self.cancel_event = canceled = threading.Event()
        async def run() -> dict[str, Any]:
            self._admitted = True
            response: dict[str, Any] = {'type': 'session_result', 'request_id': key, 'action': action,
                                        'expected_session': expected, 'success': False}
            before = (self.session.session_id, self.session.session_data.active_branch,
                      self.session.agent.state.messages, len(self.session.session_data.branches))
            try:
                if canceled.is_set():
                    raise asyncio.CancelledError
                if action in {'list', 'tree'}:
                    request = {'scope': 'sessions' if action == 'list' else 'tree',
                        'session_id': target if action == 'tree' else '',
                        'branch_id': branch if action == 'tree' else '',
                        'query': packet.get('query', ''), 'offset': packet.get('offset', 0),
                        'revision': packet.get('catalog_revision', '')}
                    scope = 'sessions' if action == 'list' else 'tree|' + (target or expected) + '|' + branch
                    def read() -> dict[str, Any]:
                        rows = (asdict(entry) for entry in navigation_entries(self.session, scope))
                        return page(rows, request, expected)
                    result = await asyncio.to_thread(read)
                    if canceled.is_set() or self.session.session_id != expected:
                        raise asyncio.CancelledError
                    response.update(success=True, catalog=result)
                elif action == 'compact':
                    # The canonical async SDK owns summary/commit cleanup while
                    # this task reserves all existing navigation interlocks.
                    if compact_alive is None:
                        await self.session.compact(instructions)
                    else:
                        await self.session.compact(instructions, compact_alive)
                    self.refresh_revision()
                    if not legacy_compact:
                        self.enforced = True
                    response.update(success=True, result={'messages': len(self.session.messages)})
                else:
                    def mutate() -> Any:
                        previous = self.session._navigation_alive
                        def guard() -> bool:
                            return not canceled.is_set()
                        self.session._navigation_alive = guard
                        self.session._navigation_cancel.clear()
                        try:
                            if not guard() or self.session.session_id != expected:
                                raise asyncio.CancelledError
                            if action == 'resume':
                                if not self.session.switch_session(target):
                                    raise ValueError('Saved session unavailable')
                            elif action == 'new':
                                return self.session.new_session()
                            elif action == 'branch':
                                if target and target != expected:
                                    return self.session.navigate_checkpoint(target, branch_id=branch)
                                if not self.session.restore_branch(branch):
                                    raise ValueError('Branch unavailable')
                            elif action == 'goto':
                                return self.session.navigate_checkpoint(target or expected,
                                                                       branch_id=branch, index=index)
                            elif action == 'fork':
                                return self.session.create_branch(at_index=index if index >= 0 else None,
                                                                  label='RPC saved checkpoint')
                            elif action == 'rename':
                                name = packet.get('name')
                                if not isinstance(name, str):
                                    raise ValueError('Session name must be text')
                                return self.session.rename_session(name, expected_name=packet.get('expected_name'),
                                                                  alive=name_alive)
                        finally:
                            if self.session._navigation_alive is guard:
                                self.session._navigation_alive = previous
                    work = asyncio.create_task(asyncio.to_thread(mutate))
                    try:
                        result = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        canceled.set()
                        while not work.done():
                            try:
                                await asyncio.shield(work)
                            except asyncio.CancelledError:
                                continue
                        result = work.result()  # Admitted commits settle; never invent rollback.
                        response['receipt_canceled'] = True
                    self.revision = secrets.token_hex(32)
                    self.enforced = True
                    response.update(success=True, result=result)
                    if canceled.is_set():
                        response['receipt_canceled'] = True
            except asyncio.CancelledError:
                response.update(canceled=True, error=('Compaction canceled; inspect state before retrying'
                    if action == 'compact' else 'Unadmitted navigation canceled; inspect state before retrying'))
            except (ValueError, RuntimeError, OSError):
                response['error'] = ('Compaction unavailable/failed; no fallback or replay' if action == 'compact' else
                                     'Navigation unavailable/failed; no fallback or replay')
            after = (self.session.session_id, self.session.session_data.active_branch,
                     self.session.agent.state.messages, len(self.session.session_data.branches))
            if not response.get('success') and before != after:
                self.revision = secrets.token_hex(32)
                self.enforced = True
                response['state_changed'] = True
                if action == 'compact' and response.get('canceled'):
                    response['receipt_canceled'] = True
            response.update(session_id=self.session.session_id, session_revision=self.revision,
                            model=self.session.model, branch_id=self.session.session_data.active_branch)
            if legacy_compact:
                response.update(type='ok' if response['success'] else 'error', messages=len(self.session.messages))
                if not response['success']:
                    response['message'] = response['error']
            return response
        async def settled() -> None:
            response = await run_control(self.session, run, name="rpc-navigation")
            self.emit(response)  # Includes endpoint rebind and durable cleanup, not just the mutation body.
        self.task = asyncio.create_task(settled())

    def cancel(self, packet: dict[str, Any]) -> None:
        if self.owner != (packet.get('request_id'), packet.get('expected_session')) or not self.busy:
            raise ValueError('Unknown/stale navigation owner')
        if self.cancel_event is not None:
            self.cancel_event.set()  # Keep the admitted owner/reader task until SDK work actually settles.
        if self.compacting and self._admitted and self.task is not None and not self.task.cancelling():
            self.task.cancel()  # Canonical compaction shields an admitted disk commit until it settles.

    def abort_compaction(self) -> None:
        if self.compacting and self.owner is not None:
            self.cancel({'request_id': self.owner[0], 'expected_session': self.owner[1]})

    async def aclose(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
