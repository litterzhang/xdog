"""Actual client startup/input under a private tmux server; no live services."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix" or not shutil.which("tmux"), reason="requires POSIX and tmux")


def _displayed_json_field(rendered, name, value):
    import re
    field = re.escape(json.dumps(name, ensure_ascii=False))
    scalar = re.escape(json.dumps(value, ensure_ascii=False))
    return re.search(field + r'\s*:\s*' + scalar + r'(?=\s*[,}\]])', rendered) is not None


class Terminal:
    def __init__(self, directory: Path):
        self.directory = directory
        self.server = "xdog-test-" + uuid.uuid4().hex
        self.raw = directory / "terminal.raw"
        self.child_env = {}
        self.submit_key = "Enter"
        self.env = dict(os.environ)
        for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            self.env[variable] = str(directory / variable.lower())
        self.call(
            "new-session", "-d", "-x", "80", "-y", "24", "-s", "test",
            "-c", str(directory), "bash --noprofile --norc",
        )
        self.call("pipe-pane", "-t", "test", "cat > " + shlex.quote(str(self.raw)))

    def call(self, *args):
        return subprocess.check_output(["tmux", "-L", self.server, *args], env=self.env, text=True, timeout=10)

    def text(self, value):
        self.call("send-keys", "-t", "test", "-l", value)

    def keys(self, *keys):
        self.call("send-keys", "-t", "test", *keys)

    def pane(self):
        return self.call("capture-pane", "-p", "-t", "test", "-S", "-1000")

    def visible(self):
        return self.call("capture-pane", "-p", "-t", "test")

    def wait(self, marker, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pane = self.visible()
            if marker in pane:
                return pane
            time.sleep(0.03)
        raise AssertionError(f"missing {marker!r} in terminal:\n{self.pane()}")

    def wait_json_field(self, name, value, timeout=10):
        """Match a typed visible JSON field across physical wrapping, never another scalar type."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rendered = self.visible()
            if _displayed_json_field(rendered, name, value):
                return rendered
            time.sleep(0.03)
        raise AssertionError(f'missing typed JSON field {name!r}={value!r}:\n{self.pane()}')

    def wait_wrapped_editor(self, marker):
        """Match editable data across displayed soft-wrap rows, not just one screen line."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.visible().splitlines()
            for index, row in enumerate(rows):
                if not row.startswith("> "):
                    continue
                text = row[2:]
                for following in rows[index + 1:]:
                    if not following.startswith("  "):
                        break
                    text += following[2:]
                if marker in text:
                    return
            time.sleep(0.03)
        raise AssertionError(f"missing wrapped editor data {marker!r}:\n{self.pane()}")

    def wait_editor(self, text):
        from wcwidth import wcswidth
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.visible().splitlines()
            x, y = map(int, self.call("display-message", "-p", "-t", "test",
                                     "#{cursor_x} #{cursor_y}").split())
            cursor_visible = self.call("display-message", "-p", "-t", "test", "#{cursor_flag}").strip() == "1"
            if cursor_visible and y < len(rows) and rows[y].endswith(text) and x == wcswidth(rows[y]):
                with (self.directory / "cursor-observations.txt").open("a") as output:
                    output.write(f"{x},{y}: {rows[y]}\n")
                return
            time.sleep(0.03)
        raise AssertionError(f"cursor did not reach end of {text!r}: {x},{y}\n{self.visible()}")

    def wait_cursor(self, visible):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            flag = self.call("display-message", "-p", "-t", "test", "#{cursor_flag}").strip()
            if flag == ("1" if visible else "0"):
                return
            time.sleep(0.03)
        raise AssertionError(f"cursor visibility did not become {visible}: {flag}")

    def review_oauth(self):
        import re
        deadline = time.monotonic() + 10
        previous = ""
        while time.monotonic() < deadline:
            pane = self.visible()
            if "Enter confirms reviewed endpoints/scopes" in pane:
                return
            match = re.search(r"Review (\d+-\d+/\d+)", pane)
            if match and match[1] != previous:
                previous = match[1]
                self.keys("NPage")
            time.sleep(0.03)
        raise AssertionError("OAuth fields did not finish visible review:\n" + self.pane())

    def review_output(self):
        """Advance only after each new metadata/path page is actually visible."""
        import re
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pane = self.visible()
            if 'Enter saves private export' in pane or 'Enter saves local HTML' in pane:
                return
            match = re.search(r'Review output (\d+-\d+/\d+)', pane)
            if match and match[1] != previous:
                previous = match[1]
                self.keys('NPage')
            time.sleep(0.03)
        raise AssertionError('Output fields did not finish visible review:\n' + self.visible())

    def review_child_start(self):
        """Page only AFTER the prior settings page was visibly painted."""
        import re
        deadline = time.monotonic() + 10
        previous = ""
        while time.monotonic() < deadline:
            pane = self.visible()
            if "Enter confirms reviewed settings" in pane:
                return
            match = re.search(r"Access: .* · (\d+-\d+/\d+)", pane)
            if match and match[1] != previous:
                previous = match[1]
                self.keys("NPage")
            time.sleep(0.03)
        raise AssertionError("Settings review did not complete:\n" + self.pane())

    def resize(self, width, height):
        offset = self.raw.stat().st_size
        self.call("resize-window", "-t", "test", "-x", str(width), "-y", str(height))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            output = self.raw.read_bytes()[offset:]
            query = output.find(b"\x1b[6n")
            if query >= 0 and b"\x1b[?2026l" in output[query:]:
                return
            time.sleep(0.03)
        raise AssertionError("resize did not query cursor and finish repainting")

    def start(self, kind, *args, expected=None):
        fixture = Path(__file__).parent / "fixtures" / "terminal_client.py"
        command = shlex.join([
            "env", *(f"{key}={value}" for key, value in self.child_env.items()),
            sys.executable, str(fixture), kind, *args,
        ])
        self.text("printf 'SHELL-BEFORE\\n'; " + command)
        self.keys("Enter")
        self.wait(expected or ("terminal-fixture" if kind == "coding" else "connected"))

    def finish(self, *, allow_fullscreen=False):
        self.keys("C-c")  # clear a draft, if any
        self.text("/quit")
        self.keys(self.submit_key)
        self.wait("TERMIOS-RESTORED=True")
        assert (self.directory / "termios-result.txt").read_text() == "True"
        raw = self.raw.read_bytes()
        assert b"\x1b[3J" not in raw
        if allow_fullscreen:
            assert raw.count(b"\x1b[?1049h") == raw.count(b"\x1b[?1049l") > 0
            import re
            alternate = False
            for control in re.findall(rb"\x1b\[\?1049[hl]|\x1b\[2J", raw):
                if control == b"\x1b[2J":
                    assert alternate, "fullscreen clear leaked into the native main buffer"
                elif control.endswith(b"h"):
                    assert not alternate
                    alternate = True
                else:
                    assert alternate
                    alternate = False
            assert not alternate
        else:
            assert b"\x1b[2J" not in raw
            assert b"\x1b[?1049h" not in raw
        assert b"\x1b[?2004l" in raw
        (self.directory / "pane.txt").write_text(self.pane())
        (self.directory / "visible-pane.txt").write_text(self.visible())

    def wait_hidden(self, marker):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if marker not in self.call("capture-pane", "-p", "-t", "test"):
                return
            time.sleep(0.03)
        raise AssertionError(f"{marker!r} stayed visible:\n{self.visible()}")


@pytest.fixture
def terminal(tmp_path):
    terminal = Terminal(tmp_path)
    try:
        yield terminal
    finally:
        terminal.call("kill-server")


def test_actual_coding_cli_permission_paste_resize_and_exit(terminal):
    terminal.start("coding")
    terminal.text("permission")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")
    terminal.wait_cursor(False)
    terminal.resize(36, 6)
    terminal.wait("Allow once")
    terminal.keys("Escape")
    terminal.wait("FIXTURE-ANSWER")
    terminal.resize(80, 24)
    terminal.call("set-buffer", "paste-one\npaste-two")
    terminal.call("paste-buffer", "-p", "-t", "test")
    terminal.wait("paste-two")
    terminal.wait_editor("paste-two")
    pane = terminal.pane()
    assert pane.count("paste-one") == 1
    assert pane.count("paste-two") == 1
    terminal.keys("C-z")
    terminal.wait("Stopped")
    terminal.text("fg")
    terminal.keys("Enter")
    terminal.wait("paste-two")
    terminal.wait_editor("paste-two")
    terminal.finish()


def test_actual_claw_tui_with_isolated_socket(terminal, tmp_path):
    # Controlled gateway responses exercise the production Unix socket client.
    (tmp_path / "review-target.py").touch()
    path = str(tmp_path / "gateway.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)
    server.settimeout(10)
    errors = []

    def serve():
        try:
            conn, _ = server.accept()
            with conn, conn.makefile("rwb") as stream:
                for line in stream:
                    request = json.loads(line)
                    kind = request["type"]
                    if kind == "ping":
                        events = [{
                            "type": "pong", "session_id": "fixture", "model": "terminal-fixture",
                            "history_format": 2, "workspace_dir": str(tmp_path),
                            "history": [{"role": "assistant", "content": [
                                {"type": "thinking", "thinking": "CLAW-REASONING-DETAIL"},
                                {"type": "text", "text": "previous answer"},
                            ]}],
                        }]
                    elif kind == "message":
                        events = [
                            {"type": "tool_call", "id": "call-1", "name": "bash", "arguments": {}},
                            {"type": "tool_result", "id": "call-1", "name": "bash",
                             "result": "\n".join(["first", *("line" for _ in range(30)), "DETAIL-TAIL"])},
                        ]
                    elif kind == "abort":
                        events = [{"type": "delta", "content": "LATE-CONTENT"}, {"type": "aborted"}]
                    else:
                        events = []
                    for event in events:
                        event["run_id"] = request.get("run_id", "")
                        stream.write((json.dumps(event) + "\n").encode())
                    stream.flush()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        terminal.start("claw", path)
        terminal.text("Read @review-ta")
        terminal.wait("@review-target.py")
        terminal.keys("Enter")
        terminal.wait_editor("Read @review-target.py")
        terminal.keys("C-l")
        terminal.wait_editor("Read @review-target.py")
        terminal.keys("C-c")
        terminal.wait("Thinking")
        assert "CLAW-REASONING-DETAIL" in terminal.visible()
        terminal.keys("C-o")
        terminal.wait("CLAW-REASONING-DETAIL")
        terminal.wait_cursor(False)
        terminal.keys("C-o")
        terminal.wait_hidden("Details focused")
        terminal.text("request")
        terminal.keys("Enter")
        terminal.wait("bash")
        terminal.keys("C-o")
        terminal.wait("Details focused")
        terminal.keys("End")
        terminal.wait("DETAIL-TAIL")
        terminal.resize(36, 10)
        terminal.wait("DETAIL-TAIL")
        terminal.resize(80, 24)
        terminal.keys("Escape")
        terminal.wait_hidden("DETAIL-TAIL")
        terminal.text("queued")
        terminal.keys("Enter")
        terminal.text("new-draft")
        terminal.keys("Escape")
        terminal.wait("run aborted")
        pane = terminal.pane()
        assert "new-draft" in pane
        assert "LATE-CONTENT" not in pane
        terminal.keys("C-z")
        terminal.wait("Stopped")
        terminal.text("fg")
        terminal.keys("Enter")
        terminal.wait("new-draft")
        terminal.wait_editor("new-draft")
        terminal.keys("C-l")
        terminal.wait_editor("new-draft")
        terminal.finish()
    finally:
        server.close()
    thread.join(timeout=2)
    assert not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_registered_argument_tab_enter_inserts_only_and_native_parent_restore(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete',
                    root / 'argcomplete')
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    argument_state = tmp_path / 'argument-state.json'
    terminal.child_env['XDOG_FIXTURE_ARGUMENT_STATE'] = str(argument_state)
    if kind == 'coding':
        home = tmp_path / 'arguments-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps(
            {'extensions': ['argcomplete'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('argcomplete',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            owned['server'] = server
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=(config.model, stream)):
                    loop.run_until_complete(server.start())
                ready.set()
                loop.run_forever()
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                loop.run_until_complete(server.stop())
                loop.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        assert ready.wait(6) and not errors
    def wait_argument_receipts(editor):
        # Painting an undo/End draft does not settle its newly issued callbacks.
        # Ctrl-C cancels admitted workers (and intentionally disables them), so
        # finish this insertion-only phase before clearing for the next one.
        nonce = uuid.uuid4().hex
        argument_state.with_suffix('.request').write_text(nonce)
        deadline, state = time.monotonic() + 6, {}
        while time.monotonic() < deadline:
            if argument_state.exists():
                state = json.loads(argument_state.read_text())
                if (state['nonce'] == nonce and state['connected'] and state['editor'] == editor
                        and not state['requests'] and state['inflight'] is None
                        and state['canceling'] is None and (editor != '' or state['queued'] is None)):
                    return
            time.sleep(.003)
        raise AssertionError('Original frontend argument receipt did not settle: ' + str(state))

    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-argcomplete-deploy')
        terminal.text('/ext-argcomplete-deploy a')
        terminal.wait('alpha')
        terminal.keys('Tab')
        terminal.wait_editor('/ext-argcomplete-deploy alpha')
        wait_argument_receipts('/ext-argcomplete-deploy alpha')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy b')
        terminal.wait('beta')
        terminal.keys('Enter')
        terminal.wait_editor('/ext-argcomplete-deploy beta')
        wait_argument_receipts('/ext-argcomplete-deploy beta')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy label')
        terminal.wait('🚀 发布候选 [release-1]')
        terminal.wait('🚀 发布候选 [release-2]')
        terminal.keys('Down', 'Enter')
        terminal.wait_editor('/ext-argcomplete-deploy release-2')
        wait_argument_receipts('/ext-argcomplete-deploy release-2')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy production en')
        terminal.wait('Endpoint for production')
        terminal.keys('Tab')
        terminal.wait_editor('/ext-argcomplete-deploy production endpoint-production')
        wait_argument_receipts('/ext-argcomplete-deploy production endpoint-production')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy staging en')
        terminal.wait('Endpoint for staging')
        terminal.keys('Enter')
        terminal.wait_editor('/ext-argcomplete-deploy staging endpoint-staging')
        wait_argument_receipts('/ext-argcomplete-deploy staging endpoint-staging')
        terminal.keys('C-c')
        for token, replacement in (
            (r'release\ canTAIL', r'release\ candidate'),
            ('"release canTAIL"', '"release candidate"'),
            ("'release\\ canTAIL'", "'release\\ candidate'"),
        ):
            original = '/ext-argcomplete-deploy literal ' + token + ' later'
            terminal.text(original)
            terminal.keys(*(['Left'] * (len(original) - (original.index('canTAIL') + 3))))
            terminal.wait('Literal complete token')
            terminal.keys('Tab', 'End')
            terminal.wait_editor('/ext-argcomplete-deploy literal ' + replacement + ' later')
            terminal.keys('M-z', 'End')
            terminal.wait_editor(original)
            wait_argument_receipts(original)
            terminal.keys('C-c')
        wait_argument_receipts('')
        terminal.text('/ext-argcomplete-counts')
        terminal.keys('Enter')
        terminal.wait('"executed": 0')
        terminal.resize(42, 10)
        terminal.resize(80, 24)
        terminal.text('argument editor parent recovered')
        terminal.wait_editor('argument editor parent recovered')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('rendered,expected', [
    ('{"terminal_generation": 1}', True),
    ('{"terminal_generation":\n  1}', True),
    ('{"terminal_generation":\n  10}', False),
    ('{"terminal_generation": "1"}', False),
    ('{"terminal_generation": true}', False),
    ('{"other_generation": 1}', False),
    ('{"description": "\\"terminal_generation\\": 1"}', False),
])
def test_visible_json_generation_assertion_handles_wrapping_without_scalar_coercion(rendered, expected):
    assert _displayed_json_field(rendered, 'terminal_generation', 1) is expected


def test_sessions_with_multiline_summaries_clear_after_arrow_navigation(terminal):
    from xdog.coding.core.session_manager import SessionManager

    directory = terminal.directory / 'coding-data'
    terminal.child_env['CODING_DIR'] = str(directory)
    manager = SessionManager(directory / 'sessions')
    for index in range(3):
        manager.create_session(model='terminal-fixture', working_dir=terminal.directory,
                               summary=f'NAV-ROW-{index}\ncontinuation\twith-tab')
    terminal.start('coding')
    terminal.text('/sessions')
    terminal.keys('Enter')
    terminal.wait('NAV-ROW-0 continuation with-tab')
    terminal.keys('Down', 'Down', 'Up', 'Up')
    terminal.keys('Escape')
    terminal.wait_hidden('Sessions ·')
    assert 'NAV-ROW-' not in terminal.pane(), 'Transient session rows leaked into the terminal or scrollback'
    terminal.finish()


def test_session_and_transcript_panels_keep_main_ui_colors_in_real_terminal(terminal):
    # The test runner may itself request plain output; opt this owned shell into colors.
    terminal.text('unset NO_COLOR')
    terminal.keys('Enter')
    terminal.child_env['TERM'] = 'xterm-256color'
    terminal.child_env['XDOG_TUI_THEME'] = 'dark'
    terminal.child_env['XDOG_TUI_COLOR'] = 'truecolor'
    terminal.start('coding')
    for command, heading in (('/sessions', 'Sessions ·'), ('/transcript', 'Transcript (inline)')):
        terminal.text(command)
        terminal.keys('Enter')
        terminal.wait(heading)
        colored = terminal.call('capture-pane', '-p', '-e', '-t', 'test')
        row = next(row for row in colored.splitlines() if heading in row)
        assert '\x1b[38;2;246;196;83m' in row, row
        terminal.keys('Escape')
        terminal.wait_hidden(heading)
    terminal.finish()
