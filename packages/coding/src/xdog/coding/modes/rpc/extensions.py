"""Opt-in owner-scoped extension dialogs/controls for the JSON-lines RPC client."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.dialogs import validate_answer
from xdog.agent.supervision import settle_on
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.sessions.operations import run_control
from xdog.coding.modes.rpc.custom_delivery import CustomDelivery

if TYPE_CHECKING:
    from xdog.coding.modes.rpc.navigation import RpcNavigation

CAPABILITIES = {'extension_message_images': 1,
                'extension_context_messages': 1, 'extension_context_delivery_modes': 1, 'extension_provider_auth': 1,
                'extension_controls': 1, 'extension_dialogs': 1, 'extension_forms': 1,
                'extension_multi_forms': 1, 'extension_editor': 1, 'extension_navigation': 1,
                'extension_session_name': 1, 'extension_session_catalog': 1, 'extension_command_catalog': 1,
                'extension_session_browser': 1, 'extension_active_tools': 1,
                'extension_active_tools_after_run': 1, 'extension_compaction': 1,
                'extension_compaction_after_run': 1, 'extension_name_after_run': 1,
                'extension_messages': 1, 'extension_queued_inputs': 1, 'extension_editor_write': 1,
                'extension_contributions': 1, 'extension_custom_views': 1, 'extension_overlay_controls': 1}


class RpcExtensions:
    def __init__(self, session: AgentSession, emit: Callable[[dict[str, Any]], None], *,
                 navigation: RpcNavigation | None = None,
                 send: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.session, self.emit = session, emit
        self.message_images_enabled = False
        self.message_images_generation = 0
        self._message_prompt_has_images = False
        from xdog.coding.modes.rpc.after_run_compaction import RpcAfterRunCompaction
        self.after_run_compaction = RpcAfterRunCompaction(self)
        from xdog.coding.modes.rpc.after_run_tools import RpcAfterRunTools
        self.after_run_tools = RpcAfterRunTools(self)
        from xdog.coding.modes.rpc.after_run_name import RpcAfterRunName
        self.after_run_name = RpcAfterRunName(self)
        from xdog.coding.modes.rpc.context_messages import RpcContextMessages
        self.context_messages = RpcContextMessages(self)
        from xdog.coding.modes.rpc.provider_auth import RpcProviderAuth
        self.provider_auth = RpcProviderAuth(self)
        from xdog.coding.modes.rpc.queued_inputs import RpcQueuedInputs
        self.queued_inputs = RpcQueuedInputs(self)
        self.send = send
        self.loop = asyncio.get_running_loop()
        self.custom_delivery = CustomDelivery(self.loop)
        self.dialogs_enabled = self.forms_enabled = self.multi_forms_enabled = self.editor_enabled = False
        from xdog.coding.modes.rpc.editor_write import RpcEditorWrites
        self.editor_writes = RpcEditorWrites(self)
        self.editor_revisions: dict[str, str] = {}
        self.editor_generation = 0
        self.name_generation = 0
        self.navigation_generation = 0
        self.navigation = navigation
        self.messages_enabled = False
        self.messages_generation = 0
        self.prompt_task: asyncio.Task[None] | None = None
        self.prompt_kind: str | None = None
        self.compaction_enabled = False
        self.compaction_generation = 0
        self.compaction_owner: tuple[str, str] | None = None
        self.browser_enabled = False
        self.browser_generation = 0
        self.deferred_browser: tuple[tuple[str, str, str], str, int, str] | None = None
        self.tools_enabled = False
        self.tools_generation = 0
        self.deferred_tools: tuple[tuple[str, str, str], str, int, int, tuple[str, ...] | None] | None = None
        self.menu_enabled = False
        self.menu_generation = 0
        self.catalog_enabled = False
        self.catalog_generation = 0
        self.name_enabled = False
        self.navigation_enabled = False
        self.deferred_navigation: tuple[tuple[str, str, str], str, dict[str, Any]] | None = None
        self.task: asyncio.Task[None] | None = None
        self.scope: tuple[str, str, str] | None = None
        self.batch_stream = False
        self.control: tuple[str, str] | None = None
        self.dialogs: dict[str, dict[str, Any]] = {}
        self.answered: set[str] = set()
        self.used: set[str] = set()
        self.closed = False
        self.handler = self._ask
        self.overlay_enabled = False
        self.custom_enabled = False
        self.custom_generation = 0
        self.custom_frames: dict[str, dict[str, Any]] = {}
        self.custom_sequences: dict[str, int] = {}
        self.contributions_enabled = False
        self.contributions_negotiated = False
        self.contributions_generation = 0
        self.contributions_sequence = 0
        self._contributions_pending = 0
        self._contributions_limited = False
        self._contributions_lock = threading.Lock()

    @property
    def ui_enabled(self) -> bool:
        return (self.after_run_name.enabled or self.after_run_tools.enabled or self.context_messages.enabled
                or self.provider_auth.enabled
                or self.queued_inputs.enabled
                or self.editor_writes.enabled
                or self.dialogs_enabled or self.editor_enabled
                or self.navigation_enabled or self.name_enabled
                or self.catalog_enabled or self.menu_enabled or self.browser_enabled
                or self.tools_enabled or self.compaction_enabled or self.messages_enabled or self.custom_enabled)

    @property
    def busy(self) -> bool:
        return (self.scope is not None or self.task is not None and not self.task.done()
                or self.prompting or self.after_run_tools.busy)

    @property
    def prompting(self) -> bool:
        return self.prompt_task is not None and not self.prompt_task.done()

    def abort_prompt(self) -> None:
        if self.prompt_task is not None and not self.prompt_task.done() and not self.prompt_task.cancelling():
            self.prompt_task.cancel()  # Includes pre-turn hooks/compaction, not just Agent streaming.

    def negotiate(self, packet: dict[str, Any]) -> None:
        # Preflight the whole capability batch before grants, revocation, or receipts.
        for key in CAPABILITIES:
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('RPC extension capabilities require exact integer 0 or 1')
        if 'extension_context_delivery_modes' in packet:
            self.context_messages.delivery_modes(type(packet['extension_context_delivery_modes']) is int
                                                 and packet['extension_context_delivery_modes'] == 1)
        if 'extension_context_messages' in packet:
            self.context_messages.negotiate(packet['extension_context_messages'] == 1)
        if 'extension_provider_auth' in packet:
            self.provider_auth.negotiate(packet['extension_provider_auth'] == 1)
        if 'extension_overlay_controls' in packet:
            value = packet['extension_overlay_controls']
            if type(value) is not int or value not in (0, 1):
                raise ValueError('Overlay controls require exact integer 0 or 1')
            self.overlay_enabled = value == 1
            self.custom_frames.clear()
            self.session.extension_dialogs.cancel_where(lambda dialog: bool(dialog.get('live_overlay')))
            if self.scope is not None:
                self.session.extensions.custom_control_handler = (self.session.extension_dialogs.control_custom
                    if self.overlay_enabled and self.custom_enabled else None)
            self.emit({'type': 'extension_overlay_capabilities',
                       'extension_overlay_controls': int(self.overlay_enabled),
                       'session_id': self.session.session_id})
            if not any(key in packet for key in CAPABILITIES if key != 'extension_overlay_controls'):
                return
        if 'extension_custom_views' in packet:
            value = packet['extension_custom_views']
            if type(value) is not int or value not in (0, 1):
                raise ValueError('Custom views require exact integer 0 or 1')
            self.custom_generation += 1
            self.custom_enabled = value == 1
            self.custom_frames.clear()
            self.session.extension_dialogs.cancel_where(lambda dialog: dialog['kind'] == 'custom')
            if self.scope is not None:
                runtime, broker = self.session.extensions, self.session.extension_dialogs
                runtime.dialog_handler = self.handler if self.ui_enabled else None
                runtime.custom_handler = self._ask_custom if self.custom_enabled else None
                runtime.custom_refresh_handler = broker.refresh_custom if self.custom_enabled else None
                runtime.custom_control_handler = (broker.control_custom
                    if self.custom_enabled and self.overlay_enabled else None)
            self.emit({'type': 'extension_custom_capabilities', 'extension_custom_views': int(self.custom_enabled),
                       'generation': self.custom_generation, 'session_id': self.session.session_id,
                       'max_width': 10000, 'max_height': 1000, 'max_frame_bytes': 128 * 1024,
                       'client_rendered': True})
            if not any(key in packet for key in CAPABILITIES
                       if key not in {'extension_custom_views', 'extension_contributions',
                                      'extension_overlay_controls'}):
                if 'extension_contributions' not in packet:
                    return
        if 'extension_contributions' in packet:
            value = packet['extension_contributions']
            if type(value) is not int or value not in (0, 1):
                raise ValueError('Extension contributions require exact integer 0 or 1')
            self.contributions_negotiated = True
            self.contributions_generation += 1
            self.contributions_enabled = value == 1
            self.emit({'type': 'extension_contribution_capabilities',
                       'extension_contributions': int(self.contributions_enabled),
                       'session_id': self.session.session_id, 'generation': self.contributions_generation,
                       'max_event_bytes': 64 * 1024, 'replay': False})
            if not any(key in packet for key in CAPABILITIES
                       if key not in {'extension_contributions', 'extension_custom_views',
                                      'extension_overlay_controls'}):
                return  # A contribution-only hello must not withdraw existing dialog/editor/navigation grants.
        for key in ('extension_dialogs', 'extension_forms', 'extension_multi_forms',
                    'extension_editor', 'extension_navigation', 'extension_session_name', 'extension_session_catalog',
                    'extension_command_catalog', 'extension_session_browser', 'extension_active_tools',
                    'extension_compaction'):
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('RPC extension UI capabilities require exact integer 0 or 1')
        if 'extension_editor_write' in packet:
            self.editor_writes.negotiate(packet['extension_editor_write'] == 1)
        if 'extension_queued_inputs' in packet:
            self.queued_inputs.negotiate(packet['extension_queued_inputs'] == 1)
        if 'extension_message_images' in packet:
            self.message_images_enabled = packet['extension_message_images'] == 1
            self.message_images_generation += 1
            if self.prompt_kind == 'message' and self._message_prompt_has_images:
                self.abort_prompt()
            self.session.extension_dialogs.cancel_where(
                lambda dialog: dialog.get('operation') == 'message' and bool(dialog.get('image_summaries')))
            if (self.deferred_navigation is not None and self.deferred_navigation[2].get('action') == 'message'
                    and self.deferred_navigation[2].get('images')):
                self.deferred_navigation = None
        if 'extension_messages' in packet:
            if self.prompt_kind == 'message':
                self.abort_prompt()  # This family cannot cancel a canonical custom continuation.
            self.messages_generation += 1
            self.messages_enabled = packet['extension_messages'] == 1 and self.navigation is not None
            if self.deferred_navigation is not None and self.deferred_navigation[2]['action'] == 'message':
                self.deferred_navigation = None
        if 'extension_name_after_run' in packet:
            self.after_run_name.negotiate(packet['extension_name_after_run'] == 1)
        if 'extension_compaction_after_run' in packet:
            self.after_run_compaction.negotiate(packet['extension_compaction_after_run'] == 1)
        if 'extension_compaction' in packet:
            if (self.compaction_owner is not None and self.navigation is not None
                    and self.navigation.owner == self.compaction_owner and self.navigation.compacting):
                self.navigation.cancel({'request_id': self.compaction_owner[0],
                                        'expected_session': self.compaction_owner[1]})
            self.compaction_owner = None
            self.compaction_generation += 1
            self.compaction_enabled = packet['extension_compaction'] == 1 and self.navigation is not None
            if self.deferred_navigation is not None and self.deferred_navigation[2]['action'] == 'compact':
                self.deferred_navigation = None
        if 'extension_active_tools_after_run' in packet:
            self.after_run_tools.negotiate(packet['extension_active_tools_after_run'] == 1)
        if 'extension_active_tools' in packet:
            self.tools_generation += 1
            self.tools_enabled = packet['extension_active_tools'] == 1 and self.navigation is not None
            self.deferred_tools = None
        if 'extension_session_browser' in packet:
            self.browser_generation += 1
            self.browser_enabled = packet['extension_session_browser'] == 1 and self.navigation is not None
            self.deferred_browser = None
        if 'extension_command_catalog' in packet:
            self.menu_generation += 1
            self.menu_enabled = packet['extension_command_catalog'] == 1
        if 'extension_session_catalog' in packet:
            self.catalog_generation += 1
            self.catalog_enabled = packet['extension_session_catalog'] == 1
        # New host families are independent delta negotiations. Preserve legacy
        # dialog/editor/navigation batch semantics for older hello packets.
        independent = {'extension_context_delivery_modes', 'extension_message_images',
                       'extension_name_after_run', 'extension_active_tools_after_run', 'extension_context_messages',
                       'extension_provider_auth',
                       'extension_command_catalog', 'extension_session_catalog',
                       'extension_session_name',
                       'extension_session_browser', 'extension_active_tools', 'extension_compaction',
                       'extension_messages', 'extension_editor_write', 'extension_queued_inputs'}
        delta = any(key in packet for key in independent) and not any(key in packet for key in (
            'extension_dialogs', 'extension_forms', 'extension_multi_forms', 'extension_editor',
            'extension_navigation'))
        default_dialogs = int(self.dialogs_enabled) if delta else 0
        default_forms = int(self.forms_enabled) if delta else 0
        default_multi = int(self.multi_forms_enabled) if delta else 0
        default_editor = int(self.editor_enabled) if delta else 0
        default_navigation = int(self.navigation_enabled) if delta else 0
        dialogs = packet.get('extension_dialogs', default_dialogs) == 1
        forms = dialogs and packet.get('extension_forms', default_forms) == 1
        multi = forms and packet.get('extension_multi_forms', default_multi) == 1
        self.dialogs_enabled, self.forms_enabled, self.multi_forms_enabled = dialogs, forms, multi
        editor_enabled = packet.get('extension_editor', default_editor) == 1
        navigation_enabled = (packet.get('extension_navigation', default_navigation) == 1
                              and self.navigation is not None)
        name_enabled = (packet.get('extension_session_name', int(self.name_enabled)) == 1
                        and self.navigation is not None)
        if editor_enabled != self.editor_enabled:
            self.editor_generation += 1
        if navigation_enabled != self.navigation_enabled:
            self.navigation_generation += 1
        if name_enabled != self.name_enabled:
            self.name_generation += 1
        self.editor_enabled = editor_enabled
        self.navigation_enabled = navigation_enabled
        self.name_enabled = name_enabled
        if (self.deferred_navigation is not None
                and not self._target_enabled(self.deferred_navigation[2])):
            self.deferred_navigation = None
        if not self.editor_enabled:
            self.editor_revisions.clear()
        if self.scope is not None:
            self.session.extensions.context_message_handler = (
                self.context_messages.ask if self.context_messages.enabled else None)
            self.session.extensions.queued_input_handler = (
                self.queued_inputs.ask if self.queued_inputs.enabled else None)
            self.session.extensions.command_menu_handler = self.handler if self.menu_enabled else None
            self.session.extensions.editor_write_handler = self.handler if self.editor_writes.enabled else None
            self.session.extensions.dialog_handler = self.handler if self.ui_enabled else None
            for key, value in tuple(self.dialogs.items()):
                if not self._supported(value):
                    self.answered.add(key)
                    self.session.extension_dialogs.resolve(key, None)
        self.emit({'type': 'rpc_capabilities', 'session_id': self.session.session_id,
                   'extension_context_messages': int(self.context_messages.enabled),
                   'extension_context_delivery_modes': int(self.context_messages.delivery_enabled),
                   'extension_provider_auth': int(self.provider_auth.enabled),
                   'extension_dialogs': int(dialogs), 'extension_forms': int(forms),
                   'extension_multi_forms': int(multi), 'extension_editor': int(self.editor_enabled),
                   'extension_editor_write': int(self.editor_writes.enabled),
                   'extension_editor_write_generation': self.editor_writes.generation,
                   'extension_controls': 1, 'extension_navigation': int(self.navigation_enabled),
                   'extension_session_name': int(self.name_enabled),
                   'extension_session_catalog': int(self.catalog_enabled),
                   'extension_command_catalog': int(self.menu_enabled),
                   'extension_session_browser': int(self.browser_enabled),
                   'browser_generation': self.browser_generation, 'extension_active_tools': int(self.tools_enabled),
                   'extension_active_tools_after_run': int(self.after_run_tools.enabled),
                   'extension_compaction': int(self.compaction_enabled),
                   'extension_compaction_after_run': int(self.after_run_compaction.enabled),
                   'extension_name_after_run': int(self.after_run_name.enabled),
                   'name_after_run_generation': self.after_run_name.generation,
                   'extension_messages': int(self.messages_enabled),
                   'extension_message_images': int(self.message_images_enabled),
                   'extension_queued_inputs': int(self.queued_inputs.enabled)})

    def _target_enabled(self, target: dict[str, Any]) -> bool:
        if target['action'] == 'message':
            return (self.messages_enabled and target.get('messages_generation') == self.messages_generation
                    and (not target.get('images') or self.message_images_enabled
                         and target.get('message_images_generation') == self.message_images_generation))
        if target['action'] == 'compact':
            return (self.compaction_enabled and target.get('compaction_generation') == self.compaction_generation)
        return self.name_enabled if target['action'] == 'rename' else self.navigation_enabled

    def _supported(self, dialog: dict[str, Any]) -> bool:
        if {'provider_auth_host', 'provider_auth_menu'} & dialog.keys():
            return self.provider_auth.owns(dialog)
        if dialog['kind'] == 'custom':
            return self.custom_enabled and (not dialog.get('live_overlay', False) or self.overlay_enabled)
        if dialog['kind'] == 'editor':
            return self.editor_writes.enabled if dialog.get('operation') == 'propose' else self.editor_enabled
        if dialog['kind'] == 'session' and dialog.get('operation') == 'context_message':
            return (self.context_messages.enabled and (dialog.get('delivery') == 'next_turn'
                                                       or self.context_messages.delivery_enabled))
        if dialog['kind'] == 'session' and dialog.get('operation') == 'queue_input':
            return self.queued_inputs.enabled
        if dialog['kind'] == 'session' and dialog.get('operation') == 'message':
            return self.messages_enabled
        if dialog['kind'] == 'session' and dialog.get('operation') == 'compact':
            return ((self.after_run_compaction.enabled
                     and dialog.get('compaction_after_run_generation') == self.after_run_compaction.generation
                     and self.session.extension_dialogs.contains(dialog['id']))
                    if dialog.get('after_run') is True else self.compaction_enabled)
        if dialog['kind'] == 'session' and dialog.get('operation') == 'select_tools':
            return ((self.after_run_tools.enabled
                     and dialog.get('active_tools_after_run_generation') == self.after_run_tools.generation)
                    if dialog.get('after_run') is True else self.tools_enabled)
        if dialog['kind'] == 'session' and dialog.get('operation') == 'commands':
            return self.menu_enabled
        if dialog['kind'] == 'session' and dialog.get('operation') == 'rename':
            return (self.after_run_name.enabled if dialog.get('name_after_run') is True else self.name_enabled)
        if dialog['kind'] == 'session' and dialog.get('operation') == 'navigate':
            return self.navigation_enabled
        if not self.dialogs_enabled or dialog['kind'] not in {'select', 'input', 'confirm', 'form'}:
            return False
        if dialog['kind'] != 'form':
            return True
        return (self.forms_enabled and (self.multi_forms_enabled
                or not any(field['kind'] == 'multi_choice' for field in dialog['fields'])))

    def _identity(self, request_id: Any, expected_session: Any) -> str:
        if (not isinstance(request_id, str) or not request_id or len(request_id.encode()) > 128
                or not isinstance(expected_session, str) or expected_session != self.session.session_id
                or request_id in self.used or len(self.used) >= 256 or self.closed):
            raise ValueError('Invalid/stale/reused RPC extension owner or request capacity')
        self.used.add(request_id)
        return request_id

    @asynccontextmanager
    async def bind(self, request_id: str) -> AsyncIterator[None]:
        if self.scope is not None:
            raise RuntimeError('Another RPC extension owner is active')
        session, extensions, broker = self.session, self.session.extensions, self.session.extension_dialogs
        captured = session.session_id, request_id, uuid.uuid4().hex
        previous = (extensions.dialog_handler, extensions.dialog_cancel, extensions.custom_handler,
                    extensions.custom_refresh_handler, extensions.custom_control_handler,
                    extensions.catalog_handler, extensions.publish, broker.publish, extensions.editor_write_handler,
                    extensions.command_menu_handler,
                    extensions.queued_input_handler, extensions.pending_messages)
        self.scope = captured
        previous_compaction = extensions.compaction_request_handler
        compaction_token = self.after_run_compaction.bind()
        self.after_run_tools.bind()
        previous_tools_request = extensions.active_tools_request_handler
        extensions.active_tools_request_handler = self.after_run_tools.ask if self.after_run_tools.enabled else None
        previous_name_request = extensions.name_request_handler
        self.after_run_name.bind()
        extensions.name_request_handler = self.after_run_name.ask if self.after_run_name.enabled else None
        extensions.compaction_request_handler = (self.after_run_compaction.ask
            if self.after_run_compaction.enabled else None)
        previous_context_handler = getattr(extensions, 'context_message_handler', None)
        extensions.context_message_handler = self.context_messages.ask if self.context_messages.enabled else None
        extensions.command_menu_handler = self.handler if self.menu_enabled else None
        async def pending_messages() -> bool | None:
            async def inspect() -> bool | None:
                if self.closed or self.scope != captured or session.session_id != captured[0]:
                    return None
                return session.agent.has_queued_messages()
            return await settle_on(self.loop, inspect())
        extensions.pending_messages = pending_messages
        previous_auth = session.provider_auth_handler
        auth_host = self.provider_auth.bind(captured)
        session.provider_auth_handler = self.provider_auth.command if self.provider_auth.enabled else None
        unsubscribe_inputs = session.agent.subscribe(self.queued_inputs.event)
        extensions.queued_input_handler = self.queued_inputs.ask if self.queued_inputs.enabled else None
        extensions.editor_write_handler = self.handler if self.editor_writes.enabled else None
        extensions.dialog_handler = self.handler if self.ui_enabled else None
        extensions.dialog_cancel = broker.cancel_extension
        extensions.custom_handler = self._ask_custom if self.custom_enabled else None
        extensions.custom_refresh_handler = broker.refresh_custom if self.custom_enabled else None
        extensions.custom_control_handler = (broker.control_custom
            if self.custom_enabled and self.overlay_enabled else None)
        extensions.catalog_handler = self._catalog  # Read-only command catalog, independently negotiated.
        extensions.publish = self._publish
        broker.publish = self._publish
        try:
            yield
            # Contributions queued during the final worker response must settle
            # while their invocation scope still exists, before its result receipt.
            while self._contributions_pending:
                await asyncio.sleep(0)
        finally:
            from xdog.coding.modes.rpc.after_run_compaction import _OUTCOME
            _OUTCOME.reset(compaction_token)
            extensions.compaction_request_handler = previous_compaction
            extensions.name_request_handler = previous_name_request
            extensions.context_message_handler = previous_context_handler
            extensions.active_tools_request_handler = previous_tools_request
            self.provider_auth.finish(auth_host)
            session.provider_auth_handler = previous_auth
            unsubscribe_inputs()
            self.queued_inputs.finish(captured)
            broker.cancel_all()
            self.dialogs.clear()
            self.custom_frames.clear()
            self.custom_sequences.clear()
            self.answered.clear()
            self.editor_revisions.clear()
            if self.scope == captured:
                self.scope = None
            (extensions.dialog_handler, extensions.dialog_cancel, extensions.custom_handler,
             extensions.custom_refresh_handler, extensions.custom_control_handler,
             extensions.catalog_handler, extensions.publish, broker.publish, extensions.editor_write_handler,
                    extensions.command_menu_handler,
             extensions.queued_input_handler, extensions.pending_messages) = previous

    async def _catalog(self, extension: str, request: dict[str, Any]) -> dict[str, Any] | None:
        from dataclasses import asdict

        from xdog.agent.extensions.catalog import page, validate_request
        from xdog.agent.supervision_transfer import settled_image_work
        from xdog.coding.core.navigation import navigation_entries
        captured, generation = self.scope, self.catalog_generation
        if captured is None:
            return None
        def alive() -> bool:
            return (not self.closed and self.catalog_enabled and self.scope == captured
                    and self.catalog_generation == generation and self.control == (captured[1], captured[0])
                    and self.session.session_id == captured[0] and self.navigation is not None)
        if not alive():
            return None
        validate_request(request)
        assert self.navigation is not None
        revision = self.navigation.revision
        scope = ('sessions' if request['scope'] == 'sessions' else
                 'tree|' + (request['session_id'] or captured[0]) + '|' + request['branch_id'])
        def read() -> dict[str, Any]:
            return page((asdict(row) for row in navigation_entries(self.session, scope)), request, captured[0])
        result = await settled_image_work(read)
        return result if alive() and self.navigation.revision == revision else None

    def _publish(self, event: dict[str, Any]) -> None:
        captured = self.scope
        if captured is None or self.closed:
            return
        if event.get('type') in {'extension_notice', 'extension_ui'} and self.contributions_negotiated:
            if not self.contributions_enabled:
                return  # Withdrawal cannot downgrade to an unnegotiated legacy disclosure.
            from xdog.coding.core.canonical_source import safe_json
            generation = self.contributions_generation
            payload = {'kind': event['type'], 'extension': event.get('extension', ''),
                       'text': safe_json(event.get('text', '')),
                       'ui': safe_json(event.get('snapshot', {}).get('ui', {}))}
            if len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()) > 32 * 1024:
                payload = {'kind': event['type'], 'limited': True}
            with self._contributions_lock:
                if self._contributions_pending >= 32:
                    self._contributions_limited = True
                    return
                self._contributions_pending += 1
            self.loop.call_soon_threadsafe(self._deliver_contribution, captured, generation, payload)
            return  # Opt-in contribution path replaces legacy duplicate UI publication.
        copied = json.loads(json.dumps(event, ensure_ascii=True, allow_nan=False))
        if event.get('type') in {'extension_custom_frame', 'extension_custom_error',
                                 'extension_custom_control', 'extension_dialog'}:
            copied['_custom_generation'] = self.custom_generation
        if event.get('type') == 'extension_custom_frame':
            size = len(json.dumps(copied, ensure_ascii=True, allow_nan=False).encode())
            if not self.custom_delivery.schedule(self._deliver, (captured, copied), size):
                identifier = event.get('id')
                if isinstance(identifier, str):
                    generation = self.custom_generation
                    self.custom_delivery.report_failure((*captured, identifier, str(generation)),
                        self._frame_overflow, (captured, generation, identifier))
                    self.session.extension_dialogs.resolve(identifier, None)
                return  # Cancel view on overflow, never deliver a silently incomplete interactive frame.
        else:
            self.loop.call_soon_threadsafe(self._deliver, captured, copied)

    def _frame_overflow(self, captured: tuple[str, str, str], generation: int, identifier: str) -> None:
        if (self.closed or self.scope != captured or self.session.session_id != captured[0]
                or not self.custom_enabled or self.custom_generation != generation):
            return
        self.custom_frames.pop(identifier, None)
        self.emit({'type': 'extension_custom_error', 'id': identifier, 'session_id': captured[0],
                   'request_id': captured[1], 'scope': captured[2], 'custom_generation': generation,
                   'code': 'frame_delivery_capacity', 'canceled': True,
                   'error': 'Custom frame delivery capacity exceeded; view canceled, no input replay'})

    def _deliver_contribution(self, captured: tuple[str, str, str], generation: int,
                              payload: dict[str, Any]) -> None:
        with self._contributions_lock:
            self._contributions_pending -= 1
            limited, self._contributions_limited = self._contributions_limited, False
        if (self.closed or self.scope != captured or self.session.session_id != captured[0]
                or not self.contributions_enabled or generation != self.contributions_generation):
            return
        self.contributions_sequence += 1
        self.emit({'type': 'extension_contribution', 'session_id': captured[0], 'request_id': captured[1],
                   'scope': captured[2], 'generation': generation, 'sequence': self.contributions_sequence,
                   'retained': False, 'data': payload, **({'limited': True} if limited else {})})

    def _deliver(self, captured: tuple[str, str, str], event: dict[str, Any]) -> None:
        if self.closed or self.scope != captured or self.session.session_id != captured[0]:
            return
        kind = event.get('type')
        if kind == 'extension_dialog':
            dialog = event['dialog']
            if (dialog.get('operation') == 'commands' and 'command_menu_generation' in dialog
                    and (dialog['command_menu_generation'] != self.menu_generation
                         or not self.session.extension_dialogs.contains(dialog['id']))):
                return
            if (dialog.get('operation') == 'propose' and (
                    dialog.get('editor_write_generation') != self.editor_writes.generation
                    or not self.session.extension_dialogs.contains(dialog['id']))):
                return
            if dialog['kind'] == 'custom' and (event.get('_custom_generation') != self.custom_generation
                    or not self.session.extension_dialogs.contains(dialog['id'])):
                return
            if not self._supported(dialog):
                self.session.extension_dialogs.resolve(dialog['id'], None)
                return
            if dialog.get('parent_id'):
                self.custom_frames.pop(dialog['parent_id'], None)
            self.dialogs[dialog['id']] = dialog
        elif kind in {'extension_custom_frame', 'extension_custom_error', 'extension_custom_control'}:
            identifier = event.get('id')
            if not isinstance(identifier, str):
                return
            dialog = self.dialogs.get(identifier)
            if (dialog is None or dialog['kind'] != 'custom' or not self.custom_enabled
                    or event.get('_custom_generation') != self.custom_generation
                    or not self.session.extension_dialogs.contains(identifier)):
                return
            if kind == 'extension_custom_control':
                if not self.overlay_enabled:
                    return
                dialog['overlay_state'] = dict(event['state'])
                self.custom_frames.pop(identifier, None)
                event['custom_generation'] = self.custom_generation
            if kind == 'extension_custom_frame':
                if event.get('superseded') is True:
                    return  # Obsolete render cannot restore a retired input receipt.
                from xdog.coding.modes.rpc.terminal_data import plain_custom_line
                event = {**event, 'lines': [plain_custom_line(line) for line in event['lines']],
                         'custom_generation': self.custom_generation}
        elif kind == 'extension_dialog_closed':
            self.custom_frames.pop(str(event.get('id', '')), None)
            self.custom_sequences.pop(str(event.get('id', '')), None)
            self.dialogs.pop(str(event.get('id', '')), None)
            self.answered.discard(str(event.get('id', '')))
        elif kind not in {'extension_notice', 'extension_ui'}:
            return
        event.pop('_custom_generation', None)
        try:
            self.emit({**event, **({'custom_generation': self.custom_generation}
                                 if kind == 'extension_dialog' and event['dialog']['kind'] == 'custom' else {}),
                       'session_id': captured[0], 'request_id': captured[1], 'scope': captured[2]})
        except OSError:
            if kind == 'extension_custom_frame':
                assert isinstance(identifier, str)
                self.custom_frames.pop(identifier, None)
                self.session.extension_dialogs.resolve(identifier, None)
                return  # Output refusal is not an interactive frame receipt.
            raise
        if kind == 'extension_custom_frame':
            assert isinstance(identifier, str)
            self.custom_frames[identifier] = event

    async def _ask_custom(self, extension: str, dialog: dict[str, Any], render: Any) -> Any:
        captured, generation = self.scope, self.custom_generation
        if captured is None or self.closed or not self._supported(dialog):
            return None
        async def owned_render(event: dict[str, Any]) -> dict[str, Any]:
            if (self.scope != captured or not self.custom_enabled or self.custom_generation != generation
                    or self.session.session_id != captured[0]):
                raise ValueError('Custom RPC invocation changed')
            result = await render(event)
            if (self.scope != captured or not self.custom_enabled or self.custom_generation != generation
                    or self.session.session_id != captured[0]):
                raise ValueError("Custom RPC invocation changed during render")
            return cast(dict[str, Any], result)
        return await self.session.extension_dialogs.ask_custom(extension, dialog, owned_render)

    def custom_input(self, packet: dict[str, Any]) -> None:
        captured = self.scope
        identifier = packet.get('id')
        if (captured is None or self.closed or not self.custom_enabled or not isinstance(identifier, str)
                or packet.get('session_id') != captured[0] or packet.get('request_id') != captured[1]
                or packet.get('scope') != captured[2]
                or type(packet.get('custom_generation')) is not int
                or packet['custom_generation'] != self.custom_generation):
            raise ValueError('Unknown/stale custom RPC owner')
        dialog = self.dialogs.get(identifier)
        if (dialog is None or dialog['kind'] != 'custom' or not self._supported(dialog)
                or not self.session.extension_dialogs.input_owner(identifier)):
            raise ValueError('Custom RPC view unavailable')
        if (self.session.permissions.has_pending_requests
                or any(manager.has_pending_requests for manager in self.session.children.permissions.values())):
            raise ValueError('Tool approval owns input; custom view cannot borrow it')
        sequence = packet.get('sequence')
        if (type(sequence) is not int or not 0 < sequence < 2**53
                or sequence <= self.custom_sequences.get(identifier, 0)):
            raise ValueError('Custom RPC input sequence reused/out of order')
        from xdog.agent.extensions.custom import event as validate_event
        event = validate_event(packet.get('event'))
        if event['kind'] != 'resize' and not self.session.extension_dialogs.custom_input_owner(identifier):
            raise ValueError('Hidden/non-capturing overlay cannot own interactive input')
        current = self.custom_frames.get(identifier)
        if event['kind'] != 'resize' and (current is None or event['painted'] != current['revision']
                or (event['width'], event['height']) != (current['width'], current['height'])
                or event.get('generation', 0) != current['generation']):
            raise ValueError('Custom RPC input requires the current delivered frame/geometry')
        if not self.session.extension_dialogs.request_custom(identifier, event):
            raise ValueError('Custom RPC input queue/owner unavailable')
        # Admission revokes the old painted receipt immediately; it cannot
        # authorize another key while this callback/resize is still rendering.
        self.custom_frames.pop(identifier, None)
        self.custom_sequences[identifier] = sequence
        self.emit({'type': 'extension_custom_input_ack', 'id': identifier, 'sequence': sequence,
                   'session_id': captured[0], 'request_id': captured[1], 'scope': captured[2]})

    async def _ask(self, extension: str, dialog: dict[str, Any]) -> Any:
        captured = self.scope
        if captured is None or self.closed or not self.ui_enabled:
            return validate_answer(dialog, None)
        if (dialog['kind'] == 'session' and dialog.get('operation') == 'rename'
                and dialog.get('name_after_run') is True):
            return await self.after_run_name.ask(extension, dialog)
        if dialog['kind'] == 'editor' and dialog.get('operation') == 'propose':
            return await self.editor_writes.ask(extension, dialog)
        if (dialog['kind'] == 'session' and dialog.get('operation') == 'select_tools'
                and dialog.get('after_run') is True):
            return await self.after_run_tools.ask(extension, dialog)
        if dialog['kind'] == 'session' and dialog.get('operation') in {'active_tools', 'select_tools'}:
            from xdog.agent.extensions.active_tools import names, snapshot
            agent = self.session.agent
            generation = self.tools_generation
            def alive() -> bool:
                return (not self.closed and self.scope == captured and self.tools_enabled
                    and self.tools_generation == generation and self.control == (captured[1], captured[0])
                    and self.session.session_id == captured[0] and self.session.agent is agent
                    and self.navigation is not None and not agent.is_streaming
                    and not self.session.children.supervisor.active)
            async def inspect_tools() -> dict[str, Any] | None:
                return snapshot(agent, captured[0]) if alive() else None
            current = await settle_on(self.loop, inspect_tools())
            if current is None:
                return None
            if dialog['operation'] == 'active_tools':
                return current
            selected = names(dialog['names'])
            if (dialog['revision'] != current['revision']
                    or selected is not None and not set(selected) <= set(current['configured'])
                    or self.deferred_tools is not None or self.deferred_navigation is not None
                    or self.deferred_browser is not None):
                return False
            assert self.navigation is not None
            revision = self.navigation.revision
            # Host snapshot replaces any caller-provided current state. Client
            # reviews exact configured/active/selected names before consenting.
            review = {**dialog, 'current_tools': current}
            approved = await self.session.extension_dialogs.ask(extension, review)
            if approved is not True:
                return False
            async def queue_tools() -> bool:
                if (not alive() or self.navigation is None or self.navigation.revision != revision
                        or agent.tool_selection_revision != current['revision']):
                    return False
                self.deferred_tools = captured, revision, generation, current['revision'], selected
                return True
            return await settle_on(self.loop, queue_tools())
        if dialog['kind'] == 'session' and dialog.get('operation') == 'browse':
            async def queue_browser() -> bool:
                if (not self.browser_enabled or self.closed or self.scope != captured
                        or self.control != (captured[1], captured[0]) or self.navigation is None
                        or self.session.session_id != captured[0] or self.deferred_browser is not None
                        or self.deferred_navigation is not None or self.deferred_tools is not None):
                    return False
                self.deferred_browser = captured, self.navigation.revision, self.browser_generation, dialog['scope']
                return True  # Queued request only, not a client-painted browser receipt.
            return await settle_on(self.loop, queue_browser())
        if dialog['kind'] == 'session' and dialog.get('operation') == 'commands':
            from xdog.agent.extensions.command_catalog import invocation_scope
            identity = invocation_scope(dialog.get('command_menu'))
            if identity is not None and (identity['session_id'] != captured[0] or identity['child_id']):
                return None
            generation = self.menu_generation
            def alive() -> bool:
                return (self.scope == captured and self.menu_enabled and not self.closed
                        and (identity is not None or self.control == (captured[1], captured[0]))
                        and self.session.session_id == captured[0] and self.menu_generation == generation)
            if not alive():
                return None
            result = await self.session.extension_dialogs.ask(extension,
                {**dialog, 'command_menu_generation': generation})
            if not alive() or result is not None and result['session_id'] != captured[0]:
                return None
            return result  # Client-provided real menu, never a guessed host terminal inventory.
        if dialog['kind'] == 'session' and dialog.get('operation') == 'message':
            import copy

            from xdog.agent.extensions.message_request import prepare
            from xdog.agent.supervision_transfer import settled_image_work
            from xdog.ai.utils.image_files import validate_references
            generation = self.messages_generation
            image_generation = self.message_images_generation
            profile, model = copy.deepcopy(self.session.model_info), self.session.model
            def admitted_message() -> bool:
                return (self.scope == captured and not self.closed and self.messages_enabled
                    and self.messages_generation == generation and self.navigation is not None
                    and self.control == (captured[1], captured[0]) and self.session.session_id == captured[0]
                    and self.deferred_navigation is None and self.deferred_browser is None
                    and self.deferred_tools is None and not self.session.children.supervisor.active
                    and not self.session.agent.is_streaming and not self.session.permissions.has_pending_requests
                    and (not dialog.get('images') or self.message_images_enabled
                         and self.message_images_generation == image_generation
                         and self.session.model_info == profile and self.session.model == model))
            if not admitted_message():
                return False
            assert self.navigation is not None
            revision = self.navigation.revision
            prepared = await settled_image_work(lambda: prepare(extension, dialog))
            await settled_image_work(lambda: validate_references(prepared.images, profile))
            if not admitted_message():
                return False
            reviewed = await self.session.extension_dialogs.ask(extension, prepared.review)
            async def queue_message() -> bool:
                if (reviewed is not True or not prepared.matches(prepared.review) or not admitted_message()
                        or self.navigation is None
                        or self.navigation.revision != revision):
                    return False
                self.deferred_navigation = captured, revision, {'action': 'message',
                    'message': prepared.content, 'images': prepared.images, 'messages_generation': generation,
                    'message_images_generation': image_generation, 'image_model': (model, profile)}
                return True
            return await settle_on(self.loop, queue_message())
        if (dialog['kind'] == 'session' and dialog.get('operation') == 'compact'
                and dialog.get('after_run') is True):
            return await self.after_run_compaction.ask(extension, dialog)
        if dialog['kind'] == 'session' and dialog.get('operation') == 'compact':
            generation = self.compaction_generation
            def admitted() -> bool:
                return (self.scope == captured and not self.closed and self.compaction_enabled
                    and self.compaction_generation == generation and self.navigation is not None
                    and self.control == (captured[1], captured[0]) and self.session.session_id == captured[0]
                    and self.deferred_navigation is None and self.deferred_browser is None
                    and self.deferred_tools is None and not self.session.children.supervisor.active
                    and not self.session.agent.is_streaming and not self.session.permissions.has_pending_requests)
            if not admitted():
                return False
            assert self.navigation is not None
            revision = self.navigation.revision
            reviewed = await self.session.extension_dialogs.ask(extension, dialog)
            async def queue_compaction() -> bool:
                if (reviewed is not True or not admitted() or self.navigation is None
                        or self.navigation.revision != revision):
                    return False
                self.deferred_navigation = captured, revision, {'action': 'compact',
                    'instructions': dialog['instructions'], 'compaction_generation': generation}
                return True
            return await settle_on(self.loop, queue_compaction())
        if dialog['kind'] == 'session' and dialog.get('operation') == 'rename':
            name_generation = self.name_generation
            async def admit_name() -> bool:
                return (self.scope == captured and self.name_enabled and self.name_generation == name_generation
                    and self.navigation is not None
                    and self.control == (captured[1], captured[0]) and self.deferred_navigation is None
                    and self.deferred_browser is None and self.deferred_tools is None
                    and self.session.session_id == captured[0]
                    and self.session.session_data.name == dialog['expected_name'])
            if not await settle_on(self.loop, admit_name()):
                return None
            assert self.navigation is not None
            revision = self.navigation.revision
            reviewed = await self.session.extension_dialogs.ask(extension, dialog)
            if reviewed is not True:
                return reviewed
            async def queue_name() -> bool:
                if (not await admit_name() or self.navigation is None or self.navigation.revision != revision):
                    return False
                self.deferred_navigation = captured, revision, {
                    'action': 'rename', 'name': dialog['name'], 'expected_name': dialog['expected_name']}
                return True
            return await settle_on(self.loop, queue_name())
        if dialog['kind'] == 'session' and dialog.get('operation') == 'navigate':
            navigation_generation = self.navigation_generation
            async def admit_navigation() -> bool:
                target = dialog['target']
                if (self.scope != captured or not self.navigation_enabled or self.navigation is None
                        or self.navigation_generation != navigation_generation
                        or self.control != (captured[1], captured[0]) or self.deferred_navigation is not None
                        or self.deferred_browser is not None or self.deferred_tools is not None):
                    return False
                if target['action'] == 'fork' and target['session_id'] and target['session_id'] != captured[0]:
                    return False  # Fork remains an explicit save of the current live head.
                if target['action'] in {'branch', 'goto'}:
                    from xdog.agent.extensions.runtime import _owned_disk
                    try:
                        await _owned_disk(self.session._checkpoint_candidate, target['session_id'] or captured[0],
                            target['branch_id'], target['index'] if target['action'] == 'goto' else None)
                    except (ValueError, RuntimeError, OSError):
                        return False
                    if (self.scope != captured or self.session.session_id != captured[0]
                            or not self.navigation_enabled or self.navigation_generation != navigation_generation
                            or self.control != (captured[1], captured[0])):
                        return False
                return True
            if not await settle_on(self.loop, admit_navigation()):
                return None
            assert self.navigation is not None
            revision = self.navigation.revision
            reviewed = await self.session.extension_dialogs.ask(extension, dialog)
            if reviewed is not True:
                return reviewed
            async def queue_reviewed() -> bool:
                if (not await admit_navigation() or self.navigation is None
                        or self.session.session_id != captured[0] or self.navigation.revision != revision):
                    return False
                self.deferred_navigation = captured, revision, json.loads(json.dumps(dialog['target']))
                return True
            # Only an actually completed broker future may queue a target. A
            # late transport answer/timeout race is not a navigation grant.
            return await settle_on(self.loop, queue_reviewed())
        if dialog['kind'] == 'session':
            async def inspect() -> Any:
                if self.scope != captured or self.session.session_id != captured[0]:
                    return None
                operation = dialog['operation']
                if operation == 'snapshot':
                    return {'session_id': captured[0], 'workspace': str(self.session.working_dir), 'group_id': ''}
                if operation in ('model', 'usage'):
                    return self.session._extension_inspection()[operation]
                if operation == 'name':
                    value = self.session._extension_session_name()
                    return {'session_id': captured[0], 'name': value['name']} if value is not None else None
                return None  # No remote response can queue hidden navigation/editor/name mutation.
            return await settle_on(self.loop, inspect())
        if not self._supported(dialog):
            return validate_answer(dialog, None)
        editor_generation = self.editor_generation
        if dialog['kind'] == 'editor':
            async def admit_editor() -> bool:
                if (self.scope != captured or not self.editor_enabled or self.editor_generation != editor_generation
                        or self.control != (captured[1], captured[0])):
                    return False  # Command-only, never tools/prompt/custom callbacks borrowing the parent editor.
                if dialog['operation'] == 'read':
                    self.editor_revisions.pop(extension, None)
                    return True
                revision = dialog.get('revision')
                expected = self.editor_revisions.pop(extension, None)
                return isinstance(revision, str) and expected is not None and expected == revision
            if not await settle_on(self.loop, admit_editor()):
                return ({'applied': False, 'reason': 'Captured RPC editor revision/owner unavailable'}
                        if dialog['operation'] == 'replace' else None)
        result = await self.session.extension_dialogs.ask(extension, dialog)
        if dialog['kind'] == 'editor' and dialog['operation'] == 'read' and isinstance(result, dict):
            async def remember() -> bool:
                if (self.scope != captured or not self.editor_enabled or self.session.session_id != captured[0]
                        or self.editor_generation != editor_generation):
                    return False
                self.editor_revisions[extension] = result['revision']
                return True
            if not await settle_on(self.loop, remember()):
                return None
        return result

    def answer(self, packet: dict[str, Any]) -> None:
        captured = self.scope
        key = packet.get('id')
        if (captured is None or not isinstance(key, str) or key not in self.dialogs or key in self.answered
                or packet.get('session_id') != captured[0] or packet.get('request_id') != captured[1]
                or packet.get('scope') != captured[2]):
            raise ValueError('Unknown/stale/foreign RPC extension dialog owner')
        dialog = self.dialogs[key]
        if {'provider_auth_host', 'provider_auth_menu'} & dialog.keys() and not self.provider_auth.owns(dialog):
            raise ValueError('Provider authentication capability/owner withdrawn')
        if packet.get('value') is not None and not self.session.extension_dialogs.input_owner(key):
            raise ValueError('Another live extension dialog owns input')
        if dialog['kind'] == 'custom' and packet.get('value') is not None:
            raise ValueError('Custom completion requires frame/input protocol, not dialog answers')
        value = validate_answer(dialog, packet.get('value'))
        if (dialog['kind'] == 'session' and dialog.get('operation') == 'commands'
                and value is not None and value['session_id'] != captured[0]):
            raise ValueError('Command catalog reply belongs to another session')
        if not self.session.extension_dialogs.resolve(key, value):
            raise ValueError('RPC extension dialog has already settled')
        self.answered.add(key)
        self.emit({'type': 'extension_dialog_ack', 'id': key, 'request_id': captured[1],
                   'session_id': captured[0], 'scope': captured[2]})

    def start(self, packet: dict[str, Any], *, turn_running: bool) -> None:
        if turn_running or self.busy or self.session.agent.is_streaming or self.session.children.supervisor.active:
            raise RuntimeError('RPC extension controls require an idle owning session')
        action, name, args = packet.get('action'), packet.get('name', ''), packet.get('args', '')
        if (not isinstance(action, str) or action not in {'list', 'reload', 'unload', 'command', 'auth'}
                or not isinstance(name, str) or len(name.encode()) > 80
                or not isinstance(args, str) or len(args.encode()) > 64 * 1024):
            raise ValueError('Invalid RPC extension control')
        if action == 'auth' and not self.provider_auth.enabled:
            raise ValueError('Provider authentication capability not negotiated')
        if action == 'auth' and (name or set(packet) - {
                'type', 'request_id', 'session_id', 'action', 'name', 'args'}):
            raise ValueError('Provider authentication uses canonical args only; extra fields refused')
        key = self._identity(packet.get('request_id'), packet.get('session_id'))
        captured_session = self.session.session_id
        self.control = key, captured_session
        admitted = [False]
        successful = [False]
        self.deferred_navigation = None
        self.deferred_browser = None
        self.deferred_tools = None
        async def run() -> None:
            admitted[0] = True
            reply: dict[str, Any] = {'type': 'extension_result', 'request_id': key,
                                    'session_id': self.session.session_id, 'success': False}
            try:
                async with self.bind(key):
                    output = await self.session.extension_command(name if action == 'command' else 'extensions',
                        args if action == 'command' else 'auth ' + args if action == 'auth'
                        else action + (' ' + name if name else ''))
                    reply.update(success=True, output=output.summary if isinstance(output, ExtensionCommandResult)
                                 else output)
                    if isinstance(output, ExtensionCommandResult):
                        reply['command_content'] = output.to_dict()
            except asyncio.CancelledError:
                reply.update(success=False, canceled=True,
                             error='Extension control canceled; effects uncertain, not replayed')
            except (ValueError, RuntimeError, OSError):
                reply.update(success=False, error='Extension control failed; effects uncertain, not replayed')
            finally:
                self.session.extensions.sync_tools(self.session.agent)
                reply['snapshot'] = self.session.extensions.snapshot()
            if not reply['success']:
                reply.pop('output', None)
                reply.pop('command_content', None)
            if self.closed:
                # Shutdown already owns this callback's cleanup. Starting another
                # delivery receipt behind an unread frame would prevent the bounded
                # output teardown from running. Deferred success stays false.
                return
            # A synchronous emitter may only enqueue output. Native RPC supplies
            # send(), whose receipt waits for the complete JSON line to be written.
            if self.send is None:
                self.emit(reply)  # Custom/embed sinks keep their synchronous contract.
            else:
                async with asyncio.timeout(10):
                    await self.send(reply)
            successful[0] = reply['success'] is True and not self.closed
        async def finish_tools() -> None:
            tools, self.deferred_tools = self.deferred_tools, None
            if tools is None:
                return
            captured, revision, generation, selection_revision, selected = tools
            canceled = threading.Event()
            def alive() -> bool:
                return (successful[0] and not canceled.is_set() and not self.closed
                        and self.task is not None and not self.task.cancelling()
                        and self.control == (key, captured_session)
                        and self.tools_enabled and self.tools_generation == generation
                        and captured[:2] == (captured_session, key) and self.session.session_id == captured_session
                        and self.navigation is not None and self.navigation.revision == revision
                        and not self.session.children.supervisor.active
                        and not self.session.permissions.has_pending_requests)
            applied = False
            try:
                if not alive():
                    raise ValueError('Tool selection owner changed')
                # Retain this command's busy/cancel owner until the actual disk
                # worker settles. Revocation is checked again immediately before
                # atomic replacement; an admitted commit is never rolled back.
                work = asyncio.create_task(asyncio.to_thread(self.session.select_active_tools, selected,
                    expected_revision=selection_revision, alive=alive))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    canceled.set()
                    while not work.done():
                        try:
                            await asyncio.shield(work)
                        except asyncio.CancelledError:
                            continue
                    work.result()
                applied = True
            except (asyncio.CancelledError, ValueError, RuntimeError, OSError):
                pass
            if not self.closed:
                self.emit({'type': 'extension_active_tools_result', 'request_id': key,
                           'session_id': captured_session, 'scope': captured[2], 'applied': applied,
                           'notice': 'Selection applied; permissions unchanged' if applied else
                                     'Selection not applied; owner/inventory changed or save failed, no replay'})
        async def owned_run() -> None:
            try:
                await run()
            finally:
                try:
                    await self.editor_writes.finish_command(successful[0])
                    await self.context_messages.finish_command(successful[0])
                finally:
                    await finish_tools()
        self.task = asyncio.create_task(run_control(self.session, owned_run, name="rpc-extensions"))
        def done(task: asyncio.Task[None]) -> None:
            if task.cancelled() and not admitted[0] and not self.closed:
                self.emit({'type': 'extension_result', 'request_id': key, 'session_id': captured_session,
                           'success': False, 'canceled': True, 'error': 'Canceled before worker admission'})
            if self.control == (key, captured_session):
                self.control = None
            self.context_messages.finish_trigger(successful[0] and not task.cancelled()
                and task.exception() is None and self.deferred_navigation is None)
            browser, self.deferred_browser = self.deferred_browser, None
            if browser is not None:
                captured, revision, generation, browser_scope = browser
                if (successful[0] and not task.cancelled() and task.exception() is None
                        and not self.closed and self.browser_enabled and self.browser_generation == generation
                        and captured[:2] == (captured_session, key) and self.session.session_id == captured_session
                        and self.navigation is not None and self.navigation.revision == revision):
                    self.emit({'type': 'extension_browser_requested', 'request_id': key,
                        'session_id': captured_session, 'session_revision': revision, 'scope': captured[2],
                        'browser_generation': generation, 'browser_scope': browser_scope,
                        'notice': 'Client browser request only; activation requires separate explicit user action'})
                elif not self.closed:
                    self.emit({'type': 'extension_browser_discarded', 'request_id': key,
                               'session_id': captured_session, 'reason': 'Callback/owner/capability changed'})
            queued, self.deferred_navigation = self.deferred_navigation, None
            if queued is not None:
                captured, revision, target = queued
                if (successful[0] and not task.cancelled() and task.exception() is None and not self.closed
                        and self._target_enabled(target)
                        and self.navigation is not None
                        and captured[:2] == (captured_session, key) and self.session.session_id == captured_session
                        and self.navigation.revision == revision):
                    if target['action'] == 'message':
                        self._start_message(target, captured, revision)
                        return
                    native_id = 'extension-navigation-' + uuid.uuid4().hex
                    request = {'action': target['action'], 'request_id': native_id,
                        'expected_session': captured_session, 'session_revision': revision,
                        'target_session': target.get('session_id', ''), 'branch_id': target.get('branch_id', ''),
                        'index': target.get('index', -1),
                        **({'name': target['name'], 'expected_name': target['expected_name']}
                           if target['action'] == 'rename' else {}),
                        **({'instructions': target['instructions']} if target['action'] == 'compact' else {})}
                    try:
                        self.navigation.start(request, turn_running=False, extension_running=False)
                        if target['action'] == 'compact':
                            self.compaction_owner = native_id, captured_session
                        self.emit({'type': 'extension_navigation_queued', 'request_id': key,
                            'session_id': captured_session, 'scope': captured[2], 'navigation_request_id': native_id,
                            'target': target})
                    except (ValueError, RuntimeError, OSError):
                        self.emit({'type': 'extension_navigation_discarded', 'request_id': key,
                                   'session_id': captured_session, 'reason': 'Target/native owner unavailable'})
                else:
                    self.emit({'type': 'extension_navigation_discarded', 'request_id': key,
                               'session_id': captured_session, 'reason': 'Callback/owner/capability changed'})
            if not task.cancelled():
                task.exception()
        self.task.add_done_callback(done)

    def _start_message(self, target: dict[str, Any], captured: tuple[str, str, str], revision: str) -> None:
        from xdog.coding.modes.rpc.rpc_mode import _handle_prompt
        prompt_id = 'extension-message-' + uuid.uuid4().hex
        self._message_prompt_has_images = bool(target.get('images'))
        self.prompt_kind = 'message'
        admitted = [False]
        async def run_message() -> None:
            admitted[0] = True
            canceled = False
            try:
                if (self.closed or not self._target_enabled(target) or self.session.session_id != captured[0]
                        or self.navigation is None or self.navigation.revision != revision
                        or self.session.children.supervisor.active or self.session.permissions.has_pending_requests
                        or self.session.agent.is_streaming):
                    return
                # Keep control=None: tool/lifecycle callbacks may use their own
                # dialogs but cannot borrow the completed command's mutation grant.
                if target.get('images') and target['image_model'] != (self.session.model, self.session.model_info):
                    return
                if self.batch_stream:
                    await _handle_prompt(self.session, target['message'], self.emit,
                        images=target.get('images', ()), input_origin='extension', batch_stream=True)
                else:
                    async with self.bind(prompt_id):
                        await _handle_prompt(self.session, target['message'], self.emit,
                                             images=target.get('images', ()), input_origin='extension')
            except asyncio.CancelledError:
                canceled = True
                raise
            finally:
                if not self.closed:
                    self.emit({'type': 'extension_message_result', 'request_id': captured[1],
                        'session_id': captured[0], 'prompt_request_id': prompt_id, 'settled': True,
                        'canceled': canceled,
                        'notice': 'Producer settled; consult ordinary message/error events; never replayed'})
        # The command-result receipt was fully delivered before this callback.
        # Queue receipt is emitted before task admission; sink failure cannot send.
        self.emit({'type': 'extension_message_queued', 'request_id': captured[1],
                   'session_id': captured[0], 'scope': captured[2], 'prompt_request_id': prompt_id})
        self.prompt_task = asyncio.create_task(run_message())
        def settled(task: asyncio.Task[None]) -> None:
            if self.prompt_task is task:
                self._message_prompt_has_images = False
            if task.cancelled() and not admitted[0] and not self.closed:
                self.emit({'type': 'extension_message_result', 'request_id': captured[1],
                    'session_id': captured[0], 'prompt_request_id': prompt_id, 'settled': True,
                    'canceled': True, 'notice': 'Canceled before producer admission; never replayed'})
            if not task.cancelled():
                task.exception()
        self.prompt_task.add_done_callback(settled)

    def cancel(self, packet: dict[str, Any]) -> None:
        captured = self.control
        if (captured is None or self.task is None or self.task.done()
                or packet.get('request_id') != captured[0] or packet.get('session_id') != captured[1]):
            raise ValueError('Unknown/stale RPC extension control owner')
        if not self.task.cancelling():
            self.task.cancel()

    async def aclose(self) -> None:
        self.closed = True
        await self.after_run_compaction.close()
        await self.after_run_tools.close()
        await self.after_run_name.close()
        self.abort_prompt()
        if self.prompt_task is not None:
            await asyncio.gather(self.prompt_task, return_exceptions=True)
        self.deferred_tools = None
        self.deferred_browser = None
        self.deferred_navigation = None
        if self.scope is not None:
            self.session.extension_dialogs.cancel_all()
        if self.task is not None:
            if not self.task.done():
                # A prior explicit cancel may already have been caught before the
                # task entered result delivery. Its historical cancelling count
                # does not mean this current await has a pending cancel signal.
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
