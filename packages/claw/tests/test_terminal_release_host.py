"""Independent native Claw release negotiation selects only the original input owner."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.stdin_buffer import KeyBytes

from packages.agent.tests.test_extension_terminal_input import add_release_observer
from packages.claw.tests.test_extension_message_gateway import handshake, peer
from packages.claw.tests.test_extension_message_ui import message_server, native_ui


async def test_native_release_hello_opt_in_and_parent_observation(tmp_path):
    async with message_server(tmp_path) as (_, _, owner, config, calls):
        source = tmp_path / 'extensions/messagerequest/main.py'
        add_release_observer(source, tmp_path / 'observed-release')
        async with native_ui(config) as (ui, wait):
            ui._extension_command('extensions', '')
            await wait(lambda: ui._extension_pending is None)
            assert ui._terminal_release_supported and ui._terminal_release_available()
            ui._editor.set_text('draft')
            ui._editor._cursor = 2
            ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
            await wait(lambda: (tmp_path / 'observed-release').exists()
                       and ui._terminal_input.queue.pending == 0)
            assert '"kind": "release"' in (tmp_path / 'observed-release').read_text()
            assert ui._editor.get_text() == 'draft' and ui._editor._cursor == 2 and not calls
            assert ui._extension_snapshot['terminal_events'] == ['release']
            ui._queue_request({'type': 'ping', 'terminal_release_input': 0})
            await wait(lambda: not ui._terminal_release_supported)
            assert not ui._terminal_release_available() and ui._terminal_supported
            assert owner.extensions.terminal_events == ('release',)


@pytest.mark.parametrize('version', [None, True, '1', 0, 1])
async def test_socket_release_grant_is_default_off_and_independent(tmp_path, version):
    async with message_server(tmp_path) as (_, _, _owner, config, _calls):
        async with peer(config) as (send, receive, _writer):
            pong = await handshake(send, receive, terminal_input=1,
                **({'terminal_release_input': version} if version is not None else {}))
            assert pong['terminal_release_input'] == int(type(version) is int and version == 1)
            assert pong['terminal_input'] == 1 and pong['terminal_owner']
            old_owner = pong['terminal_owner']
            pong = await handshake(send, receive, terminal_release_input=0)
            assert not pong['terminal_release_input']
            assert (pong['terminal_owner'] != old_owner) is (type(version) is int and version == 1)


def test_child_release_selection_never_borrows_parent_registration():
    ui = ChatApp('/unused')
    ui._terminal_supported = ui._terminal_release_supported = ui._terminal_child_supported = True
    ui._extension_snapshot = {'terminal_input': True, 'terminal_events': ['release']}
    assert ui._terminal_release_available()
    child = 'a' * 12
    ui._child_panel = SimpleNamespace(terminal_child=child)
    ui._terminal_children = {child: {'active': True, 'generation': 1, 'events': ['key', 'paste']}}
    assert ui._terminal_available() and not ui._terminal_release_available()
    ui._accept_child_terminal_metadata({'child_terminal_owner': 'b' * 32,
        'terminal_children': {child: {'active': True, 'generation': 1, 'events': ['release']}}})
    assert ui._terminal_release_available()
    ui._terminal_release_supported = False
    assert not ui._terminal_release_available()
    ui._terminal_release_supported = True
    ui._accept_child_terminal_metadata({'child_terminal_owner': 'b' * 32,
        'terminal_children': {child: {'active': True, 'generation': 1}}})
    assert not ui._terminal_release_available()  # Legacy child registry never opts into release.
