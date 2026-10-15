"""Scoped RPC supervision of the existing Coding children, never a second agent/tool loop."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, cast

from xdog.agent import AgentEvent
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.forms import multi_choice_form
from xdog.agent.supervision import ChildSpec
from xdog.ai.types import ImageContent
from xdog.coding.config import get_config_dir
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.child_agents import CodingChildren
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.core.sessions.operations import run_control
from xdog.coding.modes.rpc.custom_delivery import CustomDelivery
from xdog.coding.modes.rpc.images import RpcImages
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.transcripts import RpcTranscripts
from xdog.tui.child_browser import child_entries
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text

CAPABILITIES = {'child_controls': 1, 'child_approvals': 1, 'child_dialogs': 1, 'child_forms': 1,
                'child_multi_forms': 1, 'child_contributions': 1, 'child_transient_events': 1,
                'child_terminal_input': 1, 'child_terminal_release_input': 1, 'child_sources': 1, 'child_canonical': 1,
                'child_custom_views': 1, 'child_overlay_controls': 1, 'child_editor_write': 1,
                'child_extension_queued_inputs': 1,
                'child_queues': 1, 'child_images': 1,
                'child_image_recovery': 1, 'child_task_recovery': 1, 'child_image_return': 1,
                'child_saved_drafts': 1, 'child_saved_images': 1, 'child_saved_discard': 1, 'child_saved_save': 1}


class RpcChildren:
    def __init__(self, session: AgentSession, navigation: RpcNavigation, sources: RpcTranscripts,
                 emit: Callable[[dict[str, Any]], None], *, images: RpcImages | None = None,
                 send: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        async def compatible_send(event: dict[str, Any]) -> None:
            emit(event)
            await asyncio.sleep(0)
        self.send = send or compatible_send
        self.images = images
        self.session, self.navigation, self.sources, self.emit = session, navigation, sources, emit
        self.loop = asyncio.get_running_loop()
        self.custom_delivery = CustomDelivery(self.loop)
        self.keymap = PromptKeymap.load(get_config_dir() / "keybindings.json")
        self.flags = dict.fromkeys(CAPABILITIES, False)
        self.lease = uuid.uuid4().hex
        self.bound: CodingChildren | None = None
        self.binding: tuple[str, str, str, str] | None = None
        self.previous: tuple[Any, Any, Any, Any, Any] | None = None
        self.owners: list[CodingChildren] = []
        self.jobs: dict[str, tuple[asyncio.Task[None], dict[str, Any], CodingChildren]] = {}
        self.used: set[str] = set()
        self.starting = False
        self.closed = False
        self.dialogs: dict[str, tuple[str, dict[str, Any]]] = {}
        self.permissions: dict[str, tuple[str, int]] = {}
        self.terminal_generation = 0
        self.custom_generation = 0
        self.custom_frames: dict[str, dict[str, Any]] = {}
        self.custom_sequences: dict[str, int] = {}
        self.approval_generation = 0
        self.contribution_generation = 0
        self.contribution_sequence = 0
        self._contribution_pending = 0
        self._contribution_limited = False
        self._contribution_lock = threading.Lock()
        self.transient_generation = 0
        self.transient_sequence = 0
        self._transient_pending = 0
        self._transient_limited = False
        self._transient_lock = threading.Lock()
        self.return_lease = uuid.uuid4().hex
        self.saved_generation = 0
        self.saved_saves: dict[str, tuple[str, int, float, dict[str, Any], bytes]] = {}
        self.save_preparing = 0
        self.discard_preparing = 0
        self.saved_discards: dict[str, tuple[str, int, float, dict[str, Any]]] = {}

    @property
    def busy(self) -> bool:
        return bool(self.jobs)

    def _envelope(self) -> dict[str, Any]:
        return {'session_id': self.session.session_id, 'session_revision': self.navigation.revision,
                'child_owner': self.lease, 'child_epoch': self.session.children.token,
                'child_return_owner': self.return_lease}

    def negotiate(self, packet: dict[str, Any]) -> None:
        for key in CAPABILITIES:
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('Child UI capabilities require exact integer 0 or 1')
        if any(key in packet and self.flags[key] != (packet[key] == 1)
               for key in ('child_controls', 'child_terminal_input', 'child_terminal_release_input')):
            self.terminal_generation += 1
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_terminal') and not task.cancelling():
                    task.cancel()
        if any(key in packet and self.flags[key] != (packet[key] == 1)
               for key in ('child_controls', 'child_custom_views', 'child_overlay_controls')):
            self.custom_generation += 1
            self.custom_frames.clear()
            if self.bound is not None:
                self.bound.extensions.cancel_dialogs(self.lease, lambda dialog: dialog['kind'] != 'custom')
        if any(key in packet and self.flags[key] != (packet[key] == 1)
               for key in ('child_controls', 'child_contributions')):
            self.contribution_generation += 1
        if any(key in packet and self.flags[key] != (packet[key] == 1)
               for key in ('child_controls', 'child_transient_events')):
            self.transient_generation += 1
        if ('child_approvals' in packet
                and self.flags['child_approvals'] != (packet['child_approvals'] == 1)):
            self.approval_generation += 1
        saved_flags = ('child_controls', 'child_saved_drafts', 'child_saved_images',
                       'child_saved_discard', 'child_saved_save')
        if any(key in packet and self.flags[key] != (packet[key] == 1) for key in saved_flags):
            self.saved_generation += 1
            self.saved_discards.clear()
            self.saved_saves.clear()
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_saved') and not task.cancelling():
                    task.cancel()
        return_flags = ('child_controls', 'child_queues', 'child_image_return')
        if any(key in packet and self.flags[key] != (packet[key] == 1) for key in return_flags):
            self.return_lease = uuid.uuid4().hex
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_return') and not task.cancelling():
                    task.cancel()
        for key in CAPABILITIES:
            if key in packet:
                self.flags[key] = packet[key] == 1
        if (not self.flags['child_task_recovery'] or not self.flags['child_queues']
                or 'child_image_recovery' in packet and packet['child_image_recovery'] == 0):
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_task') and not task.cancelling():
                    task.cancel()
        if not self.flags['child_image_recovery'] or not self.flags['child_queues']:
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_recovery') and not task.cancelling():
                    task.cancel()
        if not self.flags['child_images']:
            for task, scope, _owner in self.jobs.values():
                if scope.get('requires_images') and not task.cancelling():
                    task.cancel()
        if not self.flags['child_controls']:
            for task, _scope, _owner in self.jobs.values():
                if not task.cancelling():
                    task.cancel()
            self._detach()
        else:
            self._bind()
            if not self.flags['child_sources']:
                self.sources.cancel_children(self.lease, format='display')
            if not self.flags['child_canonical']:
                self.sources.cancel_children(self.lease, format='canonical')
            if not self.flags['child_approvals'] and self.bound is not None:
                for manager in tuple(self.bound.permissions.values()):
                    manager.clear_session_rules()
                    manager.deny_all()
                self.permissions.clear()
            assert self.bound is not None and self.binding is not None
            owned, binding, generation = self.bound, self.binding, self.approval_generation
            owned.approval_alive = lambda: (self._alive(owned, binding) and self.flags['child_approvals']
                                            and self.approval_generation == generation)
            owned.extensions.enable_editor_write(self.lease, self.flags['child_editor_write'])
            owned.extensions.enable_queued_input(self.lease, self.flags['child_extension_queued_inputs'])
            owned.extensions.cancel_dialogs(self.lease, self._supported)
            for identifier, (child, dialog) in tuple(self.dialogs.items()):
                if not self._supported(dialog):
                    assert self.bound is not None
                    self.bound.extensions.answer(child, identifier, self.lease, None)
                    self.dialogs.pop(identifier, None)
        if hasattr(self.session, 'children'):
            self.emit({'type': 'child_capabilities', **self._envelope(),
                       'terminal_generation': self.terminal_generation,
                       'queued_input_generation': (self.bound.extensions.queued_input_generation(self.lease)
                                                   if self.bound is not None else 0),
                       'child_terminal_registry': (self.bound.extensions.terminal_metadata(self.lease)
                            if self.flags['child_terminal_input'] and self.bound is not None else {}),
                       'custom_generation': self.custom_generation,
                       'event_generation': self.transient_generation,
                       'contribution_generation': self.contribution_generation,
                       **{key: int(self.flags[key] and self.flags['child_controls']) for key in CAPABILITIES}})

    def _alive(self, owner: CodingChildren, binding: tuple[str, str, str, str]) -> bool:
        return (not self.closed and self.flags['child_controls'] and self.bound is owner and self.binding == binding
                and self.session.children is owner and self.session.session_id == binding[0]
                and self.navigation.revision == binding[1])

    def _bind(self) -> None:
        owner = self.session.children
        key = self.session.session_id, self.navigation.revision, owner.token, self.lease
        if self.bound is owner and self.binding == key:
            return
        self._detach()
        if owner.extensions.default_ui is not None:
            raise RuntimeError('Another child UI owns this supervisor; no borrowed approval/dialog authority')
        self.lease = uuid.uuid4().hex
        key = self.session.session_id, self.navigation.revision, owner.token, self.lease
        self.bound, self.binding = owner, key
        self.previous = (owner.approver, owner.approval_alive, owner.extensions.default_ui, owner.supervisor.publish,
                         owner.supervisor.on_event)
        if owner not in self.owners:
            self.owners.append(owner)
        def publish(event: dict[str, Any]) -> None:
            if event.get('type') in {'extension_notice', 'extension_ui'}:
                if not self.flags['child_contributions'] or not self._alive(owner, key):
                    return
                from xdog.coding.core.canonical_source import safe_json
                generation = self.contribution_generation
                try:
                    payload = {'kind': event['type'], 'extension': event.get('extension', ''),
                               'text': safe_json(event.get('text', '')),
                               'ui': safe_json(event.get('snapshot', {}).get('ui', {}))}
                    if len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()) > 32 * 1024:
                        payload = {'kind': event['type'], 'limited': True}
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    payload = {'kind': event['type'], 'limited': True}
                with self._contribution_lock:
                    if self._contribution_pending >= 32:
                        self._contribution_limited = True
                        return
                    self._contribution_pending += 1
                self.loop.call_soon_threadsafe(self._deliver_contribution, owner, key, generation,
                                              event.get('child_id'), payload)
                return
            copied = json.loads(json.dumps(event, ensure_ascii=True, allow_nan=False))
            if event.get('type') in {'child_extension_custom_frame', 'child_extension_custom_error',
                                     'child_extension_custom_control', 'child_extension_dialog'}:
                copied['_custom_generation'] = self.custom_generation
            if event.get('type') == 'child_extension_custom_frame':
                size = len(json.dumps(copied, ensure_ascii=True, allow_nan=False).encode())
                if not self.custom_delivery.schedule(self._deliver, (owner, key, copied), size):
                    child, identifier = event.get('child_id'), event.get('id')
                    if isinstance(child, str) and isinstance(identifier, str):
                        generation = self.custom_generation
                        self.custom_delivery.report_failure((*key, child, identifier, str(generation)),
                            self._frame_overflow, (owner, key, generation, child, identifier))
                        owner.extensions.answer(child, identifier, self.lease, None)
                    return  # Overflow cancels this child's view; no automatic callback/input replay.
            else:
                self.loop.call_soon_threadsafe(self._deliver, owner, key, copied)
        def approve(child: str, request: PermissionRequest) -> None:
            if not self._alive(owner, key) or not self.flags['child_approvals']:
                owner.resolve(child, request.id, 'deny')
                return
            publish({'type': 'child_permission_request', 'child_id': child, 'id': request.id,
                     'tool': request.tool_name, 'arguments': request.arguments, 'summary': request.summary,
                     'approval_generation': self.approval_generation})
        owner.approver = approve
        owner.approval_alive = lambda: self._alive(owner, key) and self.flags['child_approvals']
        owner.extensions.enable_editor_write(self.lease, self.flags['child_editor_write'])
        async def queue_receipt(event: dict[str, Any]) -> bool:
            from xdog.agent.supervision import settle_on
            async def deliver() -> bool:
                def alive() -> bool:
                    return (self._alive(owner, key) and self.flags['child_extension_queued_inputs']
                        and event.get('child_owner') == self.lease
                        and event.get('queued_input_generation')
                        == owner.extensions.queued_input_generation(self.lease))
                if not alive():
                    return False
                await self.send({**event, **self._envelope()})
                return alive()
            return await settle_on(self.loop, deliver())
        owner.extensions.set_queued_input_sender(self.lease, queue_receipt)
        owner.extensions.enable_queued_input(self.lease, self.flags['child_extension_queued_inputs'])
        owner.extensions.default_ui = self.lease, publish
        owner.supervisor.publish = publish
        previous_event = self.previous[4]
        async def on_event(spec: ChildSpec, event: AgentEvent) -> None:
            generation = self.transient_generation
            admitted = self.flags['child_transient_events'] and self._alive(owner, key)
            if previous_event is not None:
                await previous_event(spec, event)
            if (admitted and self.flags['child_transient_events']
                    and generation == self.transient_generation and self._alive(owner, key)):
                from xdog.coding.modes.rpc.events import MAX_EVENT_BYTES, _payload
                try:
                    payload = _payload(event)
                    if payload is None:
                        return
                    if len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()) > MAX_EVENT_BYTES // 2:
                        payload = {'event': event.type, 'data': {}, 'limited': True}
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    payload = {'event': event.type, 'data': {}, 'limited': True}
                with self._transient_lock:
                    if self._transient_pending >= 32:
                        self._transient_limited = True
                        return
                    self._transient_pending += 1
                self.loop.call_soon_threadsafe(self._deliver_transient, owner, key, generation, spec.id, payload)
        owner.supervisor.on_event = on_event

    def _detach(self) -> None:
        owner, previous = self.bound, self.previous
        self.sources.cancel_children(self.lease)
        if owner is not None and previous is not None:
            for manager in tuple(owner.permissions.values()):
                manager.clear_session_rules()
                manager.deny_all()
            owner.extensions.detach(self.lease)
            (owner.approver, owner.approval_alive, owner.extensions.default_ui, owner.supervisor.publish,
             owner.supervisor.on_event) = previous
        self.bound = None
        self.binding = None
        self.previous = None
        self.dialogs.clear()
        self.custom_frames.clear()
        self.custom_sequences.clear()
        self.permissions.clear()

    def _frame_overflow(self, owner: CodingChildren, binding: tuple[str, str, str, str],
                        generation: int, child: str, identifier: str) -> None:
        if (not self._alive(owner, binding) or not self.flags['child_custom_views']
                or generation != self.custom_generation):
            return
        self.custom_frames.pop(identifier, None)
        self.emit({'type': 'child_extension_custom_error', **self._envelope(), 'child_id': child,
                   'id': identifier, 'custom_generation': generation, 'code': 'frame_delivery_capacity',
                   'canceled': True,
                   'error': 'Child custom frame delivery capacity exceeded; view canceled, no input replay'})

    def _deliver_contribution(self, owner: CodingChildren, binding: tuple[str, str, str, str],
                              generation: int, child: Any, payload: dict[str, Any]) -> None:
        with self._contribution_lock:
            self._contribution_pending -= 1
            limited, self._contribution_limited = self._contribution_limited, False
        if (not self.flags['child_contributions'] or generation != self.contribution_generation
                or not self._alive(owner, binding) or not isinstance(child, str)):
            return
        try:
            owner.supervisor.capture_source(child)
        except ValueError:
            return
        self.contribution_sequence += 1
        self.emit({'type': 'child_contribution', **self._envelope(), 'child_id': child,
                   'generation': generation, 'sequence': self.contribution_sequence, 'retained': False,
                   'data': payload, **({'limited': True} if limited else {})})

    def _deliver_transient(self, owner: CodingChildren, binding: tuple[str, str, str, str],
                           generation: int, child: str, payload: dict[str, Any]) -> None:
        with self._transient_lock:
            self._transient_pending -= 1
            limited, self._transient_limited = self._transient_limited, False
        if (not self.flags['child_transient_events'] or generation != self.transient_generation
                or not self._alive(owner, binding)):
            return
        try:
            owner.supervisor.capture_source(child)
        except ValueError:
            return  # A closed child cannot donate progress to a replacement child/view.
        self.transient_sequence += 1
        self.emit({'type': 'child_transient_event', **self._envelope(), 'child_id': child,
                   'event_generation': generation, 'sequence': self.transient_sequence,
                   'retained': False, **payload, **({'limited': True} if limited else {})})

    def _supported(self, dialog: dict[str, Any]) -> bool:
        if dialog['kind'] == 'session' and dialog.get('operation') == 'queue_input':
            return self.flags['child_extension_queued_inputs']
        if dialog['kind'] == 'editor' and dialog.get('operation') == 'propose':
            return self.flags['child_editor_write']
        if dialog['kind'] == 'custom':
            return self.flags['child_custom_views'] and (not dialog.get('live_overlay', False)
                                                       or self.flags['child_overlay_controls'])
        if not self.flags['child_dialogs'] or dialog['kind'] not in {'input', 'select', 'confirm', 'form'}:
            return False
        return (dialog['kind'] != 'form' or self.flags['child_forms']
                and (self.flags['child_multi_forms'] or not multi_choice_form(dialog)))

    def _catalog(self, owner: CodingChildren) -> dict[str, Any]:
        raw = owner.supervisor.snapshot()
        return {'children': [{**asdict(entry), 'task': redact_sensitive_text(entry.task),
            'error': redact_sensitive_text(entry.error)} for entry in child_entries(raw)],
            'error': redact_sensitive_text(sanitize_terminal_text(str(raw.get('error', ''))))}

    def _deliver(self, owner: CodingChildren, binding: tuple[str, str, str, str], event: dict[str, Any]) -> None:
        if not self._alive(owner, binding):
            if event.get('type') == 'child_permission_request':
                owner.resolve(event['child_id'], event['id'], 'deny')
            return
        kind = event.get('type')
        if kind in {'child_extension_input_queued', 'child_extension_input_consumed',
                    'child_extension_input_returned'}:
            if event.get('child_owner') == self.lease:
                self.emit({**event, **self._envelope()})
        elif kind == 'child_status':
            self.emit({'type': 'child_status', **self._envelope(), 'child_id': event['child_id'],
                       'catalog': self._catalog(owner)})
        elif kind == 'child_permission_request':
            if (self.flags['child_approvals'] and event.get('approval_generation') == self.approval_generation
                    and owner.has_pending(event['child_id'], event['id'])):
                self.permissions[event['id']] = event['child_id'], self.approval_generation
                self.emit({**event, **self._envelope()})
            else:
                owner.resolve(event['child_id'], event['id'], 'deny')
        elif kind == 'child_extension_dialog':
            dialog = validate_dialog(event['dialog'])
            if (dialog.get('operation') == 'queue_input' and event.get('queued_input_generation')
                    != owner.extensions.queued_input_generation(self.lease)):
                owner.extensions.answer(event['child_id'], dialog['id'], self.lease, None)
                return
            if (dialog.get('operation') == 'propose' and dialog.get('editor_write_generation')
                    != owner.extensions.editor_write_generation(self.lease)):
                owner.extensions.answer(event['child_id'], dialog['id'], self.lease, None)
                return
            if dialog['kind'] == 'custom' and event.get('_custom_generation') != self.custom_generation:
                return
            if not owner.extensions.contains(event['child_id'], dialog['id'], self.lease):
                return
            if event.get('child_owner') != self.lease or not self._supported(dialog):
                owner.extensions.answer(event['child_id'], dialog['id'], self.lease, None)
                return
            if dialog.get('parent_id'):
                self.custom_frames.pop(dialog['parent_id'], None)
            self.dialogs[dialog['id']] = event['child_id'], dialog
            event.pop('_custom_generation', None)
            self.emit({**event, **self._envelope(),
                       **({'custom_generation': self.custom_generation} if dialog['kind'] == 'custom' else {})})
        elif kind in {'child_extension_custom_frame', 'child_extension_custom_error', 'child_extension_custom_control'}:
            identifier = event.get('id')
            if not isinstance(identifier, str):
                return
            item = self.dialogs.get(identifier)
            if (item is None or item[0] != event.get('child_id') or not self.flags['child_custom_views']
                    or event.get('_custom_generation') != self.custom_generation
                    or not owner.extensions.contains(item[0], identifier, self.lease)):
                return
            event.pop('_custom_generation', None)
            if kind == 'child_extension_custom_control':
                if not self.flags['child_overlay_controls']:
                    return
                item[1]['overlay_state'] = dict(event['state'])
                self.custom_frames.pop(identifier, None)
                event['custom_generation'] = self.custom_generation
            if kind == 'child_extension_custom_frame':
                from xdog.coding.modes.rpc.terminal_data import plain_custom_line
                if event.get('superseded') is True:
                    return  # Obsolete render cannot restore a retired input receipt.
                event = {**event, 'lines': [plain_custom_line(line) for line in event['lines']],
                         'custom_generation': self.custom_generation}
            try:
                self.emit({**event, **self._envelope()})
            except OSError:
                if kind == 'child_extension_custom_frame':
                    self.custom_frames.pop(identifier, None)
                    owner.extensions.answer(item[0], identifier, self.lease, None)
                    return  # No cached frame when the output sink refused admission.
                raise
            if kind == 'child_extension_custom_frame':
                self.custom_frames[identifier] = event
        elif kind in {'child_extension_editor_write_commit', 'child_extension_editor_write_discard'}:
            if (self.flags['child_editor_write'] and event.get('child_owner') == self.lease
                    and event.get('editor_write_generation') == owner.extensions.editor_write_generation(self.lease)
                    and event.get('editor_write', {}).get('child_id') == event.get('child_id')
                    and not self.session.permissions.has_pending_requests
                    and not any(manager.has_pending_requests for manager in owner.permissions.values())):
                self.emit({**event, **self._envelope(),
                    'type': ('child_extension_editor_write_apply' if kind.endswith('_commit') else kind),
                    'notice': 'Client request only; recheck original child editor owner and one-use draft CAS'})
        elif kind == 'child_extension_dialog_closed':
            self.custom_frames.pop(str(event.get('id')), None)
            self.custom_sequences.pop(str(event.get('id')), None)
            self.dialogs.pop(str(event.get('id')), None)
            self.emit({**event, **self._envelope()})
        # Custom/editor/session/terminal callbacks cannot borrow parent UI authority.

    def _owner(self, packet: dict[str, Any]) -> CodingChildren:
        if not self.flags['child_controls'] or self.closed:
            raise ValueError('Negotiate child_controls:1 before using child controls')
        self._bind()
        if any(packet.get(key) != value for key, value in self._envelope().items() if key != 'child_return_owner'):
            raise ValueError('Child parent/revision/connection/epoch changed; refresh capabilities')
        assert self.bound is not None
        return self.bound

    def source(self, packet: dict[str, Any]) -> None:
        owner = self._owner(packet)
        format = packet.get('format', 'display')
        if not isinstance(format, str) or format not in {'display', 'canonical'}:
            raise ValueError('Child source format must be display or canonical')
        capability = 'child_canonical' if format == 'canonical' else 'child_sources'
        if not self.flags[capability]:
            raise ValueError('Negotiate the independent child source format capability before reading content')
        child = packet.get('child_id')
        if not isinstance(child, str) or re.fullmatch(r'[a-f0-9]{12}', child) is None or self.navigation.busy:
            raise ValueError('Invalid child source/context owner')
        captured = owner.supervisor.capture_source(child)
        binding = self.binding
        assert binding is not None
        def alive() -> bool:
            if not self.flags[capability] or not self._alive(owner, binding):
                return False
            try:
                owner.supervisor.capture_source(child)
                return True
            except ValueError:
                return False
        self.sources.start_child(packet, captured, owner=self.lease, epoch=owner.token, alive=alive)

    def answer_permission(self, packet: dict[str, Any]) -> None:
        owner = self._owner(packet)
        key, child, decision = packet.get('id'), packet.get('child_id'), packet.get('decision')
        if (not self.flags['child_approvals'] or not isinstance(key, str)
                or self.permissions.get(key) != (child, self.approval_generation)
                or packet.get('approval_generation') != self.approval_generation
                or not isinstance(child, str) or not isinstance(decision, str)
                or decision not in {'deny', 'allow_once', 'allow_session'}):
            raise ValueError('Unknown/stale/invalid child permission request')
        if not owner.resolve(child, key, cast(PermissionDecision, decision)):
            raise ValueError('Child permission request already expired/settled')
        self.permissions.pop(key, None)
        self.emit({'type': 'child_permission_ack', **self._envelope(), 'id': key, 'child_id': child})

    def terminal_input(self, packet: dict[str, Any]) -> None:
        owner = self._owner(packet)
        key, child = packet.get('request_id'), packet.get('child_id')
        if (not self.flags['child_terminal_input'] or not isinstance(key, str) or not key or len(key.encode()) > 128
                or key in self.used or len(self.used) >= 256 or not isinstance(child, str)
                or self.navigation.busy or self.jobs):
            raise ValueError('Invalid/busy/unnegotiated child terminal input owner')
        generation = self.terminal_generation
        if type(packet.get('terminal_generation')) is not int or packet['terminal_generation'] != generation:
            raise ValueError('Child terminal capability generation changed')
        from xdog.coding.modes.rpc.terminal_data import editable_data
        kind = packet.get('kind')
        worker_generation = packet.get('worker_generation')
        if type(worker_generation) is not int:
            raise ValueError('Child terminal worker generation must be an integer')
        extension, extension_alive = owner.extensions.capture_terminal(child, self.lease, worker_generation)
        if not isinstance(kind, str):
            raise ValueError('Child terminal input kind must be text')
        data = editable_data(packet.get('data'), kind, keymap=self.keymap, shortcuts=extension.shortcuts(),
                             release=self.flags['child_terminal_release_input']
                             and 'release' in extension.terminal_events)
        if not extension.terminal_input_available:
            raise ValueError('Child terminal worker has no registered hook')
        binding = self.binding
        assert binding is not None
        approval_owner = self.session.permissions
        approval_revision = approval_owner.admission_revision
        child_approvals = tuple(owner.permissions.items())
        child_revisions = tuple((name, manager, manager.admission_revision)
                                for name, manager in child_approvals)
        def alive() -> bool:
            return (self.flags['child_terminal_input'] and self.terminal_generation == generation
                    and self._alive(owner, binding) and extension_alive()
                    and (kind != 'release' or self.flags['child_terminal_release_input']
                         and 'release' in extension.terminal_events)
                    and not self.session.extension_dialogs.has_pending
                    and not owner.extensions.has_pending_dialogs
                    and self.session.permissions is approval_owner
                    and approval_owner.admission_revision == approval_revision
                    and tuple(owner.permissions.items()) == child_approvals
                    and all(manager.admission_revision == revision for _name, manager, revision in child_revisions)
                    and not self.session.permissions.has_pending_requests
                    and not any(manager.has_pending_requests for manager in owner.permissions.values()))
        if not alive():
            raise ValueError('Child dialog/approval or terminal owner changed')
        self.used.add(key)
        envelope = {**self._envelope(), 'request_id': key, 'child_id': child,
                    'terminal_generation': generation, 'worker_generation': packet['worker_generation'],
                    'requires_terminal': True}
        begun = [False]
        async def run() -> None:
            begun[0] = True
            result: dict[str, Any] = {'type': 'child_terminal_result',
                **{key: value for key, value in envelope.items() if key != 'requires_terminal'},
                'success': False, 'kind': kind}
            try:
                if not alive():
                    raise ValueError('Child terminal owner changed before callback')
                async with asyncio.timeout(5):
                    decision = await extension.terminal_input(data, kind=kind, surface='child-prompt')
                if not alive():
                    raise ValueError('Child terminal owner changed after callback')
                from xdog.agent.extensions.terminal_input import event_decision as validate_decision
                value = validate_decision(decision, kind)
                if not value.consume and kind != 'release':
                    transformed = editable_data(data if value.data is None else value.data, kind,
                                                keymap=self.keymap, shortcuts=extension.shortcuts())
                    result['data'] = transformed
                result.update(success=True, consume=value.consume or kind == 'release')
            except asyncio.CancelledError:
                result.update(canceled=True, error='Terminal input canceled; original not replayed')
            except (ValueError, RuntimeError, OSError, TimeoutError):
                result['error'] = 'Terminal input unavailable/unsafe; original not replayed'
            finally:
                result['terminal_registry'] = owner.extensions.terminal_metadata(self.lease)
            if not self.closed:
                self.emit(result)
        task = asyncio.create_task(run_control(self.session, run, name="rpc-children"))
        self.jobs[key] = task, envelope, owner
        def done(completed: asyncio.Task[None]) -> None:
            self.jobs.pop(key, None)
            if completed.cancelled() and not begun[0] and not self.closed:
                self.emit({'type': 'child_terminal_result',
                    **{name: value for name, value in envelope.items() if name != 'requires_terminal'},
                    'success': False, 'canceled': True, 'kind': kind,
                    'error': 'Terminal input canceled before worker admission; original not replayed'})
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)

    def custom_input(self, packet: dict[str, Any]) -> None:
        owner = self._owner(packet)
        child, identifier = packet.get('child_id'), packet.get('id')
        item = self.dialogs.get(identifier) if isinstance(identifier, str) else None
        if (not self.flags['child_custom_views'] or not isinstance(child, str) or not isinstance(identifier, str)
                or item is None or item[0] != child or item[1]['kind'] != 'custom'
                or type(packet.get('custom_generation')) is not int
                or packet['custom_generation'] != self.custom_generation
                or not owner.extensions.input_owner(child, identifier, self.lease)):
            raise ValueError('Unknown/stale child custom view owner/generation')
        if (self.session.permissions.has_pending_requests
                or any(manager.has_pending_requests for manager in owner.permissions.values())):
            raise ValueError('Tool approval owns input; child custom view cannot borrow it')
        sequence = packet.get('sequence')
        if (type(sequence) is not int or not 0 < sequence < 2**53
                or sequence <= self.custom_sequences.get(identifier, 0)):
            raise ValueError('Child custom input sequence reused/out of order')
        from xdog.agent.extensions.custom import event as validate_event
        event = validate_event(packet.get('event'))
        if event['kind'] != 'resize' and not owner.extensions.custom_input_owner(child, identifier, self.lease):
            raise ValueError('Hidden/non-capturing child overlay cannot own interactive input')
        current = self.custom_frames.get(identifier)
        if event['kind'] != 'resize' and (current is None or event['painted'] != current['revision']
                or (event['width'], event['height']) != (current['width'], current['height'])
                or event.get('generation', 0) != current['generation']):
            raise ValueError('Child custom input requires the current delivered frame')
        if not owner.extensions.request_custom(child, identifier, self.lease, event):
            raise ValueError('Child custom broker queue/owner unavailable')
        # Admission revokes the old painted receipt immediately; it cannot
        # authorize another key while this callback/resize is still rendering.
        self.custom_frames.pop(identifier, None)
        self.custom_sequences[identifier] = sequence
        self.emit({'type': 'child_custom_input_ack', **self._envelope(), 'id': identifier,
                   'child_id': child, 'sequence': sequence})

    def answer_dialog(self, packet: dict[str, Any]) -> None:
        owner = self._owner(packet)
        key, child = packet.get('id'), packet.get('child_id')
        item = self.dialogs.get(key) if isinstance(key, str) else None
        if (not isinstance(key, str) or not isinstance(child, str) or item is None
                or item[0] != child or not self._supported(item[1])):
            raise ValueError('Unknown/stale/unsupported child extension dialog')
        if packet.get('value') is not None:
            if item[1]['kind'] == 'custom':
                raise ValueError('Child custom completion requires painted frame/input protocol')
            if not owner.extensions.input_owner(child, key, self.lease):
                raise ValueError('Another child dialog owns input')
        if not owner.extensions.answer(child, key, self.lease, packet.get('value')):
            raise ValueError('Child dialog already expired/settled')
        self.dialogs.pop(key, None)
        self.emit({'type': 'child_dialog_ack', **self._envelope(), 'id': key, 'child_id': child})

    def start(self, packet: dict[str, Any], *, turn_running: bool, extension_running: bool) -> None:
        owner = self._owner(packet)
        action, key, child = packet.get('action'), packet.get('request_id'), packet.get('child_id', '')
        if (not isinstance(key, str) or not key or len(key.encode()) > 128 or key in self.used or len(self.used) >= 256
                or not isinstance(action, str) or action not in {'list', 'presets', 'configure', 'start', 'inspect',
                                                                'steer', 'follow-up', 'cancel', 'wait',
                                                                'close', 'queue', 'saved_catalog', 'saved_read',
                                                                'saved_discard_prepare', 'saved_discard_confirm',
                                                                'saved_discard_cancel', 'saved_save_prepare',
                                                                'saved_save_confirm', 'saved_save_cancel'}):
            raise ValueError('Invalid/reused child control request or capacity')
        if self.navigation.busy or len(self.jobs) >= 4:
            raise RuntimeError('Child controls/navigation still own work; inspect after settlement')
        if action == 'start' and (turn_running or self.session.agent.is_streaming or extension_running or self.starting
                                  or self.session._compacting or self.session._auth_changing
                                  or self.session._context_changes):
            raise RuntimeError('Child admission requires an idle parent/extension/start owner')
        if action in {'inspect', 'steer', 'follow-up', 'cancel', 'wait', 'close', 'queue'} and (
                not isinstance(child, str) or re.fullmatch(r'[a-f0-9]{12}', child) is None):
            raise ValueError('Invalid child identity')
        if action == 'queue':
            from xdog.agent.supervision_queue import request
            if not self.flags['child_queues']:
                raise ValueError('Negotiate child_queues:1 before viewing or editing child input')
            queue_request = request(packet.get('queue'))
        else:
            queue_request = None
        uses_save = action in {'saved_save_prepare', 'saved_save_confirm', 'saved_save_cancel'}
        if uses_save and not self.flags['child_saved_save']:
            raise ValueError('Negotiate child_saved_save:1 independently before creating saved snapshots')
        uses_discard = action in {'saved_discard_prepare', 'saved_discard_confirm', 'saved_discard_cancel'}
        uses_saved = action in {'saved_catalog', 'saved_read'} or uses_discard or uses_save
        if uses_discard and not self.flags['child_saved_discard']:
            raise ValueError('Negotiate child_saved_discard:1 independently before deleting saved snapshots')
        saved_generation = self.saved_generation
        snapshot_id, snapshot_sha256 = packet.get('snapshot_id'), packet.get('snapshot_sha256')
        if uses_saved and not self.flags['child_saved_drafts']:
            raise ValueError('Negotiate child_saved_drafts:1 independently before accessing saved local snapshots')
        if action in {'saved_read', 'saved_discard_prepare'} and (not isinstance(snapshot_id, str)
                or re.fullmatch(r'[a-f0-9]{32}', snapshot_id) is None
                or not isinstance(snapshot_sha256, str) or re.fullmatch(r'[a-f0-9]{64}', snapshot_sha256) is None):
            raise ValueError('Saved draft read requires exact catalog snapshot_id and snapshot_sha256')
        discard_review = packet.get('review_id')
        if action in {'saved_discard_confirm', 'saved_discard_cancel', 'saved_save_confirm', 'saved_save_cancel'} and (
                not isinstance(discard_review, str) or re.fullmatch(r'[a-f0-9]{32}', discard_review) is None):
            raise ValueError('Saved discard confirmation/cancel requires an exact review_id')
        uses_return = queue_request is not None and queue_request['action'] in {
            'return_prepare', 'return_commit', 'return_cancel'}
        if uses_return and (not self.flags['child_image_return']
                            or packet.get('child_return_owner') != self.return_lease):
            raise ValueError('Negotiate child_image_return:1 and use the current child_return_owner lease')
        return_lease = self.return_lease
        uses_task = queue_request is not None and queue_request['action'] in {'recover_task', 'remove_task'}
        if uses_task and not self.flags['child_task_recovery']:
            raise ValueError('Negotiate child_task_recovery:1 for initial task recovery/discard')
        uses_recovery = queue_request is not None and queue_request['action'] == 'recover_images'
        if uses_recovery and not self.flags['child_image_recovery']:
            raise ValueError('Negotiate child_image_recovery:1 independently for stopped binary image copy')
        uses_images = 'images' in packet or 'image_refs' in packet
        image_packet: dict[str, Any] | None = None
        input_images: tuple[ImageContent, ...] = ()
        if uses_images:
            if (action == 'saved_save_prepare' and not self.flags['child_saved_images']
                    or action != 'saved_save_prepare' and (not self.flags['child_images']
                        or action not in {'start', 'steer', 'follow-up'})):
                raise ValueError('Negotiate child_images:1 for child start/steer/follow-up images')
            if 'images' in packet and 'image_refs' in packet:
                raise ValueError('Choose inline images or uploaded image_refs, not both')
            if 'image_refs' in packet:
                if self.images is None:
                    raise ValueError('Owned child image upload transport unavailable; no fallback')
                input_images = self.images.resolve(packet)
                image_packet = {'session_id': packet['session_id'], 'image_refs': list(packet['image_refs'])}
            else:
                from xdog.coding.modes.rpc.rpc_mode import _prompt_images
                input_images = _prompt_images(packet['images'])
        config = owner.supervisor.presets.resolve(packet.get('config', {})).to_dict()
        task, access, text = packet.get('task', ''), packet.get('access', 'read-only'), packet.get('text', '')
        if (not isinstance(task, str) or len(task.encode()) > 16000
                or action == 'start' and not (task.strip() or input_images)
                or not isinstance(access, str) or access not in {'read-only', 'inherit'}
                or not isinstance(text, str) or len(text.encode()) > 16000):
            raise ValueError('Invalid child task/access/text')
        self.used.add(key)
        binding, envelope = self.binding, {**self._envelope(), 'request_id': key, 'action': action,
                                          'requires_images': uses_images, 'requires_recovery': uses_recovery,
                                          'requires_task': uses_task, 'requires_return': uses_return,
                                          'requires_saved': uses_saved}
        assert binding is not None
        if action == 'start':
            self.starting = True
        begun = [False]
        async def run() -> None:
            begun[0] = True
            reply: dict[str, Any] = {'type': 'child_result', **envelope, 'success': False}
            admitted = ''
            prepared_token = ''
            try:
                if (not self._alive(owner, binding) or uses_images
                        and not self.flags['child_saved_images' if uses_save else 'child_images']):
                    raise ValueError('Child owner/image capability changed before admission')
                if action in {'list', 'inspect'}:
                    if action == 'inspect':
                        owner.supervisor.capture_source(child)
                    reply['catalog'] = self._catalog(owner)
                elif action == 'presets':
                    reply.update(presets=owner.supervisor.presets.snapshot(), error=owner.preset_error)
                elif action == 'configure':
                    reply['config'] = config
                elif action == 'start':
                    await self.session.initialize_extensions()
                    self.session._rebuild_system_prompt()
                    if not self._alive(owner, binding):
                        raise ValueError('Child owner changed during parent preflight')
                    starting = asyncio.create_task(owner.supervisor.start(task, access=access, config=config,
                                                                          images=input_images))
                    try:
                        admitted = await asyncio.shield(starting)
                    except asyncio.CancelledError:
                        while not starting.done():
                            try:
                                await asyncio.shield(starting)
                            except asyncio.CancelledError:
                                continue
                        if not starting.cancelled() and starting.exception() is None:
                            admitted = starting.result()
                        raise
                    reply['child_id'] = admitted
                    reply['accepted_input'] = owner.supervisor.admission_receipt(admitted)
                    reply['catalog'] = self._catalog(owner)
                elif uses_save:
                    from xdog.agent.supervision_transfer import settled_image_work
                    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
                    from xdog.tui.child_drafts import ChildDraft
                    store = ChildDraftStore(self.session.session_manager._dir / 'saved-child-drafts')
                    def save_alive() -> bool:
                        return (self._alive(owner, binding) and self.flags['child_saved_drafts']
                                and self.flags['child_saved_save'] and self.saved_generation == saved_generation)
                    if not save_alive():
                        raise ValueError('Saved snapshot save revoked before admission')
                    now = time.monotonic()
                    self.saved_saves = {review: value for review, value in self.saved_saves.items() if value[2] > now}
                    if action == 'saved_save_prepare':
                        if self.save_preparing + len(self.saved_saves) >= 4:
                            raise ValueError('Saved snapshot review capacity reached; cancel/expire old reviews')
                        # No caller path or tool/model configuration can become stored execution authority.
                        if not text.strip() and not input_images:
                            raise ValueError('Saved draft requires text or actual image bytes')
                        label = packet.get('label', 'RPC child draft snapshot')
                        if not isinstance(label, str) or len(label.encode()) > 4096:
                            raise ValueError('Invalid saved draft label')
                        self.save_preparing += 1
                        try:
                            def prepare_save() -> tuple[bytes, dict[str, Any]]:
                                import base64
                                import hashlib

                                from xdog.tui.attachments import attachment
                                from xdog.tui.child_drafts import draft_receipt
                                images = tuple(attachment(base64.b64decode(image.data, validate=True),
                                                          f'RPC saved image {index + 1}')
                                               for index, image in enumerate(input_images))
                                if any(part.mime_type != source.mime_type for part, source in zip(
                                        images, input_images, strict=True)):
                                    raise ValueError('RPC saved image MIME mismatch')
                                draft = ChildDraft(text, 'read-only', {'inherit_context': False}, images, True)
                                data = encode_draft(draft, label=label, scope=self.session.session_id,
                                                    action='start', slot='rpc-explicit')
                                review = {'operation': 'save_private_snapshot', 'label': label,
                                          'source_session': self.session.session_id, 'content': text,
                                          'image_receipt': draft_receipt(text, images),
                                          'image_bytes': sum(len(image.data) for image in images),
                                          'snapshot_bytes': len(data),
                                          'snapshot_sha256': hashlib.sha256(data).hexdigest(),
                                          'effect': 'Create PRIVATE PLAINTEXT snapshot only; no overwrite or execution'}
                                return data, review
                            data, save_review = await settled_image_work(prepare_save)
                        finally:
                            self.save_preparing -= 1
                        if not save_alive() or uses_images and not self.flags['child_saved_images']:
                            raise ValueError('Saved image snapshot owner changed during preparation')
                        if sum(len(value[4]) for value in self.saved_saves.values()) + len(data) > 56 * 1024 * 1024:
                            raise ValueError('Prepared saved snapshot byte budget exceeded')
                        review_id = uuid.uuid4().hex
                        self.saved_saves[review_id] = self.lease, saved_generation, now + 30, save_review, data
                        reply.update(review_id=review_id, review=json.loads(json.dumps(save_review)))
                        # Prepared bytes are copied/owned by the review; retire supplied
                        # upload references once, never silently borrow a future upload.
                    else:
                        assert isinstance(discard_review, str)
                        saved_review = self.saved_saves.get(discard_review)
                        if (saved_review is None or saved_review[0] != self.lease
                                or saved_review[1] != saved_generation or saved_review[2] <= time.monotonic()):
                            raise ValueError('Saved snapshot review expired/foreign/revoked')
                        if action == 'saved_save_cancel':
                            self.saved_saves.pop(discard_review)
                            reply.update(review_id=discard_review, canceled_review=True)
                        else:
                            if packet.get('review') != saved_review[3] or packet.get('confirmed') is not True:
                                raise ValueError('Saved snapshot requires exact review and boolean True consent')
                            # Single-use: uncertainty is not an automatic retry grant.
                            self.saved_saves.pop(discard_review)
                            saved = await settled_image_work(lambda: store.save(saved_review[4], alive=lambda: (
                                save_alive() and saved_review[2] > time.monotonic())))
                            reply.update(review_id=discard_review, snapshot_id=saved.id, snapshot_sha256=saved.sha256)
                elif uses_discard:
                    from xdog.agent.supervision_transfer import settled_image_work
                    from xdog.tui.child_draft_store import ChildDraftStore
                    store = ChildDraftStore(self.session.session_manager._dir / 'saved-child-drafts')
                    def discard_alive() -> bool:
                        return (self._alive(owner, binding) and self.flags['child_saved_drafts']
                                and self.flags['child_saved_discard'] and self.saved_generation == saved_generation)
                    if not discard_alive():
                        raise ValueError('Saved discard owner revoked before preparation')
                    now = time.monotonic()
                    self.saved_discards = {review: value for review, value in self.saved_discards.items()
                                           if value[2] > now}
                    if action == 'saved_discard_prepare':
                        if self.discard_preparing + len(self.saved_discards) >= 4:
                            raise ValueError('Saved discard review capacity reached; cancel or let reviews expire')
                        assert isinstance(snapshot_id, str)
                        self.discard_preparing += 1
                        try:
                            saved = await settled_image_work(lambda: store.load(snapshot_id))
                        finally:
                            self.discard_preparing -= 1
                        if not discard_alive() or saved.sha256 != snapshot_sha256:
                            raise ValueError('Saved snapshot changed/revoked; no deletion')
                        review: dict[str, Any] = {'operation': 'delete_saved_snapshot', 'snapshot_id': saved.id,
                                  'snapshot_sha256': saved.sha256, 'label': sanitize_terminal_text(saved.label),
                                  'source_scope': sanitize_terminal_text(saved.source_scope),
                                  'source_action': saved.source_action, 'image_count': len(saved.draft.images),
                                  'image_bytes': sum(len(image.data) for image in saved.draft.images),
                                  'effect': 'Delete saved DISK snapshot only; editable drafts/queues/history unchanged'}
                        review_id = uuid.uuid4().hex
                        self.saved_discards[review_id] = self.lease, saved_generation, now + 30, review
                        reply.update(review_id=review_id, review=json.loads(json.dumps(review)))
                    else:
                        assert isinstance(discard_review, str)
                        prepared = self.saved_discards.get(discard_review)
                        if (prepared is None or prepared[0] != self.lease or prepared[1] != saved_generation
                                or prepared[2] <= time.monotonic()):
                            raise ValueError('Saved discard review expired/foreign/revoked; no deletion')
                        if action == 'saved_discard_cancel':
                            self.saved_discards.pop(discard_review)
                            reply.update(review_id=discard_review, canceled_review=True)
                        else:
                            review = prepared[3]
                            if packet.get('review') != review or packet.get('confirmed') is not True:
                                raise ValueError('Saved discard requires exact reviewed receipt '
                                                 'and explicit True consent')
                            # Single-use admission BEFORE the worker. Failed/uncertain
                            # deletion is never silently retried with the same grant.
                            self.saved_discards.pop(discard_review)
                            await settled_image_work(lambda: store.discard(
                                review['snapshot_id'], review['snapshot_sha256'], alive=lambda: (
                                    discard_alive() and prepared[2] > time.monotonic())))
                            reply.update(review_id=discard_review, discarded_snapshot_id=review['snapshot_id'],
                                         snapshot_sha256=review['snapshot_sha256'])
                elif uses_saved:
                    from xdog.agent.supervision_transfer import send_recovery, settled_image_work
                    from xdog.tui.child_draft_store import ChildDraftStore
                    store = ChildDraftStore(self.session.session_manager._dir / 'saved-child-drafts')
                    def saved_alive() -> bool:
                        return (self._alive(owner, binding) and self.flags['child_saved_drafts']
                                and self.saved_generation == saved_generation)
                    if not saved_alive():
                        raise ValueError('Saved draft access revoked before read; no fallback')
                    if action == 'saved_catalog':
                        rows = await settled_image_work(store.catalog)
                        if not saved_alive():
                            raise ValueError('Saved draft read owner changed')
                        reply['saved_catalog'] = [{**row, 'label': redact_sensitive_text(
                            sanitize_terminal_text(row['label'])), 'source_scope': redact_sensitive_text(
                            sanitize_terminal_text(row['source_scope']))} for row in rows]
                    else:
                        assert isinstance(snapshot_id, str)
                        saved = await settled_image_work(lambda: store.load(snapshot_id))
                        if not saved_alive() or saved.sha256 != snapshot_sha256:
                            raise ValueError('Saved snapshot changed/revoked; refresh catalog, no replay')
                        draft = saved.draft
                        if draft.images:
                            if not self.flags['child_saved_images']:
                                raise ValueError('Negotiate child_saved_images:1 independently for saved binary bodies')
                            import base64

                            from xdog.agent.input_queue import QueuedInput
                            from xdog.agent.supervision_transfer import prepare_recovery
                            def encode_images() -> tuple[ImageContent, ...]:
                                return tuple(ImageContent(data=base64.b64encode(image.data).decode('ascii'),
                                                          mime_type=image.mime_type) for image in draft.images)
                            images = await settled_image_work(encode_images)
                            payload = await prepare_recovery(QueuedInput(snapshot_id, draft.text, 'follow_up',
                                                                       images=images))
                            scope = {**self._envelope(), 'request_id': key, 'child_id': 'saved-draft',
                                     'input_id': snapshot_id, 'queue_revision': saved.sha256,
                                     'snapshot_id': snapshot_id, 'snapshot_sha256': saved.sha256}
                            def images_alive() -> bool:
                                return saved_alive() and self.flags['child_saved_images']
                            await send_recovery(payload, scope, self.send, images_alive)
                            if not images_alive():
                                raise ValueError('Saved image read revoked; no draft adoption')
                            reply['image_receipt'] = payload['receipt']
                        if not saved_alive():
                            raise ValueError('Saved draft owner changed before receipt')
                        reply['saved_draft'] = {'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256,
                            'label': sanitize_terminal_text(saved.label), 'source_scope': saved.source_scope,
                            'source_action': saved.source_action, 'source_slot': saved.source_slot,
                            'text': draft.text, 'image_count': len(draft.images), 'access': 'read-only',
                            'config': {'inherit_context': False}, 'uncertain': True}
                        # Reading returns a copy only; never installs parent/child history,
                        # queues work, deletes the file or adopts old model/tool policy.
                elif action == 'queue':
                    def return_alive() -> bool:
                        return (self._alive(owner, binding) and self.flags['child_image_return']
                                and self.flags['child_queues'] and self.return_lease == return_lease)
                    result = await owner.supervisor.control('queue', child, json.dumps(queue_request),
                        return_owner=return_lease if uses_return else '', return_alive=return_alive)
                    prepared_token = result.get('queue', {}).get('return_token', '')
                    if uses_return and not return_alive():
                        raise ValueError('Live image return owner revoked after work; inspect, no replay')
                    if 'image_recovery' in result:
                        if not uses_return and not self.flags['child_image_recovery']:
                            raise ValueError('Negotiate child_image_recovery:1 for initial task image bytes')
                        from xdog.agent.supervision_transfer import send_recovery
                        assert queue_request is not None
                        payload = result.pop('image_recovery')
                        scope = {**self._envelope(), 'request_id': key, 'child_id': child,
                                 'input_id': queue_request['id'], 'queue_revision': queue_request['revision']}
                        async def current() -> bool:
                            if uses_return:
                                return (return_alive() and await owner.supervisor.return_current(
                                    child, prepared_token, return_lease))
                            return (self._alive(owner, binding) and (not uses_task or self.flags['child_task_recovery'])
                                    and self.flags['child_image_recovery']
                                    and self.flags['child_queues']
                                    and owner.supervisor.recovery_current(child, queue_request['revision']))
                        await send_recovery(payload, scope, self.send, current)
                        if not await current():
                            raise ValueError('Child recovery owner changed before final receipt')
                    reply.update(child_id=child, **result)
                else:
                    result = await owner.supervisor.control(action, child, text, images=input_images)
                    if 'accepted_input' in result:
                        reply['accepted_input'] = result['accepted_input']
                    reply.update(child_id=child, catalog=self._catalog(owner))
                if not self._alive(owner, binding):
                    raise ValueError('Child owner/capability changed after admitted work')
                if uses_images and not self.flags['child_saved_images' if uses_save else 'child_images']:
                    raise ValueError('Child image capability changed after admitted work; inspect, no replay')
                if image_packet is not None:
                    assert self.images is not None
                    self.images.consume(image_packet)
                reply['success'] = True
            except asyncio.CancelledError:
                if admitted:
                    await owner.supervisor.control('cancel', admitted)
                    reply['child_id'] = admitted
                reply.update(canceled=True,
                             error='Child control canceled after settlement; effects uncertain, no replay')
            except (ValueError, RuntimeError, OSError):
                if admitted:
                    await owner.supervisor.control('cancel', admitted)
                reply['error'] = 'Child control unavailable/failed; no fallback or replay'
            finally:
                if uses_return and queue_request is not None and queue_request['action'] == 'return_prepare' \
                        and prepared_token and not reply['success']:
                    async def revoke() -> None:
                        try:
                            await owner.supervisor.control('queue', child, json.dumps({
                                'action': 'return_cancel', 'id': queue_request['id'],
                                'revision': queue_request['revision'], 'token': prepared_token}),
                                return_owner=return_lease)
                        except (ValueError, RuntimeError):
                            pass  # Consumed/stopped/expired lease cannot remove or restart input.
                    cleanup = asyncio.create_task(revoke())
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    cleanup.result()
                if action == 'start':
                    self.starting = False
            if not self.closed:
                self.emit(reply)
        job = asyncio.create_task(run_control(self.session, run, name="rpc-children"))
        self.jobs[key] = job, envelope, owner
        def done(completed: asyncio.Task[None]) -> None:
            self.jobs.pop(key, None)
            if completed.cancelled() and action == 'start':
                self.starting = False
            if completed.cancelled() and not begun[0] and not self.closed:
                self.emit({'type': 'child_result', **envelope, 'success': False, 'canceled': True,
                           'error': 'Child control canceled before admission'})
            if not completed.cancelled():
                completed.exception()
        job.add_done_callback(done)

    def cancel(self, packet: dict[str, Any]) -> None:
        key = packet.get('request_id')
        entry = self.jobs.get(key) if isinstance(key, str) else None
        if entry is None or any(packet.get(name) != entry[1][name] for name in
                ('session_id', 'session_revision', 'child_owner', 'child_epoch')):
            raise ValueError('Unknown/stale child control cancellation owner')
        if not entry[0].cancelling():
            entry[0].cancel()
        self.emit({'type': 'child_cancel_requested', **entry[1]})

    async def aclose(self) -> None:
        self.closed = True
        self.saved_discards.clear()
        self.saved_saves.clear()
        self._detach()
        for task, _scope, _owner in tuple(self.jobs.values()):
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*(item[0] for item in tuple(self.jobs.values())), return_exceptions=True)
        current = getattr(self.session, 'children', None)
        if current is not None and current not in self.owners:
            self.owners.append(current)
        for owner in self.owners:
            owner.abort()
            await owner.supervisor.aclose()
