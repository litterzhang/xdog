"""Owned subprocess extension host. stdout carries frames, never terminal escapes."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import inspect
import io
import json
import re
import sys
import types
import uuid
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from xdog.agent.extensions.api import ExtensionAPI, identifier, invoke
from xdog.agent.extensions.input import decision, input_text
from xdog.agent.extensions.tool_result import worker_result
from xdog.agent.extensions.transfer import ContextTransfer
from xdog.agent.extensions.types import ExtensionHook, PostToolUseHook, PreToolUseHook, StopHook
from xdog.agent.mcp.manager import _schema
from xdog.agent.mcp.stdio import CURRENT_VERSION, MAX_FRAME


class _Captured(io.TextIOBase):
    def __init__(self, api: ExtensionAPI) -> None:
        self.api = api
        self.remaining = 8000

    def write(self, value: str) -> int:
        if self.remaining > 0 and value.strip():
            text = value[:min(self.remaining, 800)]
            self.remaining -= len(text)
            try:
                self.api.notify(text)
            except ValueError:
                pass
        return len(value)

    def flush(self) -> None:
        pass


def manifest(path: Path, *, check_entry: bool = True) -> dict[str, Any]:
    file = path / "extension.yaml"
    if file.stat().st_size > 64 * 1024:
        raise ValueError("Extension manifest is too large")
    value = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("name", path.name) != path.name:
        raise ValueError("Extension manifest identity mismatch")
    if type(value.get("display_only", False)) is not bool:
        raise ValueError("display_only must be a strict boolean declaration")
    entry = value.get("entry_point", "main")
    if not isinstance(entry, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", entry):
        raise ValueError("Extension entry_point must be a local Python module name")
    if check_entry and not (path / f"{entry}.py").is_file():
        raise ValueError("Extension entry module is missing")
    dependencies = value.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(name, str) for name in dependencies):
        raise ValueError("Invalid extension dependencies")
    value["entry_point"] = entry
    return value


async def load(path: Path, settings: dict[str, Any]) -> tuple[ExtensionAPI, dict[str, Any]]:
    meta = manifest(path)
    api = ExtensionAPI(settings)
    schema = meta.get("settings_schema", {"type": "object"})
    Draft202012Validator(_schema(schema)).validate(settings)
    captured = _Captured(api)
    sys.stdout = sys.stderr = captured
    package = f"_xdog_extension_{uuid.uuid4().hex}"
    parent = types.ModuleType(package)
    parent.__path__ = [str(path)]
    sys.modules[package] = parent
    spec = importlib.util.spec_from_file_location(package + "." + meta["entry_point"],
                                                 path / f"{meta['entry_point']}.py")
    if spec is None or spec.loader is None:
        raise ValueError("Invalid extension entry module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    setup = getattr(module, "setup", None)
    if setup is not None:
        await invoke(setup, api)
    # Keep the previous Python hook contract usable, without duplicating imported
    # base classes/instances. New contributions use setup(api).
    found: set[int] = set()
    explicit_types = {type(value) for value in vars(module).values() if isinstance(value, ExtensionHook)}
    for value in vars(module).values():
        hook = value
        if isinstance(value, type) and issubclass(value, ExtensionHook) and not inspect.isabstract(value):
            if value.__module__ != module.__name__ or value in explicit_types:
                continue
            hook = value()
        if not isinstance(hook, ExtensionHook) or id(hook) in found:
            continue
        found.add(id(hook))
        if isinstance(hook, PreToolUseHook):
            async def pre(name: str, params: dict[str, Any], context: Any, h: PreToolUseHook = hook) -> Any:
                return await h.before_tool(name, params)
            api.before_tool(pre)
        if isinstance(hook, PostToolUseHook):
            async def post(name: str, params: dict[str, Any], result: str, context: Any,
                           h: PostToolUseHook = hook) -> Any:
                return await h.after_tool(name, params, result)
            api.after_tool(post)
        if isinstance(hook, StopHook):
            async def stop(context: Any, h: StopHook = hook) -> None:
                await h.on_stop()
            api.on("session_end", stop)
    return api, meta


async def dispatch(api: ExtensionAPI, method: str, params: dict[str, Any]) -> dict[str, Any]:
    context = params.get("context", {})
    if method == "server/discover":
        return {"supportedVersions": [CURRENT_VERSION], "capabilities": {"tools": {}}}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": value[0], "inputSchema": value[1]}
                          for name, value in api.tools.items()]}
    if method == "xdog/info":
        return {"commands": api.command_snapshot()["commands"],
                "events": api.hook_snapshot()["events"], "pre": bool(api.pre), "post": bool(api.post),
                "context": bool(api.context_hooks), "input": bool(api.input_hooks),
                "shortcuts": api.shortcuts,
                "providers": [info for info, _callback in api.providers.values()],
                "provider_revision": api._provider_revision, "hook_revision": api._hook_revision,
                "command_revision": api._command_revision, "tool_revision": api._tool_revision,
                "tool_inventory_cache": 1, "compaction_hooks": 1}
    if method == "tools/call":
        name = identifier(params["name"])
        if params.get("_xdog_tool_registration") != api._tool_ids.get(name):
            return {"isError": True, "content": [{"type": "text",
                "text": ("Extension tool registration changed before worker admission; "
                         "callback not invoked, no replay")}]}
        _description, schema, callback = api.tools[name]
        arguments = params.get("arguments", {})
        Draft202012Validator(schema).validate(arguments)
        result = await invoke(callback, arguments, context)
        return worker_result(result)
    if method == 'xdog/command_complete':
        from xdog.agent.extensions.argument_completion import argument_context, completion_id, items, plain
        name = identifier(params.get('name'))
        registered = api._command_completions.get(name)
        expected = completion_id(params.get('completion_id'))
        if registered is None or registered[0] != expected:
            return {'status': 'stale', 'items': []}
        callback = registered[1]
        entry = api.commands.get(name)
        prefix = plain(params.get('prefix'), 4096)
        completion_context = argument_context(params.get('argument_context'), prefix)
        value = await invoke(callback, prefix, {**context, 'argument_context': completion_context})
        if api.commands.get(name) is not entry or api._command_completions.get(name) != registered:
            return {'status': 'stale', 'items': []}
        return {'status': 'ok', 'items': items(value)}
    if method == "xdog/command":
        callback = api.commands[identifier(params["name"])][1]
        result = await invoke(callback, params.get("args", ""), context)
        if isinstance(result, str):
            if len(result.encode("utf-8")) > 64 * 1024:
                raise ValueError("Extension command text exceeds its limit")
            return {"output": result}
        from xdog.agent.extensions.command_result import normalize_command_result
        return {"xdog_command_result": 1, "command_content": normalize_command_result(result)}
    if method == "xdog/event":
        event = params["event"]
        for callback in tuple(api.events.get(event, [])):
            await invoke(callback, context)
        return {}
    if method == "xdog/compaction":
        from xdog.agent.extensions.compaction import CompactionDecision, wire
        from xdog.agent.extensions.compaction import decision as compaction_decision
        preparation = params['preparation']
        if not isinstance(preparation, dict) or not isinstance(preparation.get('messages'), list):
            raise ValueError('Invalid compaction preparation')
        selected = CompactionDecision()
        # A callback's edits affect its copy, never the preparation seen by a
        # sibling callback or the host's current canonical history.
        for callback in tuple(api.events.get('session_before_compact', [])):
            outcome = compaction_decision(await invoke(callback, {**copy.deepcopy(context),
                                                       'compaction': copy.deepcopy(preparation)}))
            if outcome.cancel:
                return {'decision': wire(outcome)}
            if outcome.summary is not None:
                if selected.summary is not None:
                    raise ValueError('Multiple custom compaction summaries conflict')
                selected = outcome
        return {'decision': wire(selected)}
    if method == "xdog/input":
        current = input_text(params["text"])
        metadata = params["metadata"]
        if (not isinstance(metadata, dict) or metadata.get("source") not in ("prompt", "steer", "follow_up")
                or not isinstance(metadata.get("images"), list) or len(metadata["images"]) > 64):
            raise ValueError("Invalid submitted input metadata")
        for callback in tuple(api.input_hooks):
            result = decision(await invoke(callback, current, metadata, context))
            if result.action in {"reject", "handled"}:
                return {"decision": result.to_dict()}
            if result.action == "transform":
                current = result.text
        return {"decision": {"action": "transform", "text": current}}
    if method == "xdog/context":
        messages = params["messages"]
        for callback in tuple(api.context_hooks):
            messages = await invoke(callback, messages, context)
            if not isinstance(messages, list):
                raise ValueError("Context callbacks must return a message list")
        return {"messages": messages}
    if method == "xdog/pre":
        current = params["arguments"]
        for callback in tuple(api.pre):
            current = await invoke(callback, params["name"], current, context)
            if current is None:
                return {"blocked": True}
            if not isinstance(current, dict):
                raise ValueError("Pre-tool hooks must return parameters or None")
        return {"arguments": current}
    if method == "xdog/post":
        result = params["text"]
        for callback in tuple(api.post):
            result = await invoke(callback, params["name"], params["arguments"], result, context)
            if not isinstance(result, str):
                raise ValueError("Post-tool hooks must return text")
        return {"text": result}
    raise ValueError("Unknown extension method")


async def main() -> None:
    path = Path(sys.argv[1]).resolve()
    output = sys.stdout.buffer
    # Capture even setup failures; they must not reach the host's terminal.
    placeholder = ExtensionAPI({})
    sys.stdout = sys.stderr = _Captured(placeholder)
    failure = ""
    api: ExtensionAPI | None = None
    transfer = ContextTransfer()
    from xdog.agent.extensions.provider_worker import ProviderCalls
    providers = ProviderCalls()
    from xdog.agent.extensions.tool_inventory import InventoryCache
    inventories = InventoryCache()
    sent_provider_revision = -1
    sent_hook_revision = -1
    sent_command_revision = -1
    sent_tool_revision = -1
    pending: dict[str, asyncio.Task[dict[str, Any]]] = {}
    context_calls: dict[str, str] = {}
    provider_auth_calls: set[str] = set()
    async def callback_result(call_id: str, task: asyncio.Task[dict[str, Any]]) -> dict[str, Any]:
        assert api is not None
        waiter = asyncio.create_task(api._dialog_events[call_id].wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED,
                               timeout=0.1 if call_id in provider_auth_calls else None)
            if task.done():
                provider_auth_calls.discard(call_id)
                pending.pop(call_id, None)
                api._dialog_events.pop(call_id, None)
                api._retired_dialogs.pop(call_id, None)
                result = task.result()
                if call_id in context_calls:
                    field = context_calls.pop(call_id)
                    return transfer.result(result[field])
                return result
            item = api._dialogs.get(call_id)
            if item is None and call_id in provider_auth_calls:
                return {'xdog_auth_pending': call_id}
            if item is None:
                return await callback_result(call_id, task)
            return {"xdog_pending": call_id, "xdog_prompt": item[0]}
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    while raw := await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1):
        if len(raw) > MAX_FRAME:
            return
        request = json.loads(raw)
        if "id" not in request:
            continue
        try:
            if api is None and not failure:
                try:
                    settings = request.get("params", {}).get("extension_settings", {})
                    api, _meta = await load(path, settings)
                except BaseException:
                    failure = "Extension setup failed; inspect its trusted source/settings"
                    api = placeholder
            if failure or api is None:
                raise ValueError(failure)
            result: dict[str, Any]
            method, params = request["method"], request.get("params", {})
            from xdog.agent.extensions.dialogs import REMOVED_HOST_CAPABILITIES
            from xdog.agent.extensions.inspection import envelope
            invocation = {key: value for key, value in params.get("context", {}).items()
                          if key not in REMOVED_HOST_CAPABILITIES}
            params = {**params, "context": invocation}
            session_id = str(invocation.get("session_id", ""))
            owner_id = str(invocation.get("child_id") or session_id)
            from xdog.agent.extensions.views import METHODS as view_methods
            api._has_views.set(method in view_methods and invocation.get('host_views') is True)
            from xdog.agent.extensions.pending_messages import METHODS as pending_methods
            api._has_pending_messages.set(method in pending_methods and invocation.get('host_pending_messages') is True)
            from xdog.agent.extensions.command_catalog import METHODS as menu_methods
            api._has_command_menu.set(method in menu_methods and invocation.get('host_command_menu') is True
                and invocation.get('has_ui') is True)
            api._command_menu_session.set(session_id)
            api._inspection.set(envelope(invocation.get("host_inspection"),
                                         session_id=session_id, owner_id=owner_id))
            from xdog.agent.session_name import name_snapshot
            api._session_name.set(name_snapshot(invocation.get('host_session_name'),
                                                session_id=session_id, owner_id=owner_id))
            from xdog.agent.extensions.tool_inventory import validate as validate_tools
            inventory_miss = None
            inventory = invocation.get('host_tool_inventory')
            reference = invocation.get('host_tool_inventory_ref')
            if inventory is not None:
                if reference is not None:
                    raise ValueError('Tool inventory cannot contain both inline data and a reference')
                inventory = validate_tools(inventory, session_id=session_id, owner_id=owner_id)
                inventories.remember(inventory)
            elif reference is not None:
                inventory = inventories.resolve(reference, session_id=session_id, owner_id=owner_id)
                if inventory is None:
                    inventory_miss = reference
            api._tool_inventory.set(inventory)
            if method == "xdog/ui_reply":
                call_id = params.get("call_id")
                if not isinstance(call_id, str) or call_id not in pending:
                    raise ValueError("Unknown extension callback owner")
                item = api._dialogs.get(call_id)
                if item is not None and item[0]["id"] == params.get("dialog_id"):
                    api._reply(call_id, params["dialog_id"], params.get("value"))
                elif params.get("dialog_id") not in api._retired_dialogs.get(call_id, ()):
                    raise ValueError("Unknown/foreign extension dialog owner")
                # A recognized expired question never answers the next question
                # sharing its callback. Return that actual pending prompt instead.
                result = await callback_result(call_id, pending[call_id])
            elif method == 'xdog/provider_auth_poll':
                call_id = params.get('call_id')
                if not isinstance(call_id, str) or call_id not in provider_auth_calls or call_id not in pending:
                    raise ValueError('Provider authentication poll owner changed')
                result = await callback_result(call_id, pending[call_id])
            elif method == 'xdog/provider_auth':
                if invocation.get('host_provider_auth') is not True:
                    raise ValueError('Provider authentication requires an explicitly reviewed host owner')
                call_id = uuid.uuid4().hex
                api._dialog_events[call_id] = asyncio.Event()
                api._call_id.set(call_id)
                api._has_ui.set(True)
                api._has_session.set(False)
                api._has_catalog.set(False)
                task = asyncio.create_task(providers.authenticate(api, params))
                provider_auth_calls.add(call_id)
                pending[call_id] = task
                result = await callback_result(call_id, task)
            elif method.startswith("xdog/provider_"):
                api._has_ui.set(False)
                api._has_session.set(False)
                api._has_catalog.set(False)
                result = await providers.request(api, method, params)
            elif method == "xdog/context_begin":
                transfer.begin(params)
                result = {}
            elif method == "xdog/context_chunk":
                transfer.chunk(params)
                result = {}
            elif method == "xdog/context_finish":
                transferred = transfer.finish(params)
                operation = params.get('operation', 'context')
                if operation not in {'context', 'compaction'}:
                    raise ValueError('Invalid owned context transfer operation')
                call_id = uuid.uuid4().hex
                api._dialog_events[call_id] = asyncio.Event()
                api._call_id.set(call_id)
                api._has_ui.set(params.get("context", {}).get("has_ui") is True)
                api._has_session.set(method == "xdog/command" and params.get("context", {}).get("host_session") is True)
                api._has_catalog.set(method == "xdog/command"
                                     and params.get("context", {}).get("host_catalog") is True)
                method_name = 'xdog/context' if operation == 'context' else 'xdog/compaction'
                field = 'messages' if operation == 'context' else 'preparation'
                task = asyncio.create_task(dispatch(api, method_name, {**params, field: transferred}))
                pending[call_id] = task
                context_calls[call_id] = 'messages' if operation == 'context' else 'decision'
                result = await callback_result(call_id, task)
            elif method == "xdog/context_read":
                result = transfer.read(params)
            elif method == "xdog/context_close":
                transfer.close()
                result = {}
            else:
                call_id = uuid.uuid4().hex
                api._dialog_events[call_id] = asyncio.Event()
                api._call_id.set(call_id)
                api._has_ui.set(params.get("context", {}).get("has_ui") is True)
                api._has_session.set(method == "xdog/command" and params.get("context", {}).get("host_session") is True)
                api._has_catalog.set(method == "xdog/command"
                                     and params.get("context", {}).get("host_catalog") is True)
                task = asyncio.create_task(dispatch(api, method, params))
                pending[call_id] = task
                result = await callback_result(call_id, task)
            if inventory_miss is not None:
                result['xdog_tool_inventory_miss'] = inventory_miss
            result["xdog_entries"] = api.drain_entries()
            result["xdog_ui"] = api.drain_ui()
            if api._provider_revision != sent_provider_revision:
                result["xdog_provider_snapshot"] = {
                    "revision": api._provider_revision,
                    "providers": [info for info, _callback in api.providers.values()],
                }
                sent_provider_revision = api._provider_revision
            if api._hook_revision != sent_hook_revision:
                result["xdog_hook_snapshot"] = api.hook_snapshot()
                sent_hook_revision = api._hook_revision
            if api._command_revision != sent_command_revision:
                result["xdog_command_snapshot"] = api.command_snapshot()
                sent_command_revision = api._command_revision
            if api._tool_revision != sent_tool_revision:
                result["xdog_tool_snapshot"] = api.tool_snapshot()
                sent_tool_revision = api._tool_revision
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except BaseException:
            transfer.close()
            response = {"jsonrpc": "2.0", "id": request["id"], "error": {
                "code": -32000, "message": failure or "Extension callback failed; effects uncertain, not replayed"}}
        data = (json.dumps(response, ensure_ascii=True) + "\n").encode()
        if len(data) > MAX_FRAME:
            return
        output.write(data)
        output.flush()
    transfer.close()
    await providers.close()
    for task in pending.values():
        task.cancel()
    await asyncio.gather(*pending.values(), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
