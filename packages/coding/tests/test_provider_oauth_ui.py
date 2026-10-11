"""Actual native /extensions auth and RPC ownership over real offline OAuth worker."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_extension_provider_oauth import ACCESS, CODE, PROVIDER, REFRESH
from packages.coding.tests.test_rpc_extensions import close, packet, response, session
from packages.tui.tests.test_extension_provider_oauth_panel import paint


async def until(predicate, ui=None):
    async with asyncio.timeout(8):
        while not predicate():
            if ui is not None:
                ui._poll()
            await asyncio.sleep(.003)


@pytest.mark.parametrize('end', ['accept', 'deny', 'cancel'])
async def test_native_exact_auth_reviews_masked_input_parent_unchanged_and_no_model(tmp_path, end, monkeypatch):
    owner, calls = session(tmp_path, extra=('provideroauth',))
    ui = InteractiveMode(owner)
    history, model, policy = owner.agent.messages, owner.model, owner.permissions.mode
    ui._editor.set_text('PARENT UNSENT DRAFT')
    panels, opened = [], []
    def open_url(url, current):
        assert current()
        opened.append((url, current))
    monkeypatch.setattr(ui._reviewed_url, 'start', open_url)
    try:
        ui._handle_slash_command('login', PROVIDER) if end == 'accept' else ui._handle_slash_command(
            'extensions', 'auth login ' + PROVIDER)
        for phase in ('consent', 'on_auth', 'on_prompt'):
            await until(lambda: isinstance(ui._extension_dialog_panel, ExtensionProviderAuth)
                        and ui._extension_dialog_panel.dialog['provider_auth']['phase'] == phase, ui)
            panel = ui._extension_dialog_panel
            panels.append(panel)
            assert panel.focused and owner._auth_changing and not calls
            if end != 'accept' and phase == ('consent' if end == 'deny' else 'on_prompt'):
                panel.handle_input(KeyEvent('escape'))
                break
            if phase == 'on_auth':
                panel.handle_input(KeyEvent('o'))
                assert not opened
            paint(panel)
            if phase == 'on_auth':
                panel.handle_input(KeyEvent('o'))
                panel.handle_input(KeyEvent('o'))
                assert len(opened) == 1
            panel.handle_input(KeyEvent('enter'))
            if phase == 'on_prompt':
                panel.handle_paste(CODE)
                assert CODE not in '\n'.join(panel.render(80))
                panel.handle_input(KeyEvent('enter'))
        await until(lambda: ui._command is None, ui)
        assert not owner._auth_changing and not calls
        assert owner.agent.messages is history and owner.model == model and owner.permissions.mode == policy
        assert ui._editor.get_text() == 'PARENT UNSENT DRAFT'
        public = '\n'.join(ui._chat_log.render(160)) + repr(owner.extensions.snapshot())
        public += repr([panel.dialog for panel in panels])
        assert all(secret not in public for secret in (ACCESS, REFRESH, CODE))
        assert all(panel._secret == '' for panel in panels)
        assert all(not current() for _url, current in opened)
        if end == 'accept':
            assert owner.extensions.provider_auth_catalog()[0]['authenticated']
            assert await owner.extensions.command('ext-provideroauth-count', '') == (
                'login=1;refresh=0;key=0;models=1;stream=0')
        elif end == 'deny':
            assert not owner.extensions.provider_auth_catalog()[0]['authenticated']
            assert await owner.extensions.command('ext-provideroauth-count', '') == (
                'login=0;refresh=0;key=0;models=0;stream=0')
        else:
            assert not owner.extensions.provider_auth_catalog()  # Uncertain canceled login worker quarantined.
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('end', ['accept', 'withdraw', 'cancel', 'storage'])
async def test_rpc_independent_auth_capability_scope_private_responses_and_no_native_storage(tmp_path, end):
    owner, calls = session(tmp_path, extra=('provideroauth',))
    events = []
    rpc = RpcExtensions(owner, events.append)
    history, model = owner.agent.messages, owner.model
    request = {**packet(owner, 'auth', '', action='auth'), 'args': 'login ' + PROVIDER}
    try:
        await owner.initialize_extensions()
        with pytest.raises(ValueError, match='not negotiated'):
            rpc.start(request, turn_running=False)
        with pytest.raises(ValueError):
            rpc.negotiate({'extension_provider_auth': True})
        rpc.negotiate({'extension_provider_auth': 1})
        assert not rpc.dialogs_enabled and rpc.provider_auth.enabled
        if end == 'storage':
            request['args'] = f'save {PROVIDER} secret-service {tmp_path}'
        rpc.start(request, turn_running=False)
        seen = set()
        async with asyncio.timeout(8):
            while rpc.task is not None and not rpc.task.done():
                reviews = [event for event in events if event['type'] == 'extension_dialog'
                           and event['dialog']['id'] not in seen]
                for event in reviews:
                    seen.add(event['dialog']['id'])
                    phase = event['dialog']['provider_auth']['phase']
                    assert owner._auth_changing
                    with pytest.raises(ValueError, match='foreign'):
                        rpc.answer(response(event, True, scope='foreign'))
                    if phase == 'on_prompt' and end == 'withdraw':
                        rpc.negotiate({'extension_provider_auth': 0})
                        with pytest.raises(ValueError):
                            rpc.answer(response(event, CODE))
                    elif phase == 'on_prompt' and end == 'cancel':
                        rpc.task.cancel()
                    else:
                        rpc.answer(response(event, CODE if phase == 'on_prompt' else True))
                await asyncio.sleep(.003)
        await rpc.task
        assert not owner._auth_changing and not calls and owner.agent.messages is history and owner.model == model
        assert all(secret not in json.dumps(events) for secret in (ACCESS, REFRESH, CODE))
        assert rpc.provider_auth.host is None and owner.provider_auth_handler is None
        result = next(event for event in reversed(events) if event['type'] == 'extension_result')
        assert result['success'] is (end == 'accept')
        if end == 'storage':
            assert not seen
    finally:
        await rpc.aclose()
        await close(owner)


async def test_actual_jsonl_rpc_reader_delivers_auth_reviews_and_never_echoes_private_code(tmp_path):
    import queue
    from unittest.mock import patch

    from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode
    owner, calls = session(tmp_path, extra=('provideroauth',))
    incoming, outgoing = queue.Queue(), []
    incoming.put({'type': 'rpc_hello', 'extension_provider_auth': 1})
    incoming.put({**packet(owner, 'actual-auth', '', action='auth'), 'args': 'login ' + PROVIDER})
    class Input:
        def readline(self, size=-1):
            return json.dumps(incoming.get(timeout=8)) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                event = json.loads(line)
                outgoing.append(event)
                if event['type'] == 'extension_dialog':
                    incoming.put(response(event, CODE if event['dialog']['kind'] == 'input' else True))
                elif event['type'] == 'extension_result':
                    incoming.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
                'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output()):
            async with asyncio.timeout(10):
                assert await run_rpc_mode(owner) == 0
        result = next(row for row in outgoing if row['type'] == 'extension_result')
        assert result['success'] is True and not calls
        assert all(secret not in json.dumps(outgoing) for secret in (ACCESS, REFRESH, CODE))
        assert [row['dialog']['provider_auth']['phase'] for row in outgoing if row['type'] == 'extension_dialog'] == [
            'consent', 'on_auth', 'on_prompt']
    finally:
        await close(owner)


async def test_rpc_provider_menu_is_independent_of_generic_dialog_grants_and_no_default_login(tmp_path):
    owner, calls = session(tmp_path, extra=('provideroauth',))
    events = []
    rpc = RpcExtensions(owner, events.append)
    try:
        rpc.negotiate({'extension_provider_auth': 1})
        rpc.start({**packet(owner, 'menu', '', action='auth'), 'args': 'menu'}, turn_running=False)
        await until(lambda: any(row['type'] == 'extension_dialog' for row in events))
        first = next(row for row in events if row['type'] == 'extension_dialog')
        assert first['dialog']['choices'][0] == 'Cancel' and not rpc.dialogs_enabled
        rpc.answer(response(first, PROVIDER))
        await until(lambda: sum(row['type'] == 'extension_dialog' for row in events) == 2)
        second = [row for row in events if row['type'] == 'extension_dialog'][1]
        assert second['dialog']['choices'] == ['Cancel', 'login', 'refresh', 'logout']
        rpc.answer(response(second, 'Cancel'))
        await rpc.task
        assert not calls and not owner.extensions.provider_auth_catalog()[0]['authenticated']
        assert await owner.extensions.command('ext-provideroauth-count', '') == (
            'login=0;refresh=0;key=0;models=0;stream=0')
    finally:
        await rpc.aclose()
        await close(owner)


def test_login_command_discovery_is_explicit_extension_provider_auth_not_native_provider_cli():
    from xdog.coding.core.slash_commands import BUILTIN_COMMANDS
    assert 'Extension-provider' in BUILTIN_COMMANDS['login']
    assert 'xdog-ai' in BUILTIN_COMMANDS['login']
    assert 'auth menu' in BUILTIN_COMMANDS['extensions']


@pytest.mark.parametrize('extra', [{'name': 'ext-other-provider'}, {'storage': {'backend': 'secret-service'}}])
async def test_rpc_auth_rejects_ambiguous_provider_name_or_extra_storage_fields_before_admission(tmp_path, extra):
    owner, calls = session(tmp_path, extra=('provideroauth',))
    events = []
    rpc = RpcExtensions(owner, events.append)
    try:
        rpc.negotiate({'extension_provider_auth': 1})
        with pytest.raises(ValueError, match='extra fields'):
            rpc.start({**packet(owner, 'bad', '', action='auth'), 'args': 'login ' + PROVIDER, **extra},
                      turn_running=False)
        assert rpc.task is None and not calls and not owner.extension_dialogs.has_pending
    finally:
        await rpc.aclose()
        await close(owner)
