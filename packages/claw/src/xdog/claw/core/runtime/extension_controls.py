"""Trusted extension controls on actual group/session resources; no gateway access."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.async_work import run_owned
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController
from xdog.claw.core.runtime.session import AgentSession

CommandMenuHost = tuple[Callable[[], bool], int]

def command_menu_handler(session: AgentSession, binding: CommandMenuHost
                         ) -> Callable[[str, dict[str, Any]], Awaitable[Any]]:
    runtime, session_id = session._runtime, session.meta.session_id
    def alive() -> bool:
        return (binding[0]() and runtime._active_session is session
            and runtime.get_new_session_id() == session_id)
    async def read(extension: str, dialog: dict[str, Any]) -> Any:
        from xdog.agent.extensions.command_catalog import invocation_scope
        identity = invocation_scope(dialog.get('command_menu'))
        if identity is None or identity['child_id'] or identity['session_id'] != session_id or not alive():
            return None
        revision = runtime._source_revision
        result = await session.extension_dialogs.ask(extension,
            {**dialog, 'command_menu_generation': binding[1]})
        return result if alive() and runtime._source_revision == revision else None
    return read


def pending_messages_reader(session: AgentSession) -> Callable[[], Awaitable[bool | None]]:
    loop, runtime, agent = asyncio.get_running_loop(), session._runtime, session.agent
    session_id = session.meta.session_id
    async def read() -> bool | None:
        from xdog.agent.cross_loop import settle_on
        async def inspect() -> bool | None:
            if (runtime._active_session is not session or session.agent is not agent
                    or runtime.get_new_session_id() != session_id or session.extensions._closed):
                return None
            return bool(agent.pending_inputs()) or agent.has_queued_messages()
        return await settle_on(loop, inspect())
    return read


async def manage_provider_auth(controller: GroupController, session: AgentSession, arguments: str,
                                publish: Callable[[dict[str, Any]], None] | None,
                               host_alive: Callable[[], bool] | None) -> dict[str, Any]:
    import json

    from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost
    if publish is None or host_alive is None or not host_alive():
        raise ValueError('Provider authentication needs an explicit original native viewer')
    await session.initialize_extensions()
    runtime, agent, sid = session._runtime, session.agent, session.meta.session_id
    revision = runtime._source_revision
    def alive() -> bool:
        return (host_alive() and runtime._active_session is session and session.agent is agent
                and runtime.get_new_session_id() == sid and runtime._source_revision == revision
                and not agent.is_streaming and not session.children.runtime.active and not session._compacting
                and not controller.context_changing)
    async def ask(extension: str, dialog: dict[str, Any]) -> Any:
        if not alive() or not (host.owns(dialog) or host.owns_menu(dialog)):
            raise ValueError('Foreign provider authentication view')
        return await session.extension_dialogs.ask(extension, dialog)
    host = ProviderAuthHost(session.extensions, ask, alive, native_storage=True)
    previous = session.extension_dialogs.publish
    session.extension_dialogs.publish = publish
    try:
        result = await host(arguments)
        if not alive():
            raise ValueError('Provider authentication owner changed; no replay')
        await session.initialize_extensions()
        model = session.extensions.provider_model(session.agent.state.model)
        return {'session_id': sid, 'snapshot': session.extensions.snapshot(), 'output': json.dumps(result),
                'notices': [], 'model': session.agent.state.model,
                'model_context_window': model.context_window if model is not None else session.agent.context_window}
    finally:
        host.revoke()
        session.extension_dialogs.publish = previous
        await session._persist_tool_retirement()


async def manage_extensions(runtime: GroupRuntime, controller: GroupController, action: str, name: str, args: str,
                            expected_session: str,
                            on_dialog: Callable[[dict[str, Any]], None] | None = None, *,
                            provider_auth_alive: Callable[[], bool] | None = None,
                            command_menu: CommandMenuHost | None = None,
                            input_service: Callable[[], Awaitable[InputService]],
                            ) -> dict[str, Any]:
    """Execute trusted extension commands with basic dialogs and read-only host queries."""
    runtime.require_current(expected_session)
    async with controller.operation(
            validate=lambda: runtime.require_current(expected_session)):
        session = await runtime.prepare_session(allow_reset=False)
        if action == 'command' and session.extensions.is_native_command(name):
            if (session.agent.is_streaming or session._compacting
                    or session._tool_selection_changing or session._tool_selection_retiring):
                raise RuntimeError('Native command requires an idle parent and settled context')
        else:
            session._children_idle()
        await session.initialize_extensions()
        if action == "auth":
            return await manage_provider_auth(controller, session, args, on_dialog, provider_auth_alive)
        notices: list[str] = []
        owner_session = session.meta.session_id
        original_agent, source_revision = session.agent, runtime._source_revision
        host_loop = asyncio.get_running_loop()
        def owned() -> bool:
            current = runtime._active_session
            return (current is not None and current is session and current.agent is original_agent
                and (runtime.get_new_session_id() or "") == owner_session
                and runtime._source_revision == source_revision and not controller.context_changing)
        def publish(event: dict[str, Any]) -> None:
            if event.get("type") == "extension_notice" and len(notices) < 8 and owned():
                notices.append(str(event.get("text", ""))[:4000])
        async def catalog(extension: str, request: dict[str, Any]) -> dict[str, Any] | None:
            from xdog.agent.extensions.catalog import page
            if (action != "command" or session.extensions.command_owner(name) != extension
                    or not owner_session or not owned()):
                return None
            scope = ("sessions" if request["scope"] == "sessions" else
                     "tree|" + (request["session_id"] or owner_session) + "|" + request["branch_id"])
            rows = await run_owned(runtime.navigation_entries, scope)
            result = await run_owned(page, rows, request, owner_session)
            return result if owned() else None
        async def host_dialog(extension: str, dialog: dict[str, Any]) -> Any:
            from xdog.agent.cross_loop import settle_on
            from xdog.agent.extensions.dialogs import validate_sdk_dialog
            validate_sdk_dialog(dialog)  # Retired writes cannot borrow this read-only bridge.
            if not owned() or session.extensions.command_owner(name) != extension:
                return None
            if dialog.get('kind') == 'session' and dialog.get('operation') == 'active_tools':
                from xdog.agent.extensions.active_tools import snapshot
                async def inspect() -> dict[str, Any] | None:
                    return snapshot(session.agent, owner_session) if owned() else None
                return await settle_on(host_loop, inspect())
            result = await session.extension_dialogs.ask(extension, dialog)
            return result if owned() else None
        previous_publish, previous_dialog_publish = session.extensions.publish, session.extension_dialogs.publish
        previous_dialog = session.extensions.dialog_handler
        previous_catalog = session.extensions.catalog_handler
        previous_menu = session.extensions.command_menu_handler
        previous_pending = session.extensions.pending_messages
        menu_handler = (command_menu_handler(session, command_menu)
                        if command_menu is not None and on_dialog is not None else None)
        session.extensions.publish = publish
        session.extension_dialogs.publish = on_dialog or (lambda event: None)
        session.extensions.dialog_handler = host_dialog if on_dialog is not None else None
        session.extensions.dialog_cancel = session.extension_dialogs.cancel_extension
        session.extensions.catalog_handler = catalog if action == "command" and on_dialog is not None else None
        session.extensions.command_menu_handler = menu_handler
        session.extensions.pending_messages = pending_messages_reader(session)
        previous_authority = session._user_operation_authorized
        try:
            await runtime.initialize_capabilities(await input_service())
            await session.initialize_extensions()
            if action == "command":
                session._user_operation_authorized = True
                try:
                    output = await session.extensions.command(name, args)
                finally:
                    session._user_operation_authorized = previous_authority
            else:
                await session.extensions.manage(action, name)
                output = ""
            await session.initialize_extensions()
            active_model = session.extensions.provider_model(session.agent.state.model)
            context_window = (active_model.context_window if active_model is not None else
                              0 if session.agent.state.model.startswith("ext-") else session.agent.context_window)
            payload: dict[str, Any] = {"session_id": runtime.get_new_session_id() or "",
                "snapshot": session.extensions.snapshot(), "output": output, "notices": notices,
                "model": session.agent.state.model, "model_context_window": context_window}
            if isinstance(output, ExtensionCommandResult):
                payload["output"] = output.summary
                payload["command_content"] = output.to_dict()
            return payload
        finally:
            session._user_operation_authorized = previous_authority
            session.extensions.pending_messages = previous_pending
            if session.extensions.command_menu_handler is menu_handler:
                session.extensions.command_menu_handler = previous_menu
            if session.extensions.catalog_handler is catalog:
                session.extensions.catalog_handler = previous_catalog
            if session.extensions.dialog_handler is host_dialog or on_dialog is None:
                session.extensions.dialog_handler = previous_dialog
            if session.extensions.publish is publish:
                session.extensions.publish = previous_publish
            session.extension_dialogs.publish = previous_dialog_publish
            await session._persist_tool_retirement()


async def complete_extension_argument(runtime: GroupRuntime, controller: GroupController, expected_session: str,
                                      command: str, prefix: str, identity: str,
                                      argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime.require_current(expected_session)
    if runtime._active_session is None or runtime._active_session.meta.session_id != expected_session:
        raise ValueError('Argument completion owner changed; no activation')
    async with controller.operation(
            validate=lambda: runtime.require_current(expected_session)):
        session = runtime._active_session
        if session is None or session.meta.session_id != expected_session:
            raise ValueError('Argument completion owner changed; no activation')
        session._children_idle()
        if session.agent.is_streaming:
            raise ValueError('Argument completion requires an idle current owner')
        try:
            result = await session.complete_command(command, prefix, identity, argument_input=argument_input)
            if runtime._active_session is not session or runtime.get_new_session_id() != expected_session:
                raise ValueError('Argument completion owner changed')
            return {**result, 'snapshot': session.extensions.snapshot()}
        finally:
            await session._persist_tool_retirement()
