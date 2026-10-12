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


def test_actual_coding_ctrl_o_shows_and_hides_reasoning(terminal):
    terminal.start("coding")
    terminal.text("reasoning")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-ANSWER")
    terminal.wait("Thinking")
    assert "FIXTURE-REASONING-DETAIL" in terminal.visible()
    terminal.text("/details")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-REASONING-DETAIL")
    terminal.wait_cursor(False)
    terminal.keys("C-o")
    terminal.wait_hidden("Details focused")
    terminal.text("preserved-draft")
    terminal.wait_editor("preserved-draft")
    terminal.keys("C-o")
    terminal.wait("FIXTURE-REASONING-DETAIL")
    terminal.keys("Escape")
    terminal.wait_hidden("Details focused")
    terminal.wait_editor("preserved-draft")
    terminal.finish()


def test_actual_coding_file_picker_preserves_draft_and_cursor(terminal):
    (terminal.directory / "review-target.py").touch()
    (terminal.directory / "src").mkdir()
    (terminal.directory / "src" / "hello world.py").touch()
    terminal.start("coding")
    terminal.text("Read @review-ta")
    terminal.wait("@review-target.py")
    terminal.keys("Enter")
    terminal.wait_editor("Read @review-target.py")
    assert "FIXTURE-ANSWER" not in terminal.visible()
    terminal.resize(36, 6)
    terminal.keys("C-l")
    terminal.wait_editor("Read @review-target.py")
    terminal.keys("C-c")
    terminal.text('Read @"src/hello ')
    terminal.wait('@"src/hello world.py"')
    terminal.keys("Tab")
    terminal.wait_editor('Read @"src/hello world.py"')
    assert "FIXTURE-ANSWER" not in terminal.visible()
    terminal.keys("Enter")
    terminal.wait("FIXTURE-ANSWER")
    terminal.resize(80, 24)
    terminal.finish()


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


def test_actual_coding_cli_queue_cancel_preserves_new_draft(terminal):
    terminal.start("coding")
    terminal.text("hold")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-HOLD")
    terminal.text("queued-message")
    terminal.keys("Enter")
    terminal.wait("queued 1")
    terminal.text("new-draft")
    terminal.keys("Escape")
    terminal.wait("restored 1 queued")
    terminal.wait_editor("new-draft")
    terminal.keys("C-l")
    terminal.wait_editor("new-draft")
    pane = terminal.visible()
    assert "queued-message" in pane and "new-draft" in pane
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


def test_coding_streaming_resize_latency_and_draft(terminal):
    terminal.start("coding")
    terminal.text("stress")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")
    terminal.keys("Enter")
    terminal.wait("running bash")
    terminal.text("retained-draft")
    started = time.monotonic()
    terminal.wait_editor("retained-draft")
    input_latency = time.monotonic() - started
    terminal.keys("C-o")
    terminal.wait("Details focused")
    terminal.keys("End")
    terminal.wait("STRESS-")
    measurements = []
    for width, height in ((44, 10), (100, 30), (60, 14), (80, 24)):
        started = time.monotonic()
        terminal.resize(width, height)
        terminal.wait("STRESS-")
        measurements.append(time.monotonic() - started)
    terminal.keys("Escape")
    terminal.wait_editor("retained-draft")
    terminal.wait("FIXTURE-ANSWER")
    terminal.wait("ready")
    terminal.keys("C-o")
    terminal.wait("Details focused")
    terminal.keys("End")
    terminal.wait("STRESS-119")
    terminal.keys("Escape")
    terminal.wait_editor("retained-draft")
    (terminal.directory / "latency.json").write_text(json.dumps({
        "draft_visible_seconds": input_latency,
        "resize_visible_seconds": measurements,
        "output_bytes": terminal.raw.stat().st_size,
    }, indent=2))
    terminal.finish()


@pytest.mark.parametrize("legacy", [False, True])
def test_coding_resume_restores_prompt_model_and_context(terminal, legacy):
    terminal.start("coding")
    terminal.text("PERSISTED-PROMPT")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-ANSWER")
    terminal.wait("ctx:2.2k/200k")
    terminal.finish()
    # Clear terminal history so assertions cannot match the previous process.
    terminal.call("clear-history", "-t", "test")
    terminal.text("printf '\\n%.0s' {1..30}")
    terminal.keys("Enter")
    terminal.wait_hidden("ctx:2.2k/200k")
    from xdog.coding.core.session_manager import SessionManager
    manager = SessionManager(Path(terminal.env["XDG_DATA_HOME"]) / "xdog/coding/sessions")
    original = manager.get_most_recent()
    assert original is not None
    if legacy:
        original.working_dir = ""
        manager.save_session(original)
    newer = manager.create_session(model="terminal-fixture", summary="NEWER-SESSION", working_dir=terminal.directory)
    other = manager.create_session(model="terminal-fixture", summary="OTHER-DIRECTORY",
                                   working_dir=terminal.directory / "other")
    terminal.start("coding", "-r")
    menu = terminal.wait("Resume session")
    assert newer.session_id[:8] in menu
    assert other.session_id[:8] not in menu
    if legacy:
        assert "Unknown dir" in menu
    terminal.keys("Down")
    terminal.keys("Enter")
    pane = terminal.wait("ctx:2.2k/200k")
    assert "PERSISTED-PROMPT" in pane
    assert "FIXTURE-ANSWER" in pane
    assert "terminal-fixture" in pane
    terminal.text("draft")
    terminal.wait_editor("draft")
    terminal.keys("C-u")
    terminal.keys("Up")
    terminal.wait_editor("PERSISTED-PROMPT")
    terminal.finish()
    restored = manager.load_session(original.session_id)
    assert restored is not None and restored.working_dir == str(terminal.directory.resolve())


def test_actual_claw_reconnect_preserves_draft_and_requires_explicit_resubmission(terminal, tmp_path):
    path = str(tmp_path / "reconnect.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(2)
    server.settimeout(10)
    disconnect = threading.Event()
    received = []
    errors = []

    def serve():
        try:
            for number in (1, 2):
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        received.append((number, request))
                        if request["type"] == "ping":
                            event = {"type": "pong", "session_id": "fixture", "history_count": 0}
                        elif number == 1 and request["type"] == "message":
                            event = {
                                "type": "tool_call", "name": "bash", "id": "reconnect-call",
                                "arguments": {"command": "printf TRANSPORT-WORK"}, "run_id": request["run_id"],
                            }
                            stream.write((json.dumps(event) + "\n").encode())
                            stream.flush()
                            if not disconnect.wait(10):
                                raise AssertionError("test did not request disconnect")
                            break
                        elif request["type"] == "message":
                            event = {"type": "response", "run_id": request["run_id"], "content": "RECONNECTED-ANSWER"}
                        else:
                            continue
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        terminal.start("claw", path)
        terminal.text("active request")
        terminal.keys("Enter")
        terminal.wait("TRANSPORT-WORK")
        terminal.text("queued request")
        terminal.keys("Enter")
        terminal.wait("queued 1")
        terminal.text("new-draft")
        terminal.wait_editor("new-draft")
        disconnect.set()
        terminal.wait("reconnecting")
        terminal.wait_editor("new-draft")
        pane = terminal.wait("connected | idle")
        assert "active request" in pane and "queued request" in pane and "new-draft" in pane
        assert [request["type"] for number, request in received if number == 2] == ["ping"]
        terminal.keys("Enter")
        terminal.wait("RECONNECTED-ANSWER")
        terminal.wait("connected | idle")
        messages = [request["content"] for _, request in received if request["type"] == "message"]
        assert messages == ["active request", "active request\n\nqueued request\n\nnew-draft"]
        assert all("_connection_generation" not in request for _, request in received)
        terminal.finish()
    finally:
        disconnect.set()
        server.close()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert not errors


def test_actual_claw_offline_draft_and_exit_during_reconnect(terminal, tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "terminal_client.py"
    command = shlex.join([sys.executable, str(fixture), "claw", str(tmp_path / "missing.sock")])
    terminal.text(command)
    terminal.keys("Enter")
    terminal.wait("reconnecting")
    terminal.text("offline draft")
    terminal.keys("Enter")
    terminal.wait("draft preserved")
    terminal.wait_editor("offline draft")
    terminal.finish()


def test_actual_coding_command_cancel_logging_and_preserved_draft(terminal):
    terminal.start("coding")
    terminal.text("/fixture-command")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-COMMAND-WARNING")
    terminal.wait("running /fixture-command")
    terminal.text("kept-draft")
    terminal.wait_editor("kept-draft")
    terminal.keys("Enter")
    terminal.wait("message kept in draft")
    terminal.keys("Escape")
    terminal.wait("Cancelled /fixture-command")
    terminal.wait("ready")
    terminal.wait_editor("kept-draft")
    assert "Traceback" not in terminal.pane()
    terminal.finish()


def test_actual_coding_clear_after_scrolling_keeps_editor_and_native_history(terminal):
    terminal.start("coding")
    terminal.text("long-history")
    terminal.keys("Enter")
    terminal.wait("LONG-HISTORY-99")
    terminal.wait("ready")
    terminal.text("/clear")
    terminal.keys("Enter")
    terminal.wait("Conversation cleared.")
    terminal.wait("ready")
    assert "LONG-HISTORY-0" in terminal.pane()  # Native scrollback remains intact.
    terminal.text("after-clear")
    terminal.wait_editor("after-clear")
    terminal.finish()


def test_actual_claw_clear_after_scrolled_history_keeps_editor(terminal, tmp_path):
    path = str(tmp_path / "clear.sock")
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
                    if json.loads(line)["type"] == "ping":
                        event = {
                            "type": "pong", "session_id": "fixture", "history_format": 1,
                            "history": [{
                                "role": "assistant",
                                "content": "\n".join(f"CLAW-HISTORY-{i}" for i in range(100)),
                            }],
                        }
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        terminal.start("claw", path)
        terminal.wait("CLAW-HISTORY-99")
        terminal.text("/clear")
        terminal.keys("Enter")
        terminal.wait_hidden("CLAW-HISTORY-99")
        assert "CLAW-HISTORY-0" in terminal.pane()
        terminal.text("after-clear")
        terminal.wait_editor("after-clear")
        terminal.finish()
    finally:
        server.close()
    worker.join(timeout=2)
    assert not errors
    assert not worker.is_alive()


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_custom_keys_and_external_editor_success_failure_interrupt(terminal, tmp_path, kind):
    config = tmp_path / "config"
    config.mkdir()
    (config / "keybindings.json").write_text(json.dumps({
        "send": "f2", "details": "ctrl+t", "external_editor": "f4",
    }))
    record = tmp_path / "editor-record.json"
    script = tmp_path / "editor.py"
    script.write_text("\n".join([
        "import sys, pathlib, json, termios, os",
        f"record = pathlib.Path({str(record)!r})",
        "records = json.loads(record.read_text()) if record.exists() else []",
        "path = pathlib.Path(sys.argv[-1])",
        "attrs = termios.tcgetattr(0)",
        "records.append({'path': str(path), 'mode': os.stat(path).st_mode & 0o777,",
        "                'cooked': bool(attrs[3] & termios.ICANON), 'text': path.read_text()})",
        "record.write_text(json.dumps(records))",
        "print(f'EXTERNAL-EDITOR-ROUND-{len(records)}', flush=True)",
        "action = input()",
        "if action == 'fail':",
        "    import tty; tty.setraw(0); sys.exit(7)",
        "path.write_text(path.read_text() + '\\nEDITED-DRAFT')",
    ]))
    terminal.child_env = {
        "CODING_DIR" if kind == "coding" else "CLAW_DIR": str(config),
        "VISUAL": shlex.join([sys.executable, str(script)]),
    }
    terminal.submit_key = "F2"
    received = []
    errors = []
    server = None
    thread = None
    if kind == "claw":
        path = str(tmp_path / "editor-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            event = {"type": "pong", "workspace_dir": str(tmp_path), "session_id": "fixture"}
                        elif request["type"] == "message":
                            received.append(request["content"])
                            event = {"type": "final", "content": "FIXTURE-ANSWER", "run_id": request["run_id"]}
                        else:
                            continue
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("original draft")
        terminal.wait_editor("original draft")
        terminal.keys("Enter")  # The replaced default must not submit.
        terminal.wait_editor("original draft")
        terminal.keys("F4")
        terminal.wait("EXTERNAL-EDITOR-ROUND-1")
        terminal.text("save")
        terminal.keys("Enter")  # Cooked input belongs to the external child only.
        terminal.wait_editor("EDITED-DRAFT")
        assert "FIXTURE-ANSWER" not in terminal.visible()
        assert received == []
        assert "EXTERNAL-EDITOR-ROUND-1" in terminal.pane()
        terminal.keys("F4")
        terminal.wait("EXTERNAL-EDITOR-ROUND-2")
        terminal.text("fail")
        terminal.keys("Enter")
        terminal.wait("status 7; draft preserved")
        terminal.wait_editor("EDITED-DRAFT")
        terminal.keys("F4")
        terminal.wait("EXTERNAL-EDITOR-ROUND-3")
        terminal.keys("C-c")
        terminal.wait("draft preserved")
        terminal.wait_editor("EDITED-DRAFT")
        terminal.resize(36, 8)
        terminal.keys("C-l")
        terminal.wait_editor("EDITED-DRAFT")
        terminal.resize(80, 24)
        records = json.loads(record.read_text())
        assert len(records) == 3
        assert all(entry["cooked"] and entry["mode"] == 0o600 for entry in records)
        assert all(not Path(entry["path"]).exists() for entry in records)
        assert records[0]["text"] == "original draft"
        assert records[1]["text"] == records[2]["text"] == "original draft\nEDITED-DRAFT"
        assert "FIXTURE-ANSWER" not in terminal.visible()
        assert received == []
        terminal.keys("F2")
        terminal.wait("FIXTURE-ANSWER")
        if kind == "claw":
            assert received == ["original draft\nEDITED-DRAFT"]
        terminal.finish()
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_ignore_aware_file_picker_uses_group_workspace(terminal, tmp_path, kind):
    if not shutil.which("git"):
        pytest.skip("requires Git ignore support")
    (tmp_path / ".gitignore").write_text("ignored-target.py\n")
    (tmp_path / "ignored-target.py").touch()
    (tmp_path / "allowed-target.py").touch()
    server = None
    thread = None
    errors = []
    if kind == "claw":
        path = str(tmp_path / "index-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        assert request["type"] == "ping"  # Selection must never send a turn.
                        stream.write((json.dumps({
                            "type": "pong", "workspace_dir": str(tmp_path), "session_id": "fixture",
                        }) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("Read @target")
        terminal.wait("@allowed-target.py")
        terminal.wait("Git ignore rules")
        assert "@ignored-target.py" not in terminal.visible()
        terminal.keys("Tab")
        terminal.wait_editor("Read @allowed-target.py")
        assert "FIXTURE-ANSWER" not in terminal.visible()
        terminal.resize(36, 6)
        terminal.keys("C-l")
        terminal.wait_editor("Read @allowed-target.py")
        assert not (tmp_path / ".git").exists()
        terminal.resize(80, 24)
        terminal.finish()
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_steering_follow_up_and_editable_queue_preserve_draft(terminal, tmp_path, kind):
    server = None
    thread = None
    errors = []
    requests = []
    if kind == "claw":
        path = str(tmp_path / "queue-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            pending = {}
            active = ""
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    def emit(event):
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()

                    for line in stream:
                        request = json.loads(line)
                        requests.append(request)
                        request_type = request["type"]
                        if request_type == "ping":
                            emit({"type": "pong", "session_id": "fixture", "input_controls": 1})
                        elif request_type == "message":
                            active = request["run_id"]
                            if request["content"] == "queue-hold":
                                emit({"type": "delta", "content": "QUEUE-HOLD", "run_id": active})
                            else:
                                assert request["content"] == "local-new"
                                emit({"type": "response", "content": "LOCAL-RESPONSE", "run_id": active})
                        elif request_type == "input_control":
                            key = request["input_id"]
                            action = request["action"]
                            if action == "add":
                                pending[key] = (request["content"], request["delivery"])
                            elif action == "edit":
                                pending[key] = (request["content"], pending[key][1])
                            elif action == "remove":
                                pending.pop(key)
                            emit({"type": "input_ack", "run_id": active, "request_id": request["request_id"],
                                  "success": True, "snapshot": [{"id": key, "delivery": value[1]}
                                                                 for key, value in pending.items()]})
                        elif request_type == "status":
                            if (tmp_path / "release-queue").exists():
                                for key, (content, delivery) in pending.items():
                                    emit({"type": "input_consumed", "run_id": active, "id": key,
                                          "content": content, "delivery": delivery})
                                    emit({"type": "delta", "run_id": active,
                                          "content": "STEER-RESPONSE" if delivery == "steer" else "FOLLOW-RESPONSE"})
                                pending.clear()
                                emit({"type": "final", "run_id": active})
                            emit({"type": "status", "pid": 1, "groups": ["main"]})
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("queue-hold")
        terminal.keys("Enter")
        terminal.wait("QUEUE-HOLD")
        for value in ("local-old", "LOCAL-REMOVED", "/steer steer-old", "/follow-up follow-old"):
            terminal.text(value)
            terminal.keys("Enter")
        terminal.wait("Input i2: add accepted")
        terminal.text("/queue")
        terminal.keys("Enter")
        terminal.wait("q1 [next_turn")
        for command in ("/queue edit q1 local-new", "/queue remove q2", "/queue edit i1 steer-new",
                        "/queue edit i2 follow-new"):
            terminal.text(command)
            terminal.keys("Enter")
        terminal.wait("Input i2: edit accepted")
        terminal.text("PRESERVED-DRAFT")
        terminal.wait_editor("PRESERVED-DRAFT")
        terminal.resize(36, 6)
        terminal.keys("C-l")
        terminal.wait_editor("PRESERVED-DRAFT")
        terminal.resize(80, 24)
        (tmp_path / "release-queue").touch()
        if kind == "claw":
            # Status releases the controlled gateway, not a model/user message.
            terminal.keys("C-c")
            terminal.text("/status")
            terminal.keys("Enter")
            terminal.text("PRESERVED-DRAFT")
        terminal.wait("STEER-RESPONSE")
        terminal.wait("FOLLOW-RESPONSE")
        terminal.wait("LOCAL-RESPONSE")
        terminal.wait_editor("PRESERVED-DRAFT")
        pane = terminal.pane()
        assert pane.count("steer-new") == 1
        assert pane.count("follow-new") == 1
        if kind == "claw":
            assert [request["content"] for request in requests if request["type"] == "message"] == [
                "queue-hold", "local-new",
            ]
        terminal.finish()
    finally:
        (tmp_path / "release-queue").touch(exist_ok=True)
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_repository_review_is_read_only_and_preserves_draft_focus(terminal, tmp_path, kind):
    if not shutil.which("git"):
        pytest.skip("requires Git")

    def git(*args):
        subprocess.run(["git", "--no-pager", "-c", "core.hooksPath=", "-C", str(tmp_path), *args],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    git("init", "-q")
    source = tmp_path / "tracked-review.py"
    source.write_text("BASE-LINE\n")
    git("add", source.name)
    git("-c", "user.name=xdog-test", "-c", "user.email=xdog-test@example.invalid", "-c", "commit.gpgSign=false",
        "commit", "-qm", "ephemeral terminal fixture")
    source.write_text("STAGED-LINE\n")
    git("add", source.name)
    source.write_text("WORKING-LINE\n")
    index = (tmp_path / ".git" / "index").read_bytes()
    server = None
    thread = None
    errors = []
    if kind == "claw":
        path = str(tmp_path / "review-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            event = {"type": "pong", "workspace_dir": str(tmp_path), "session_id": "fixture"}
                        elif request["type"] == "message":
                            assert request["content"] == "hold"
                            event = {"type": "delta", "run_id": request["run_id"], "content": "REVIEW-HOLD"}
                        elif request["type"] == "abort":
                            event = {"type": "aborted", "run_id": request["run_id"]}
                        else:
                            raise AssertionError(f"Review sent an unexpected gateway request: {request}")
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("review-permission" if kind == "coding" else "hold")
        terminal.keys("Enter")
        terminal.wait("REVIEW-HOLD")
        terminal.text("PRESERVED-REVIEW-DRAFT")
        terminal.wait_editor("PRESERVED-REVIEW-DRAFT")
        terminal.keys("M-d")
        terminal.wait("Review · working")
        terminal.wait_cursor(False)
        terminal.text("/tracked-review.py")
        terminal.keys("Enter")
        terminal.wait("+WORKING-LINE")
        terminal.text("s")
        terminal.wait("Review · staged")
        terminal.wait("+STAGED-LINE")
        terminal.text("w")
        terminal.wait("+WORKING-LINE")
        terminal.text("n")
        terminal.resize(36, 7)
        terminal.keys("C-l")
        terminal.wait("Review")
        terminal.wait_cursor(False)
        terminal.resize(80, 24)
        if kind == "coding":
            (tmp_path / "release-review").touch()
            terminal.wait("Tool permission required")
            terminal.keys("Escape")
            terminal.wait_hidden("Tool permission required")
            terminal.wait("Review · working")
            terminal.wait_cursor(False)
            terminal.wait("FIXTURE-ANSWER")
        terminal.keys("Escape")
        terminal.wait_editor("PRESERVED-REVIEW-DRAFT")
        assert source.read_text() == "WORKING-LINE\n"
        assert (tmp_path / ".git" / "index").read_bytes() == index
        terminal.finish()
    finally:
        (tmp_path / "release-review").touch(exist_ok=True)
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_session_browser_tree_restore_and_return_checkpoint_preserve_draft(terminal, tmp_path, kind):
    server = None
    thread = None
    errors = []
    if kind == "coding":
        terminal.child_env["XDOG_FIXTURE_SESSIONS"] = "1"
    else:
        path = str(tmp_path / "navigation-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            active = "first-session"
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    def emit(event):
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()

                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            emit({"type": "pong", "session_id": active, "session_controls": 1})
                            continue
                        assert request["type"] == "session_control"
                        request_id = request["request_id"]
                        if request["action"] == "list":
                            entries = [{"id": "target-session", "title": "NAV-TARGET", "kind": "session",
                                        "session_id": "target-session"}]
                            emit({"type": "session_result", "request_id": request_id, "success": True,
                                  "scope": "sessions", "total": 1, "entries": entries})
                        elif request["action"] == "tree":
                            entries = [{"id": "branch", "title": "NAV-BRANCH" if active == "first-session" else
                                        "Return checkpoint before navigation", "kind": "branch",
                                        "session_id": "target-session", "branch_id": "branch"}]
                            emit({"type": "session_result", "request_id": request_id, "success": True,
                                  "scope": request["scope"], "total": 1, "entries": entries})
                        else:
                            assert request["action"] == "branch"
                            assert request["expected_session"] == active
                            content = "NAV-CONTEXT" if active == "first-session" else "NAV-LATER"
                            active = "target-session"
                            emit({"type": "session_result", "request_id": request_id, "success": True})
                            emit({"type": "session_changed", "request_id": request_id, "session_id": active,
                                  "history_count": 1, "history_format": 2})
                            emit({"type": "history_chunk", "entries": [{"role": "user", "content": content}]})
                            emit({"type": "history_end"})
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("PRESERVED-NAV-DRAFT")
        terminal.wait_editor("PRESERVED-NAV-DRAFT")
        terminal.keys("M-r")
        terminal.wait("Sessions ·")
        terminal.text("/NAV-TARGET")
        terminal.keys("Enter")
        terminal.wait("NAV-TARGET")
        terminal.wait_hidden("loading")
        terminal.keys("Right")
        terminal.wait("NAV-BRANCH")
        terminal.wait_hidden("loading")
        terminal.resize(36, 7)
        terminal.keys("C-l")
        terminal.wait("Branch/tree")
        terminal.resize(80, 24)
        terminal.keys("Enter")
        terminal.wait("NAV-CONTEXT")
        terminal.wait_editor("PRESERVED-NAV-DRAFT")
        terminal.keys("C-c")
        terminal.text("/tree")
        terminal.keys("Enter")
        terminal.wait("Return checkpoint before")
        # Select the return checkpoint by its label, not a unstable numeric row.
        terminal.text("/Return checkpoint")
        terminal.keys("Enter")
        terminal.wait("Return checkpoint before")
        terminal.wait_hidden("loading")
        terminal.keys("Enter")
        terminal.wait("NAV-LATER")
        assert "FIXTURE-ANSWER" not in terminal.visible()
        terminal.text("RESTORED-DRAFT")
        terminal.wait_editor("RESTORED-DRAFT")
        terminal.finish()
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
@pytest.mark.parametrize("clipboard_backend", ["wayland", "macos", "windows"])
def test_actual_image_attach_clipboard_preview_send_and_draft_recovery(terminal, tmp_path, kind, clipboard_backend):
    import base64

    from xdog.claw.channels.tui.image_upload import ImageUploads
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="
    )
    if clipboard_backend == "windows":
        import struct

        from xdog.tui.bitmap_dib import dib_image
        raw = bytearray(124)
        struct.pack_into('<IiiHHIIIIII', raw, 0, 124, 1, -1, 1, 32, 3, 4, 0, 0, 0, 0)
        struct.pack_into('<4I', raw, 40, 0xff0000, 0xff00, 0xff, 0xff000000)
        endpoints = (.4124564, .2126729, .0193339, .3575761, .7151522, .1191920,
                     .1804375, .0721750, .9503041)
        struct.pack_into('<9i', raw, 60, *(round(value * 2**30) for value in endpoints))
        struct.pack_into('<3I', raw, 96, *(round(value * 65536) for value in (1.8, 2.2, 2.4)))
        raw.extend(b'\x20\x40\x80\x70')
        png = dib_image(bytes(raw), maximum=10000)  # Calibrated RGB/declared alpha, valid ICC/PNG, not header-only.
        assert b'iCCP' in png
    image = tmp_path / "example.png"
    image.write_bytes(png)
    helper = tmp_path / ("osascript" if clipboard_backend == "macos" else
                         "owned-win-python" if clipboard_backend == "windows" else "wl-paste")
    if clipboard_backend == "macos":
        reply = json.dumps({"kind": "image", "data": base64.b64encode(png).decode()})
        helper.write_text(f"#!{sys.executable}\nimport sys\n"
                          "assert sys.argv[1:4] == ['-l', 'JavaScript', '-e']\n"
                          "assert sys.argv[5:] == ['--', '10485760', '1048576']\n"
                          f"print({reply!r})\n")
        terminal.child_env["XDOG_FIXTURE_MAC_CLIPBOARD"] = "1"
    elif clipboard_backend == "windows":
        reply = json.dumps({"kind": "image", "data": base64.b64encode(png).decode()})
        helper.write_text(f"#!{sys.executable}\nimport sys\n"
                          "assert sys.argv[1:] == ['-m', 'xdog.tui.clipboard_windows_worker', '10485760']\n"
                          f"print({reply!r})\n")
        terminal.child_env["XDOG_FIXTURE_WIN_CLIPBOARD"] = str(helper)
    else:
        helper.write_text(f"#!{sys.executable}\nimport sys,base64\n"
                          "if '--list-types' in sys.argv: print('image/png')\n"
                          f"else: sys.stdout.buffer.write(base64.b64decode({base64.b64encode(png).decode()!r}))\n")
    helper.chmod(0o700)
    terminal.child_env.update(WAYLAND_DISPLAY="fixture", PATH=str(tmp_path) + ":" + os.environ["PATH"])
    errors = []
    server = None
    thread = None
    received = []
    if kind == "claw":
        path = str(tmp_path / "images-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            uploads = ImageUploads()
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            event = {"type": "pong", "session_id": "fixture", "workspace_dir": str(tmp_path),
                                     "attachment_transport": 1, "image_input": True}
                        elif request["type"] == "attachment_chunk":
                            event = {"type": "attachment_ack", "id": request["id"], "status": uploads.chunk(request)}
                        else:
                            assert request["type"] == "message"
                            images = uploads.resolve(request["image_refs"])
                            assert len(images) == 2 and all(base64.b64decode(item.data) == png for item in images)
                            received.append(request)
                            event = {"type": "response", "run_id": request["run_id"], "content": "IMAGE-RECEIVED-2"}
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("/attach example.png")
        terminal.keys("Enter")
        terminal.wait("Images: 1")
        terminal.keys("C-v")
        terminal.wait("Images: 2")
        terminal.text("/attachments")
        terminal.keys("Enter")
        pane = terminal.wait("Draft attachments")
        import re
        key = re.search(r"\b(a[0-9a-f]{8}) example.png", pane).group(1)
        terminal.text("/attachments preview " + key)
        terminal.keys("Enter")
        terminal.wait("Attachment ·")
        terminal.wait_cursor(False)
        terminal.keys("Escape")
        terminal.wait_hidden("Image preview")
        terminal.text("IMAGE-DRAFT")
        terminal.wait_editor("IMAGE-DRAFT")
        terminal.resize(36, 6)
        terminal.keys("C-l")
        terminal.wait_editor("IMAGE-DRAFT")
        terminal.resize(80, 24)
        terminal.keys("Enter")
        terminal.wait("IMAGE-RECEIVED-2")
        terminal.wait_hidden("Images: 2 · /attachments")
        assert base64.b64encode(png).decode() not in terminal.pane()
        if kind == "claw":
            assert len(received) == 1
        terminal.text("AFTER-IMAGE-DRAFT")
        terminal.wait_editor("AFTER-IMAGE-DRAFT")
        terminal.finish()
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_actual_transcript_search_selection_fullscreen_resize_and_native_return(terminal, tmp_path, kind):
    server = thread = None
    errors = []
    if kind == "claw":
        path = str(tmp_path / "transcript-gateway.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)

        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            event = {"type": "pong", "session_id": "fixture"}
                        else:
                            assert request["type"] == "message" and request["content"] == "long-history"
                            event = {"type": "response", "run_id": request["run_id"],
                                     "content": "\n".join(f"LONG-HISTORY-{i}" for i in range(100))}
                        stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        terminal.text("long-history")
        terminal.keys("Enter")
        terminal.wait("LONG-HISTORY-99")
        terminal.text("KEEP-TRANSCRIPT-DRAFT")
        terminal.wait_editor("KEEP-TRANSCRIPT-DRAFT")
        terminal.keys("M-h")
        terminal.wait("Transcript (inline)")
        terminal.text("/LONG-HISTORY-8")
        terminal.keys("Enter")
        terminal.wait("11 matches")
        terminal.keys("n")
        terminal.wait("2/11 matches")
        terminal.wait("LONG-HISTORY-80")
        terminal.keys("a")
        terminal.wait("selected 100 lines")
        terminal.keys("Escape")
        terminal.wait_hidden("selected 100 lines")
        terminal.keys("f")
        terminal.wait("Transcript (fullscreen)")
        assert terminal.call("display-message", "-p", "-t", "test", "#{alternate_on}").strip() == "1"
        terminal.resize(44, 8)
        terminal.keys("C-l")
        terminal.wait("LONG-HISTORY-80")
        terminal.resize(80, 24)
        terminal.keys("f")
        terminal.wait("Transcript (inline)")
        assert terminal.call("display-message", "-p", "-t", "test", "#{alternate_on}").strip() == "0"
        terminal.keys("Escape")
        terminal.wait_hidden("Transcript (inline)")
        terminal.wait_editor("KEEP-TRANSCRIPT-DRAFT")
        assert "SHELL-BEFORE" in terminal.pane()
        assert "LONG-HISTORY-99" in terminal.pane()
        terminal.finish(allow_fullscreen=True)
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


def test_actual_fullscreen_permission_preemption_and_denial_return(terminal, tmp_path):
    try:
        terminal.start("coding")
        terminal.text("review-permission")
        terminal.keys("Enter")
        terminal.wait("REVIEW-HOLD")
        terminal.text("PERMISSION-TRANSCRIPT-DRAFT")
        terminal.wait_editor("PERMISSION-TRANSCRIPT-DRAFT")
        terminal.keys("M-h")
        terminal.wait("Transcript (inline)")
        terminal.keys("f")
        terminal.wait("Transcript (fullscreen)")
        (tmp_path / "release-review").touch()
        terminal.wait("Tool permission required")
        assert terminal.call("display-message", "-p", "-t", "test", "#{alternate_on}").strip() == "0"
        terminal.keys("Escape")
        terminal.wait_hidden("Tool permission required")
        terminal.wait("Transcript (inline)")
        terminal.wait_cursor(False)
        terminal.keys("Escape")
        terminal.wait_hidden("Transcript (inline)")
        terminal.wait_editor("PERMISSION-TRANSCRIPT-DRAFT")
        terminal.wait("FIXTURE-ANSWER")
        terminal.finish(allow_fullscreen=True)
    finally:
        (tmp_path / "release-review").touch(exist_ok=True)


def test_actual_claw_full_source_beyond_wire_caps_search_and_fullscreen(terminal, tmp_path):
    import base64

    from xdog.claw.core.runtime.group import _display_history_entry
    from xdog.claw.core.runtime.transcript_source import SourceCapture, prepare_source

    entries = [
        {"role": "user", "content": "inspect source"},
        {"role": "assistant", "content": [{"type": "toolCall", "id": "call", "name": "bash",
             "arguments": {"command": "x" * 5000 + "FULL-ARG-END", "password": "PRIVATE-SOURCE-SECRET"}}]},
        {"role": "toolResult", "tool_call_id": "call", "tool_name": "bash",
         "content": [{"type": "text", "text": "\n".join(f"source-line-{i}" for i in range(3000))
                      + "\n" + "x" * 1200000 + "FULL-WIRE-END"}]},
    ]
    history = [_display_history_entry(entry) for entry in entries]
    path = str(tmp_path / "full-source.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)
    server.settimeout(10)
    errors, requests = [], []
    def serve():
        try:
            conn, _ = server.accept()
            with conn, conn.makefile("rwb") as stream:
                for line in stream:
                    request = json.loads(line)
                    requests.append(request["type"])
                    if request["type"] == "ping":
                        events = [{"type": "pong", "session_id": "fixture", "history_format": 2,
                                   "history_count": len(history), "transcript_source": 1},
                                  {"type": "history_chunk", "entries": history}, {"type": "history_end"}]
                        for event in events:
                            stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
                    elif request["type"] == "source_read":
                        spool = prepare_source(SourceCapture("fixture", lambda: entries), threading.Event())
                        envelope = {key: request[key] for key in ("request_id", "group_id", "session_id")}
                        envelope["revision"] = 0
                        try:
                            stream.write((json.dumps({"type": "source_begin", **envelope,
                                                      "bytes": spool.size, "sha256": spool.sha256}) + "\n").encode())
                            index = 0
                            while block := spool.file.read(64 * 1024):
                                stream.write((json.dumps({"type": "source_chunk", **envelope, "index": index,
                                                          "data": base64.b64encode(block).decode()}) + "\n").encode())
                                index += 1
                            ending = {"type": "source_end", **envelope, "chunks": index}
                            stream.write((json.dumps(ending) + "\n").encode())
                            stream.flush()
                        finally:
                            spool.file.close()
                    else:
                        assert request["type"] == "source_cancel"
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        terminal.start("claw", path)
        terminal.wait("bash")
        assert "FULL-WIRE-END" not in terminal.pane()
        terminal.text("FULL-SOURCE-DRAFT")
        terminal.wait_editor("FULL-SOURCE-DRAFT")
        terminal.keys("M-h")
        terminal.wait("Transcript (inline)")
        terminal.text("/FULL-WIRE-END")
        terminal.keys("Enter")
        terminal.wait("Full source snapshot")
        terminal.wait("1/1 matches")
        terminal.wait("FULL-WIRE-END")
        terminal.keys("f")
        terminal.wait("Transcript (fullscreen)")
        terminal.resize(44, 8)
        terminal.keys("C-l")
        terminal.wait("FULL-WIRE-END")
        terminal.resize(80, 24)
        terminal.keys("a")
        terminal.wait("selected 3005 lines")
        terminal.keys("Escape")
        terminal.wait_hidden("selected 3005 lines")
        terminal.keys("Escape")
        terminal.wait_hidden("Transcript (inline)")
        terminal.wait_editor("FULL-SOURCE-DRAFT")
        assert "PRIVATE-SOURCE-SECRET" not in terminal.pane()
        assert b"eyJpZCI6" not in terminal.raw.read_bytes()
        assert "message" not in requests
        terminal.finish(allow_fullscreen=True)
    finally:
        server.close()
        thread.join(5)
    assert not errors


@pytest.mark.parametrize("kind", ["coding", "claw"])
@pytest.mark.parametrize("clipboard_backend", ["wayland", "windows"])
def test_actual_copy_export_and_local_share_review_preserve_terminal_draft(
        terminal, tmp_path, kind, clipboard_backend):
    import base64

    from xdog.claw.core.runtime.transcript_source import SourceCapture, prepare_source

    copied = tmp_path / "clipboard-output"
    helper = tmp_path / ("owned-win-copy" if clipboard_backend == "windows" else "wl-copy")
    check = ("assert sys.argv[1:] == ['-m', 'xdog.tui.clipboard_windows_write_worker']\n"
             if clipboard_backend == "windows" else "")
    helper.write_text(f"#!{sys.executable}\nimport sys\n" + check +
                      f"open({str(copied)!r},'wb').write(sys.stdin.buffer.read())\n")
    if clipboard_backend == "windows":
        terminal.child_env["XDOG_FIXTURE_WIN_COPY"] = str(helper)
    helper.chmod(0o700)
    terminal.child_env.update(WAYLAND_DISPLAY="fixture", PATH=str(tmp_path) + ":" + os.environ["PATH"])
    server = thread = None
    errors = []
    if kind == "claw":
        path = str(tmp_path / "output-source.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        server.listen(1)
        server.settimeout(10)
        entries = [{"role": "user", "content": "hello output"},
                   {"role": "assistant", "content": [{"type": "text", "text": "FIXTURE-ANSWER"}]}]
        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    for line in stream:
                        request = json.loads(line)
                        if request["type"] == "ping":
                            events = [{"type": "pong", "session_id": "fixture", "history_format": 2,
                                       "history_count": len(entries), "transcript_source": 1,
                                       "workspace_dir": str(tmp_path)},
                                      {"type": "history_chunk", "entries": entries}, {"type": "history_end"}]
                        elif request["type"] == "source_read":
                            spool = prepare_source(SourceCapture("fixture", lambda: entries), threading.Event())
                            envelope = {key: request[key] for key in ("request_id", "group_id", "session_id")}
                            envelope["revision"] = 0
                            events = [{"type": "source_begin", **envelope,
                                       "bytes": spool.size, "sha256": spool.sha256}]
                            index = 0
                            while block := spool.file.read(64 * 1024):
                                events.append({"type": "source_chunk", **envelope, "index": index,
                                               "data": base64.b64encode(block).decode()})
                                index += 1
                            events.append({"type": "source_end", **envelope, "chunks": index})
                            spool.file.close()
                        else:
                            assert request["type"] == "source_cancel"
                            events = []
                        for event in events:
                            stream.write((json.dumps(event) + "\n").encode())
                        stream.flush()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
    try:
        terminal.start(kind, *([path] if kind == "claw" else []))
        if kind == "coding":
            terminal.text("hello output")
            terminal.keys("Enter")
        terminal.wait("FIXTURE-ANSWER")
        terminal.text("OUTPUT-KEEP-DRAFT")
        terminal.wait_editor("OUTPUT-KEEP-DRAFT")
        terminal.keys("M-c")
        terminal.wait("Snapshot copied")
        assert copied.read_text() == "FIXTURE-ANSWER"
        # Claw opens the source browser for the authoritative read.
        if kind == "claw":
            terminal.keys("Escape")
            terminal.wait_hidden("Transcript (inline)")
        terminal.wait_editor("OUTPUT-KEEP-DRAFT")
        terminal.keys("C-c")
        target = tmp_path / "export.json"
        terminal.text(f"/export {target.name} json conversation")
        terminal.keys("Enter")
        terminal.wait("Saved")
        assert "FIXTURE-ANSWER" in target.read_text()
        if kind == "claw":
            terminal.keys("Escape")
            terminal.wait_hidden("Transcript (inline)")
        shared = tmp_path / "share.html"
        terminal.text(f"/share {shared.name}")
        terminal.keys("Enter")
        terminal.wait("Review local share")
        assert not shared.exists()
        terminal.resize(36, 6)
        terminal.keys("Enter")
        time.sleep(0.1)
        assert not shared.exists()
        terminal.resize(120, 24)
        terminal.wait("Enter saves local HTML")
        terminal.keys("Enter")
        deadline = time.monotonic() + 10
        while not shared.exists() and time.monotonic() < deadline:
            time.sleep(0.03)
        terminal.wait("Saved")
        assert "FIXTURE-ANSWER" in shared.read_text() and "default-src 'none'" in shared.read_text()
        if kind == "claw":
            terminal.keys("Escape")
            terminal.wait_hidden("Transcript (inline)")
        terminal.text("AFTER-OUTPUT-DRAFT")
        terminal.wait_editor("AFTER-OUTPUT-DRAFT")
        terminal.finish()
    finally:
        if server is not None:
            server.close()
        if thread is not None:
            thread.join(5)
    assert not errors


def test_actual_coding_mcp_connect_tool_approval_disconnect_and_terminal_cleanup(terminal, tmp_path):
    config_dir = tmp_path / "coding-home"
    config_dir.mkdir()
    fixture = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"
    log = tmp_path / "mcp-requests"
    (config_dir / "settings.json").write_text(json.dumps({"mcp_servers": {"fixture": {
        "command": sys.executable, "args": [str(fixture), "modern", str(log)],
    }}}))
    terminal.child_env["CODING_DIR"] = str(config_dir)
    terminal.start("coding")
    assert not log.exists()
    terminal.text("/mcp connect fixture")
    terminal.keys("Enter")
    terminal.wait("MCP fixture: connect completed")
    terminal.text("mcp-call")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")
    assert not any(json.loads(line)["method"] == "tools/call" for line in log.read_text().splitlines())
    terminal.keys("Enter")
    terminal.wait("TERMINAL-MCP-RESULT")
    terminal.wait("FIXTURE-ANSWER")
    terminal.text("/mcp disconnect fixture")
    terminal.keys("Enter")
    terminal.wait("MCP fixture: disconnect completed")
    assert sum(json.loads(line)["method"] == "tools/call" for line in log.read_text().splitlines()) == 1
    terminal.text("AFTER-MCP-DRAFT")
    terminal.wait_editor("AFTER-MCP-DRAFT")
    terminal.finish()


def test_actual_coding_http_mcp_connection_mirrors_tool_and_approval(terminal, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    observed = []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((request, dict(self.headers)))
            method = request["method"]
            if method == "server/discover":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {
                    "value": {"type": "string", "x-mcp-header": "Value"}}, "required": ["value"]}}]}
            else:
                assert method == "tools/call" and self.headers["Mcp-Name"] == "echo"
                assert self.headers["Mcp-Param-Value"] == request["params"]["arguments"]["value"]
                result = {"content": [{"type": "text", "text": "HTTP-MCP-RESULT"}]}
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_dir = tmp_path / "coding-home"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps({"mcp_servers": {"fixture": {
        "transport": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}}}))
    terminal.child_env["CODING_DIR"] = str(config_dir)
    # The fixture globally refuses model HTTP; allow only the configured MCP
    # client's HTTP factory via a scoped bootstrap flag, never ai providers.
    terminal.child_env["XDOG_FIXTURE_MCP_HTTP"] = "1"
    try:
        terminal.start("coding")
        assert not observed
        terminal.text("/mcp connect fixture")
        terminal.keys("Enter")
        terminal.wait("MCP fixture: connect completed")
        terminal.text("mcp-call")
        terminal.keys("Enter")
        terminal.wait("Tool permission required")
        assert not any(request["method"] == "tools/call" for request, _headers in observed)
        terminal.keys("Enter")
        terminal.wait("HTTP-MCP-RESULT")
        terminal.wait("FIXTURE-ANSWER")
        terminal.text("/mcp disconnect fixture")
        terminal.keys("Enter")
        terminal.wait("MCP fixture: disconnect completed")
        assert sum(request["method"] == "tools/call" for request, _headers in observed) == 1
        terminal.finish()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_actual_coding_mcp_browser_resource_preview_is_inert_until_draft_insert(terminal, tmp_path):
    config_dir = tmp_path / "coding-home"
    config_dir.mkdir()
    fixture = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"
    log = tmp_path / "mcp-data-requests"
    (config_dir / "settings.json").write_text(json.dumps({"mcp_servers": {"fixture": {
        "command": sys.executable, "args": [str(fixture), "modern", str(log)]}}}))
    terminal.child_env["CODING_DIR"] = str(config_dir)
    terminal.start("coding")
    terminal.text("/mcp")
    terminal.keys("Enter")
    terminal.wait("MCP servers")
    terminal.wait("fixture · disconnected")
    assert not log.exists()
    terminal.text("c")
    terminal.wait("fixture · connected")
    terminal.text("u")
    terminal.wait("fixture://text")
    terminal.keys("Enter")
    terminal.wait("MCP preview")
    terminal.wait("UNTRUSTED-RESOURCE-START")
    assert "FIXTURE-ANSWER" not in terminal.pane()
    terminal.resize(44, 8)
    terminal.keys("C-l")
    terminal.wait("MCP preview")
    terminal.resize(80, 24)
    terminal.text("i")
    terminal.wait_hidden("MCP preview")
    terminal.wait_editor("RESOURCE-TAIL")
    assert "MCP data inserted into editable draft" in terminal.pane()
    assert not any(json.loads(line)["method"] == "tools/call" for line in log.read_text().splitlines())
    terminal.keys("C-c")
    terminal.text("/mcp disconnect fixture")
    terminal.keys("Enter")
    terminal.wait("MCP fixture: disconnect completed")
    terminal.finish()


def test_actual_coding_legacy_mcp_tool_response_resumes_without_second_approval_or_post(terminal, tmp_path):
    import runpy

    fixture = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_resume_peer.py"
    peer_class = runpy.run_path(str(fixture))["ResumePeer"]
    with peer_class() as peer:
        config_dir = tmp_path / "coding-home"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(json.dumps({"mcp_servers": {"fixture": {
            "transport": "http", "url": peer.url}}}))
        terminal.child_env["CODING_DIR"] = str(config_dir)
        terminal.child_env["XDOG_FIXTURE_MCP_HTTP"] = "1"
        terminal.start("coding")
        assert not peer.requests
        terminal.text("/mcp connect fixture")
        terminal.keys("Enter")
        terminal.wait("MCP fixture: connect completed")
        terminal.text("mcp-call")
        terminal.keys("Enter")
        terminal.wait("Tool permission required")
        assert not any(request.get("method") == "tools/call" for request, _headers in peer.requests)
        terminal.keys("Enter")
        terminal.wait("RESUMED-TERMINAL-MCP-RESULT")
        terminal.wait("FIXTURE-ANSWER")
        assert sum(request.get("method") == "tools/call" for request, _headers in peer.requests) == 1
        assert sum("Last-Event-ID" in headers for headers in peer.gets) == 1
        terminal.text("/mcp disconnect fixture")
        terminal.keys("Enter")
        terminal.wait("MCP fixture: disconnect completed")
        terminal.finish()


def test_actual_coding_plan_read_only_phase_visible_transition_and_retained_approval(terminal, tmp_path):
    home = tmp_path / "planning-home"
    home.mkdir()
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/plan start Implement change")
    terminal.keys("Enter")
    terminal.wait("Plan · planning")
    terminal.keys("Escape")
    terminal.wait_hidden("Plan · planning")
    terminal.text("plan-build")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-ANSWER")
    terminal.text("permission")
    terminal.keys("Enter")
    terminal.wait("Planning is read-only")
    assert "Tool permission required" not in terminal.pane()
    terminal.text("/plan")
    terminal.keys("Enter")
    terminal.wait("Implement change")
    terminal.text("e")
    terminal.wait("Confirm execute")
    terminal.resize(32, 3)
    terminal.keys("Enter")
    time.sleep(0.1)
    snapshots = [json.loads(path.read_text()) for path in (home / "sessions").glob("*.json")]
    assert snapshots and all(value.get("plan", {}).get("mode") == "planning" for value in snapshots)
    terminal.resize(80, 24)
    terminal.keys("Escape")  # Cancel the old confirmation after resize.
    terminal.text("e")
    terminal.wait("Confirm execute")
    terminal.keys("Enter")
    terminal.wait("Plan · executing")
    terminal.keys("Escape")
    terminal.wait_hidden("Plan · executing")
    terminal.text("permission")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")  # Plan execution did not grant allow-all.
    terminal.keys("Enter")
    terminal.wait("printf fixture")
    terminal.wait("FIXTURE-ANSWER")
    terminal.finish()


def test_actual_claw_plan_gateway_read_only_confirmation_execution_and_terminal_cleanup(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    calls, errors, owned = [], [], {}
    ready = threading.Event()
    workspace = tmp_path / "workspace"
    def stream(_model, context, _options=None):
        calls.append(context)
        last = context.messages[-1]
        if last.role == "user" and "plan-build" in str(last.content):
            call = ToolCall(id="build", name="update_plan", arguments={"expected_revision": 1, "steps": [
                {"description": "Implement change", "status": "pending"}]})
            message = AssistantMessage(content=(call,), stop_reason="toolUse")
        elif last.role == "user" and "plan-write" in str(last.content):
            call = ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "changed"})
            message = AssistantMessage(content=(call,), stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="CLAW-PLAN-ANSWER"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "claw-plan.sock"), pid_file=str(tmp_path / "claw-plan.pid"),
                        groups=(GroupDef(id="main", is_main=True, workspace=str(workspace)),))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/plan start Implement change")
        terminal.keys("Enter")
        terminal.wait("Plan · planning")
        assert not calls
        terminal.keys("Escape")
        terminal.wait_hidden("Plan · planning")
        terminal.text("plan-build")
        terminal.keys("Enter")
        terminal.wait("CLAW-PLAN-ANSWER")
        terminal.text("plan-write")
        terminal.keys("Enter")
        terminal.wait("Planning is read-only")
        terminal.wait("connected | idle")
        assert not (workspace / "target").exists()
        terminal.text("/plan")
        terminal.keys("Enter")
        terminal.wait("revision 2")
        terminal.wait_hidden("Waiting for idle/plan refresh")
        terminal.text("e")
        terminal.wait("Confirm execute")
        terminal.keys("Enter")
        terminal.wait("Plan · executing")
        assert len(calls) == 4
        terminal.keys("Escape")
        terminal.wait_hidden("Plan · executing")
        terminal.text("plan-write")
        terminal.keys("Enter")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            # File creation precedes write completion; wait for the actual content,
            # not the transient empty file observed under concurrent test load.
            if (workspace / "target").exists() and (workspace / "target").read_text() == "changed":
                break
            time.sleep(0.02)
        assert (workspace / "target").read_text() == "changed"
        terminal.wait("CLAW-PLAN-ANSWER")
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_extension_commands_widgets_approval_stop_and_terminal_safety(terminal, tmp_path):
    import shutil

    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    home = tmp_path / "extension-home"
    home.mkdir()
    marker = tmp_path / "extension-marker"
    (home / "settings.json").write_text(json.dumps({"extensions": ["sample"], "extension_dir": str(root),
                                                   "extension_settings": {"sample": {"marker": str(marker)}}}))
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    assert not marker.exists()
    terminal.text("/extensions")
    terminal.keys("Enter")
    terminal.wait("EXTENSION-WIDGET")
    terminal.wait_hidden("Esc cancel command")
    assert '"state": "active"' in terminal.pane()
    assert marker.exists()
    terminal.text("/ext-sample-hello user")
    terminal.keys("Enter")
    terminal.wait("EXTENSION-COMMAND-user")
    terminal.wait("EXTENSION-COMMAND-STATUS")
    assert "FIXTURE-ANSWER" not in terminal.pane()
    terminal.text("extension-call")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")
    terminal.keys("Enter")
    terminal.wait("EXTENSION-RESULT-fixture-POST")
    terminal.wait("FIXTURE-ANSWER")
    terminal.text("/extensions unload sample")
    terminal.keys("Enter")
    terminal.wait("unloaded")
    assert marker.read_text().endswith("stopped\n")
    terminal.finish()
    assert b"\x1b]52;invalid" not in terminal.raw.read_bytes()


def test_actual_claw_extension_gateway_command_widget_tool_and_cleanup(terminal, tmp_path):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    marker = tmp_path / "marker"
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    def stream(_model, context, _options=None):
        calls.append(context)
        if context.messages[-1].role == "user":
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_sample_"))
            message = AssistantMessage(content=(ToolCall(id="ext", name=tool.name, arguments={"value": "fixture"}),),
                                       stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="CLAW-EXTENSION-ANSWER"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "claw-ext.sock"), pid_file=str(tmp_path / "claw-ext.pid"),
                        groups=(GroupDef(id="main", is_main=True),), extensions=("sample",),
                        extension_allow_tools=("sample",), extension_dir=str(root),
                        extension_settings={"sample": {"marker": str(marker)}})
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        assert not marker.exists()
        terminal.text("/extensions")
        terminal.keys("Enter")
        terminal.wait("EXTENSION-WIDGET")
        assert '"state": "active"' in terminal.pane()
        assert marker.exists() and not calls
        terminal.text("/ext-sample-hello user")
        terminal.keys("Enter")
        terminal.wait("EXTENSION-COMMAND-user")
        terminal.wait("EXTENSION-COMMAND-STATUS")
        terminal.text("extension-call")
        terminal.keys("Enter")
        terminal.wait("EXTENSION-RESULT-fixture-POST")
        terminal.wait("CLAW-EXTENSION-ANSWER")
        assert len(calls) == 2
        terminal.text("/extensions unload sample")
        terminal.keys("Enter")
        terminal.wait("unloaded")
        assert marker.read_text().endswith("stopped\n")
        terminal.finish()
        assert b"\x1b]52;invalid" not in terminal.raw.read_bytes()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_extension_entries_context_reload_and_terminal_cleanup(terminal, tmp_path):
    import shutil
    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    home = tmp_path / "context-home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"extensions": ["contextual"], "extension_dir": str(root)}))
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/ext-contextual-remember first")
    terminal.keys("Enter")
    terminal.wait("ENTRY-SAVED-first")
    terminal.text("context-check")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-CONTEXT-first")
    snapshots = [json.loads(path.read_text()) for path in (home / "sessions").glob("*.json")]
    assert snapshots[-1]["extension_entries"]["contextual"][0]["data"] == {"text": "first"}
    assert "EXTENSION-CONTEXT" not in json.dumps(snapshots[-1]["messages"])
    terminal.text("/extensions reload contextual")
    terminal.keys("Enter")
    terminal.wait("RESTORED-ENTRIES-1")
    terminal.wait_hidden("Esc cancel command")
    terminal.text("context-check")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-CONTEXT-first")
    terminal.finish()


def test_actual_claw_extension_entries_context_reload_and_terminal_cleanup(terminal, tmp_path):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    def stream(model, context, options=None):
        calls.append(context)
        text = str(context.messages[-1].content)
        answer = "CLAW-CONTEXT-first" if "EXTENSION-CONTEXT-first" in text else "CLAW-NO-CONTEXT"
        return EventStream.empty(AssistantMessage(content=(TextContent(text=answer),)))
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "ctx.sock"), pid_file=str(tmp_path / "ctx.pid"),
                        groups=(GroupDef(id="main", is_main=True),),
                        extensions=("contextual",), extension_dir=str(root))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/ext-contextual-remember first")
        terminal.keys("Enter")
        terminal.wait("ENTRY-SAVED-first")
        assert not calls
        terminal.text("context-check")
        terminal.keys("Enter")
        terminal.wait("CLAW-CONTEXT-first")
        terminal.text("/extensions reload contextual")
        terminal.keys("Enter")
        terminal.wait("RESTORED-ENTRIES-1")
        terminal.text("context-check")
        terminal.keys("Enter")
        deadline = time.monotonic() + 6
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(calls) == 2 and "EXTENSION-CONTEXT-first" in str(calls[-1].messages[-1].content)
        terminal.wait("CLAW-CONTEXT-first")
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_extension_select_input_confirm_focus_and_terminal_safety(terminal, tmp_path):
    import shutil
    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    home = tmp_path / "dialog-home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"extensions": ["dialogue"], "extension_dir": str(root)}))
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/ext-dialogue-ask")
    terminal.keys("Enter")
    terminal.wait("Choose color")
    terminal.keys("Down")
    terminal.wait("2/2 · Blue")
    terminal.keys("Enter")
    terminal.wait("Editable extension input")
    terminal.wait_editor("seed")
    terminal.keys("C-u")
    terminal.text("user")
    terminal.wait_editor("user")
    terminal.keys("Enter")
    terminal.wait("Proceed")
    terminal.keys("Down")
    terminal.resize(32, 3)
    terminal.keys("Enter")
    time.sleep(0.1)
    assert "DIALOG-RESULT" not in terminal.pane()
    terminal.resize(80, 24)
    terminal.wait("This does not grant host tool permissions")
    terminal.keys("Enter")
    terminal.wait("DIALOG-RESULT-Blue-user-True")
    assert "FIXTURE-ANSWER" not in terminal.pane()
    terminal.finish()


def test_actual_claw_extension_select_input_confirm_command_keeps_socket_live(terminal, tmp_path):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "dialog.sock"), pid_file=str(tmp_path / "dialog.pid"),
                        groups=(GroupDef(id="main", is_main=True),), extensions=("dialogue",), extension_dir=str(root))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/ext-dialogue-ask")
        terminal.keys("Enter")
        terminal.wait("Choose color")
        terminal.keys("Down")
        terminal.wait("2/2 · Blue")
        terminal.keys("Enter")
        terminal.wait("Editable extension input")
        terminal.wait_editor("seed")
        terminal.keys("C-u")
        terminal.text("user")
        terminal.wait_editor("user")
        terminal.keys("Enter")
        terminal.wait("Proceed")
        terminal.keys("Down")
        terminal.wait("2/2 · Yes")
        terminal.keys("Enter")
        terminal.wait("DIALOG-RESULT-Blue-user-True")
        assert not calls
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_child_start_independent_approval_and_terminal_restore(terminal):
    terminal.start("coding")
    terminal.text("/agents start --inherit permission")
    terminal.keys("Enter")
    terminal.wait("independent approval")
    terminal.wait("Run shell command")
    terminal.wait_cursor(False)
    terminal.keys("Escape")
    terminal.wait("ready")
    terminal.text("/agents list")
    terminal.keys("Enter")
    terminal.wait("[completed]")
    terminal.keys("Enter")
    terminal.wait("Child snapshot")
    terminal.text("/")
    terminal.text("FIXTURE-ANSWER")
    terminal.keys("Enter")
    terminal.wait("1/1 matches")
    terminal.wait("FIXTURE-ANSWER")
    terminal.text("f")
    terminal.wait("snapshot")
    terminal.keys("Escape")
    terminal.wait("Child agents")
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("draft after child")
    terminal.wait_editor("draft after child")
    terminal.finish(allow_fullscreen=True)


def test_actual_claw_child_commands_and_terminal_restore(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    errors, owned, calls = [], {}, []
    ready = threading.Event()
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="TERMINAL-CHILD-RESULT"),)))
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "child.sock"), pid_file=str(tmp_path / "child.pid"),
                        groups=(GroupDef(id="main", is_main=True),))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/agents start Inspect")
        terminal.keys("Enter")
        terminal.wait("started (read-only)")
        terminal.text("/agents list")
        terminal.keys("Enter")
        terminal.wait("[completed]")
        terminal.keys("Enter")
        terminal.wait("TERMINAL-CHILD-RESULT")
        terminal.text("/")
        terminal.text("TERMINAL-CHILD-RESULT")
        terminal.keys("Enter")
        terminal.wait("1/1 matches")
        terminal.text("f")
        terminal.wait("snapshot")
        terminal.keys("Escape")
        terminal.wait("Child agents")
        terminal.keys("Escape")
        terminal.wait_cursor(True)
        assert len(calls) == 1
        terminal.text("draft after child")
        terminal.wait_editor("draft after child")
        terminal.finish(allow_fullscreen=True)
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_child_owned_dialog_requires_separate_permission_and_restores_cursor(terminal, tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    home = tmp_path / "child-dialog-home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"extensions": ["dialogue"], "extension_dir": str(root)}))
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/agents start --inherit child-dialog")
    terminal.keys("Enter")
    terminal.wait("independent approval")
    terminal.wait("Allow once")
    terminal.keys("Enter")
    terminal.wait("Tool input")
    terminal.wait("Child ")
    terminal.text("user")
    terminal.wait_editor("user")
    terminal.keys("Enter")
    terminal.wait("ready")
    terminal.wait_cursor(True)
    terminal.text("/agents")
    terminal.keys("Enter")
    terminal.wait("[completed]")
    terminal.keys("Enter")
    terminal.wait("TOOL-DIALOG-user")
    terminal.keys("Escape")
    terminal.wait("Child agents")
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("parent draft")
    terminal.wait_editor("parent draft")
    terminal.finish()


def test_actual_claw_child_owned_dialog_socket_answer_and_terminal_restore(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / "extensions"
    shutil.copytree(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions", root)
    ready, errors, owned = threading.Event(), [], {}
    def stream(model, context, options):
        if context.messages[-1].role == "user":
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_dialogue_"))
            message = AssistantMessage(content=(ToolCall(id="ask", name=tool.name, arguments={}),))
        else:
            message = AssistantMessage(content=(TextContent(text="CHILD-DIALOG-DONE"),))
        return EventStream.empty(message)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "child-dialog.sock"), pid_file=str(tmp_path / "child-dialog.pid"),
        groups=(GroupDef(id="main", is_main=True),), extensions=("dialogue",), extension_dir=str(root),
        extension_allow_tools=("dialogue",))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/agents start --inherit child-dialog")
        terminal.keys("Enter")
        terminal.wait("Tool input")
        terminal.text("user")
        terminal.wait_editor("user")
        terminal.keys("Enter")
        terminal.wait_cursor(True)
        terminal.text("/agents")
        terminal.keys("Enter")
        terminal.wait("[completed]")
        terminal.keys("Enter")
        terminal.wait("TOOL-DIALOG-user")
        terminal.keys("Escape")
        terminal.wait("Child agents")
        terminal.keys("Escape")
        terminal.wait_cursor(True)
        terminal.text("parent draft")
        terminal.wait_editor("parent draft")
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_child_config_form_reviews_all_settings_before_start(terminal, tmp_path):
    home = tmp_path / "config-home"
    home.mkdir()
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/agents")
    terminal.keys("Enter")
    terminal.wait("No matching children")
    terminal.text("n")
    terminal.wait("Access: read-only")
    terminal.text("specialized task")
    terminal.wait_editor("specialized task")
    terminal.keys("C-g")
    terminal.wait("Child settings JSON")
    terminal.keys("C-u")
    terminal.text(json.dumps({"label": "Reviewer", "instructions": "SPECIALIZED-TERMINAL", "tools": [],
                              "inherit_context": False, "max_model_calls": 2, "max_tokens": 4000}))
    terminal.keys("Enter")
    terminal.wait_editor("specialized task")
    terminal.keys("Enter")
    terminal.wait("Confirm child start")
    terminal.keys("Enter")
    time.sleep(0.1)
    assert not list((home / "sessions").glob("*.children/*/*.json"))
    terminal.review_child_start()
    terminal.keys("Enter")
    terminal.wait("[completed]")
    records = [json.loads(path.read_text()) for path in (home / "sessions").glob("*.children/*/????????????.json")]
    assert len(records) == 1
    assert records[0]["config"]["label"] == "Reviewer" and records[0]["config"]["tools"] == []
    assert records[0]["config"]["inherit_context"] is False and records[0]["config"]["max_model_calls"] == 2
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("draft preserved")
    terminal.wait_editor("draft preserved")
    terminal.finish()


def test_actual_claw_child_config_form_applies_specialization_on_owned_gateway(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    ready, errors, owned, contexts = threading.Event(), [], {}, []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="SPECIALIST-DONE"),)))
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "config.sock"), pid_file=str(tmp_path / "config.pid"),
        groups=(GroupDef(id="main", is_main=True),))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/agents")
        terminal.keys("Enter")
        terminal.wait("No matching children")
        terminal.text("n")
        terminal.wait("Access: read-only")
        terminal.text("specialized task")
        terminal.wait_editor("specialized task")
        terminal.keys("C-g")
        terminal.wait("Child settings JSON")
        terminal.keys("C-u")
        terminal.text(json.dumps({"label": "Reviewer", "instructions": "SPECIALIZED-TERMINAL", "tools": [],
                                  "inherit_context": False, "max_model_calls": 2, "max_tokens": 4000}))
        terminal.keys("Enter")
        terminal.wait_editor("specialized task")
        terminal.keys("Enter")
        terminal.wait("Confirm child start")
        terminal.keys("Enter")
        time.sleep(0.1)
        assert not contexts
        terminal.review_child_start()
        terminal.keys("Enter")
        terminal.wait("[completed]")
        assert len(contexts) == 1 and not contexts[0].tools
        assert "SPECIALIZED-TERMINAL" in str(contexts[0].system_prompt)
        terminal.keys("Escape")
        terminal.wait_cursor(True)
        terminal.text("draft preserved")
        terminal.wait_editor("draft preserved")
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_child_model_reasoning_override_keeps_parent_model_and_cursor(terminal, tmp_path):
    home = tmp_path / "child-model-home"
    home.mkdir()
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/agents start --model fixture/reviewer --thinking low --fresh review")
    terminal.keys("Enter")
    terminal.wait("started (read-only)")
    terminal.text("/agents")
    terminal.keys("Enter")
    terminal.wait("[completed]")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-REVIEWER-low")
    records = [json.loads(path.read_text()) for path in (home / "sessions").glob("*.children/*/????????????.json")]
    assert len(records) == 1
    assert records[0]["effective"]["model"] == "fixture/reviewer" and records[0]["effective"]["thinking"] == "low"
    assert records[0]["effective"]["max_output_tokens"] == 2000
    terminal.keys("Escape")
    terminal.wait("Child agents")
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("parent model draft")
    terminal.wait_editor("parent model draft")
    assert "terminal-fixture" in terminal.visible()
    terminal.finish()


def test_actual_claw_child_model_reasoning_override_routes_without_parent_change(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.ai.types import AssistantMessage, Model, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    ready, errors, owned, calls = threading.Event(), [], {}, []
    class Peer:
        def model(self, name):
            return Model(id="fixture/reviewer", context_window=128000, max_tokens=2000, reasoning=True,
                         supported_efforts=("low", "medium")) if name == "fixture/reviewer" else None
        def stream(self, model, context, options):
            calls.append((model, options.thinking))
            return EventStream.empty(AssistantMessage(content=(TextContent(text=f"CLAW-REVIEWER-{options.thinking}"),)))
    config = ClawConfig(model="test/parent", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "models.sock"), pid_file=str(tmp_path / "models.pid"),
        groups=(GroupDef(id="main", is_main=True),))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
                       return_value=("test/parent", stream_fn_from_provider(Peer()))):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/agents start --model fixture/reviewer --thinking low --fresh review")
        terminal.keys("Enter")
        terminal.wait("started (read-only)")
        terminal.text("/agents")
        terminal.keys("Enter")
        terminal.wait("[completed]")
        terminal.keys("Enter")
        terminal.wait("CLAW-REVIEWER-low")
        assert calls == [("fixture/reviewer", "low")]
        terminal.keys("Escape")
        terminal.wait("Child agents")
        terminal.keys("Escape")
        terminal.wait_cursor(True)
        terminal.text("parent model draft")
        terminal.wait_editor("parent model draft")
        assert "test/parent" in terminal.visible()
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


def test_actual_coding_preset_picker_reviews_template_before_any_model_work(terminal, tmp_path):
    home = tmp_path / "preset-home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"child_presets": {"reviewer": {
        "description": "Offline specialist", "config": {"instructions": "PRESET-TERMINAL-INSTRUCTIONS",
        "tools": [], "inherit_context": False, "max_model_calls": 2, "max_tokens": 4000}}}}))
    terminal.child_env["CODING_DIR"] = str(home)
    terminal.start("coding")
    terminal.text("/agents presets")
    terminal.keys("Enter")
    terminal.wait("Child specialist presets")
    terminal.wait("reviewer")
    terminal.keys("Enter")
    terminal.wait("Access: read-only")
    terminal.text("preset task")
    terminal.wait_editor("preset task")
    terminal.keys("Enter")
    terminal.wait("Confirm child start")
    terminal.keys("Enter")
    time.sleep(0.1)
    assert not list((home / "sessions").glob("*.children/*/????????????.json"))
    terminal.review_child_start()
    terminal.keys("Enter")
    terminal.wait("[completed]")
    records = [json.loads(path.read_text()) for path in (home / "sessions").glob("*.children/*/????????????.json")]
    assert records[0]["config"]["preset"] == "reviewer"
    assert records[0]["config"]["instructions"] == "PRESET-TERMINAL-INSTRUCTIONS"
    assert records[0]["config"]["tools"] == [] and not records[0]["config"]["inherit_context"]
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("parent draft")
    terminal.wait_editor("parent draft")
    terminal.finish()


def test_actual_claw_preset_picker_applies_owned_template_with_review_and_cursor_restore(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    ready, errors, owned, contexts = threading.Event(), [], {}, []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="PRESET-DONE"),)))
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "presets.sock"), pid_file=str(tmp_path / "presets.pid"),
        groups=(GroupDef(id="main", is_main=True),), child_presets={"reviewer": {
            "description": "Offline specialist", "config": {"instructions": "PRESET-TERMINAL-INSTRUCTIONS",
            "tools": [], "inherit_context": False, "max_model_calls": 2, "max_tokens": 4000}}})
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
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
    try:
        terminal.start("claw", config.socket_path)
        terminal.text("/agents presets")
        terminal.keys("Enter")
        terminal.wait("Child specialist presets")
        terminal.wait("reviewer")
        assert not contexts
        terminal.keys("Enter")
        terminal.wait("Access: read-only")
        terminal.text("preset task")
        terminal.wait_editor("preset task")
        terminal.keys("Enter")
        terminal.wait("Confirm child start")
        terminal.keys("Enter")
        time.sleep(0.1)
        assert not contexts
        terminal.review_child_start()
        terminal.keys("Enter")
        terminal.wait("[completed]")
        assert len(contexts) == 1 and "PRESET-TERMINAL-INSTRUCTIONS" in str(contexts[0].system_prompt)
        assert not contexts[0].tools
        terminal.keys("Escape")
        terminal.wait_cursor(True)
        terminal.text("parent draft")
        terminal.wait_editor("parent draft")
        terminal.finish()
    finally:
        owned["loop"].call_soon_threadsafe(owned["loop"].stop)
        thread.join(6)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize("registration,client_auth,refresh", [("pre-registered", "none", False),
    ("dynamic", "none", False), ("pre-registered", "client_secret_post", False),
    ("pre-registered", "client_secret_jwt", False), ("pre-registered", "none", True),
    ("pre-registered", "private_key_jwt", True)])
def test_actual_coding_oauth_consent_callback_explicit_connection_and_terminal_restore(
    terminal, tmp_path, registration, client_auth, refresh,
):
    from urllib.parse import parse_qs, urlencode, urlsplit
    from urllib.request import urlopen
    home = tmp_path / "oauth-home"
    home.mkdir()
    profile = {"issuer": "https://issuer.test/tenant", "scopes": ["tools:read"], "registration": registration}
    if registration == "pre-registered":
        profile["client_id"] = "registered-client"
    if client_auth != "none":
        profile.update(token_auth_method=client_auth, client_secret_env="XDOG_TERMINAL_OAUTH_SECRET")
        (tmp_path / "oauth-client-secret.txt").write_text("TERMINAL-CLIENT-PRIVATE-SECRET-" + "x" * 48)
        terminal.child_env["XDOG_FIXTURE_CLIENT_AUTH"] = client_auth
        if client_auth == 'private_key_jwt':
            key = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                                 capture_output=True, check=True).stdout.decode()
            (tmp_path / 'oauth-client-secret.txt').write_text(key)
            profile.pop('client_secret_env')
            profile['client_private_key_env'] = 'XDOG_TERMINAL_OAUTH_SECRET'
    (home / "settings.json").write_text(json.dumps({"mcp_servers": {"peer": {
        "transport": "http", "url": "https://resource.test/mcp", "oauth": profile}}}))
    if refresh:
        terminal.child_env["XDOG_FIXTURE_REFRESH"] = "1"
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_OAUTH="1")
    terminal.start("coding")
    terminal.text("/mcp authorize peer")
    terminal.keys("Enter")
    terminal.wait("MCP OAuth · review")
    terminal.resize(32, 12)
    terminal.keys("Enter")
    assert not (tmp_path / "oauth-launch.json").exists()
    assert not (tmp_path / "oauth-registration.json").exists()
    terminal.resize(80, 24)
    terminal.review_oauth()
    terminal.resize(32, 3)
    terminal.keys("Enter")
    time.sleep(0.1)
    assert not (tmp_path / "oauth-launch.json").exists()
    assert not (tmp_path / "oauth-registration.json").exists()
    terminal.resize(80, 24)
    terminal.review_oauth()
    terminal.keys("Enter")
    terminal.wait("MCP OAuth · waiting")
    launch = json.loads((tmp_path / "oauth-launch.json").read_text())
    assert (tmp_path / "oauth-registration.json").exists() is (registration == "dynamic")
    params = parse_qs(urlsplit(launch["authorization_url"]).query)
    callback = params["redirect_uri"][0] + "?" + urlencode({
        "state": params["state"][0], "code": "fixture-code", "iss": "https://issuer.test/tenant"})
    assert urlopen(callback, timeout=5).status == 200
    terminal.wait("MCP OAuth · authorized")
    if refresh:
        terminal.text("r")
        terminal.wait("MCP OAuth · refresh")
        terminal.keys("Enter")
        assert not (tmp_path / "oauth-refresh.json").exists()
        terminal.review_oauth()
        terminal.keys("Enter")
        terminal.wait("MCP OAuth · authorized")
        assert (tmp_path / "oauth-refresh.json").exists()
    terminal.keys("Escape")
    terminal.wait("MCP servers")
    terminal.wait("explicit external connections/data")
    terminal.text("c")
    terminal.wait("peer · connected")
    terminal.keys("Escape")
    terminal.wait_cursor(True)
    terminal.text("parent draft")
    terminal.wait_editor("parent draft")
    terminal.finish()
    assert b"FIXTURE-OAUTH-SECRET" not in terminal.raw.read_bytes()
    assert b"TERMINAL-CLIENT-PRIVATE-SECRET" not in terminal.raw.read_bytes()


@pytest.mark.parametrize("registration,client_auth,refresh", [("pre-registered", "none", False),
    ("dynamic", "none", False), ("pre-registered", "client_secret_basic", False),
    ("pre-registered", "client_secret_jwt", False), ("pre-registered", "none", True),
    ("pre-registered", "private_key_jwt", True)])
def test_actual_claw_oauth_consent_callback_and_socket_terminal_restore(
    terminal, tmp_path, registration, client_auth, refresh,
):
    import asyncio
    import importlib.util
    from types import SimpleNamespace
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlencode, urlsplit
    from urllib.request import urlopen

    import httpx
    import xdog.agent.mcp.http as mcp_http
    from xdog.agent.mcp.oauth import OAuthOwner
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer
    spec = importlib.util.spec_from_file_location("oauth_peer", Path(__file__).parents[2]
                                                / "agent/tests/fixtures/oauth_ui_peer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    peer = module.Peer()
    peer.issue_refresh = refresh
    if client_auth != "none":
        peer.method, peer.secret = client_auth, "TERMINAL-CLIENT-PRIVATE-SECRET-" + "x" * 48
    factory = httpx.AsyncClient
    namespace = SimpleNamespace(AsyncClient=lambda **kwargs: factory(transport=peer.transport, **kwargs),
                               Limits=httpx.Limits, HTTPError=httpx.HTTPError, Headers=httpx.Headers)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    profile = {"issuer": module.ISSUER, "scopes": ["tools:read"], "registration": registration}
    if registration == "pre-registered":
        profile["client_id"] = "registered-client"
    if client_auth != "none":
        profile.update(token_auth_method=client_auth, client_secret_env="XDOG_TERMINAL_OAUTH_SECRET")
        if client_auth == 'private_key_jwt':
            import re
            peer.secret = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA',
                '-pkeyopt', 'rsa_keygen_bits:2048'], capture_output=True, check=True).stdout.decode()
            public = subprocess.run(['openssl', 'pkey', '-text_pub', '-noout'], input=peer.secret.encode(),
                                    capture_output=True, check=True).stdout.decode()
            modulus = int(re.search(r'Modulus:\s*([0-9a-f:\s]+)Exponent:', public)[1].replace(':', '')
                          .replace('\n', '').replace(' ', ''), 16)
            peer.public_rsa = modulus, int(re.search(r'Exponent: ([0-9]+)', public)[1])
            profile.pop('client_secret_env')
            profile['client_private_key_env'] = 'XDOG_TERMINAL_OAUTH_SECRET'
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "oauth.sock"), pid_file=str(tmp_path / "oauth.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": profile}})
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned["loop"] = loop
        server = GatewayServer(config)
        try:
            with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
                loop.run_until_complete(server.start())
            owned["manager"] = server._orchestrator._runtimes["main"].mcp
            ready.set()
            loop.run_forever()
        except BaseException as exc:
            errors.append(exc)
            ready.set()
        finally:
            loop.run_until_complete(server.stop())
            loop.close()
    with (patch.object(OAuthOwner, "client", lambda self: factory(transport=peer.transport)),
          patch.object(mcp_http, "httpx", namespace),
          patch.dict(os.environ, {"XDOG_TERMINAL_OAUTH_SECRET": peer.secret})):
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        assert ready.wait(6) and not errors
        try:
            terminal.start("claw", config.socket_path)
            terminal.text("/mcp authorize peer")
            terminal.keys("Enter")
            terminal.wait("MCP OAuth · review")
            terminal.resize(32, 12)
            terminal.keys("Enter")
            assert owned["manager"]._oauth["peer"].flow is None
            assert not peer.registration_posts
            terminal.resize(80, 24)
            terminal.review_oauth()
            terminal.keys("Enter")
            terminal.wait("MCP OAuth · waiting")
            assert len(peer.registration_posts) == (1 if registration == "dynamic" else 0)
            url = owned["manager"]._oauth["peer"].flow.url
            peer.launch(url)
            params = parse_qs(urlsplit(url).query)
            callback = params["redirect_uri"][0] + "?" + urlencode({
                "state": params["state"][0], "code": "fixture-code", "iss": module.ISSUER})
            assert urlopen(callback, timeout=5).status == 200
            terminal.wait("MCP OAuth · authorized")
            if refresh:
                terminal.text("r")
                terminal.wait("MCP OAuth · refresh")
                terminal.keys("Enter")
                assert not peer.refresh_posts
                terminal.review_oauth()
                terminal.keys("Enter")
                terminal.wait("MCP OAuth · authorized")
                assert len(peer.refresh_posts) == 1
            assert not calls and not owned["manager"].tools()
            terminal.keys("Escape")
            terminal.wait("MCP servers")
            terminal.wait("explicit external connections/data")
            terminal.text("c")
            terminal.wait("peer · connected")
            terminal.keys("Escape")
            terminal.wait_cursor(True)
            terminal.text("parent draft")
            terminal.wait_editor("parent draft")
            terminal.finish()
            assert b"FIXTURE-OAUTH-SECRET" not in terminal.raw.read_bytes()
            assert b"TERMINAL-CLIENT-PRIVATE-SECRET" not in terminal.raw.read_bytes()
        finally:
            owned["loop"].call_soon_threadsafe(owned["loop"].stop)
            thread.join(6)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('reference_kind', ['named', 'opaque'])
def test_actual_mcp_argument_completion_is_explicit_inert_and_restores_parent_terminal(
        terminal, tmp_path, kind, reference_kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'
    log = tmp_path / 'completion-requests'
    servers = {'peer': {'command': sys.executable, 'args': [str(fixture), 'modern', str(log)]}}
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'coding-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
                            socket_path=str(tmp_path / 'completion.sock'), pid_file=str(tmp_path / 'completion.pid'),
                            groups=(GroupDef(id='main', is_main=True),), mcp_servers=servers)
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('peer · disconnected')
        assert not log.exists()
        terminal.text('c')
        terminal.wait('peer · connected')
        if reference_kind == 'opaque':
            terminal.text('v')
            terminal.wait('MCP resource_arguments')
            terminal.keys('C-u')
            terminal.text(json.dumps({'uri': 'fixture://text', 'argument': 'opaque-field',
                                      'value': 'pre', 'context': {'language': 'python'}}))
            terminal.keys('C-Space')
            terminal.wait('MCP completion · resource · opaque-field')
            terminal.keys('Tab')
            terminal.wait('2 suggestions')
            terminal.keys('Enter')
            terminal.wait('MCP resource_arguments')
            terminal.wait_wrapped_editor('OPAQUE-ARGUMENT-VALUE')
            assert not calls
            terminal.keys('Enter')
            terminal.wait('MCP preview')
            terminal.wait('Operator-selected/edited')
            frames = [json.loads(line) for line in log.read_text().splitlines()]
            assert sum(frame['method'] == 'completion/complete' for frame in frames) == 1
            assert not any(frame['method'] in ('resources/read', 'prompts/get', 'tools/call') for frame in frames)
            terminal.text('i')
            terminal.wait_hidden('MCP preview')
            terminal.wait_editor('}')
            assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
            terminal.finish()
            return
        terminal.text('p')
        terminal.wait('▶ review · review')
        terminal.keys('Enter')
        terminal.wait('MCP arguments')
        terminal.keys('C-Space')
        terminal.wait('MCP completion · prompt · topic')
        terminal.keys('Tab')
        terminal.wait('2 suggestions')
        terminal.resize(20, 5)
        terminal.keys('Enter')
        terminal.resize(80, 24)
        terminal.wait('MCP completion · prompt · topic')
        terminal.wait('2 suggestions')
        terminal.keys('Enter')
        terminal.wait('MCP arguments')
        terminal.wait('completed value')
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(frame['method'] == 'prompts/get' for frame in frames)
        terminal.keys('Enter')
        terminal.wait('MCP preview')
        terminal.wait('PROMPT-completed value')
        terminal.keys('Escape')
        terminal.wait('MCP servers · peer · explicit external connections/data')
        terminal.text('t')
        terminal.wait('fixture://document/{name}')
        terminal.keys('Enter')
        terminal.wait('MCP uri')
        terminal.keys('C-Space')
        terminal.wait('MCP completion · template · name')
        terminal.keys('Tab')
        terminal.wait('2 suggestions')
        terminal.keys('Enter')
        terminal.wait('MCP uri')
        terminal.wait('fixture://document/completed%20value')
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(frame['method'] == 'completion/complete' for frame in frames) == 2
        assert not any(frame['method'] in ('resources/read', 'tools/call') for frame in frames)
        assert 'FIXTURE-ANSWER' not in terminal.pane() and not calls
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('completion parent draft')
        terminal.wait_editor('completion parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            # Settle the owned socket even when a pane assertion failed before /quit.
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('choice', ['array', 'error', 'content'])
def test_actual_rich_mcp_schema_nullable_arguments_structured_array_and_terminal_restore(
        terminal, tmp_path, kind, choice):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_schema_peer.py'
    log = tmp_path / 'rich-requests'
    servers = {'peer': {'command': sys.executable, 'args': [str(fixture), 'modern-' + choice, str(log)],
                        'allow_tools': True}}
    ready, errors, owned, contexts = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'coding-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
                           socket_path=str(tmp_path / 'rich.sock'), pid_file=str(tmp_path / 'rich.pid'),
                           groups=(GroupDef(id='main', is_main=True),), mcp_servers=servers)
        def stream(model, context, options):
            contexts.append(context)
            if len(contexts) == 1:
                tool = next(tool for tool in context.tools if tool.name.startswith('mcp_peer_'))
                return EventStream.empty(AssistantMessage(content=(ToolCall(id='rich', name=tool.name,
                                                                           arguments={'value': None}),),
                                                          stop_reason='toolUse'))
            return EventStream.empty(AssistantMessage(content=(TextContent(text='RICH-MCP-DONE'),)))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        assert not log.exists()
        if kind == 'claw':
            # Capability diagnostics must be visible, not merely retained in scrollback.
            terminal.resize(100, 40)
        terminal.text('/mcp connect peer')
        terminal.keys('Enter')
        terminal.wait('MCP peer: connect completed' if kind == 'coding' else '"state": "connected"')
        if kind == 'claw':
            terminal.resize(80, 24)  # Preserve the original geometry for every interaction below.
        terminal.text('mcp-rich')
        terminal.keys('Enter')
        if kind == 'coding':
            terminal.wait('Tool permission required')
            assert not any(json.loads(line)['method'] == 'tools/call' for line in log.read_text().splitlines())
            terminal.keys('Enter')
        terminal.wait(('IMAGE-RECEIVED-1' if choice in ('error', 'content') else 'FIXTURE-ANSWER')
                      if kind == 'coding' else 'RICH-MCP-DONE')
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        calls = [frame for frame in frames if frame['method'] == 'tools/call']
        assert len(calls) == 1 and calls[0]['params']['arguments'] == {'value': None}
        terminal.keys('C-o')
        if choice == 'content':
            terminal.keys('End')
        terminal.wait('MCP structured output')
        terminal.wait('ERROR-STRUCTURED-TAIL' if choice == 'error' else 'STRUCTURED-ARRAY-TAIL-987')
        if choice == 'content':
            if kind == 'claw':
                result = next(message for message in contexts[-1].messages if message.role == 'toolResult')
                assert result.details['mcp_content_metadata'][0]['metadata']['annotations']['priority'] == 0.25
                assert 'MCP-IMAGE-METADATA' in str(result.content)
            assert not any(frame['method'] == 'resources/read' for frame in frames)
        if choice == 'error':
            assert 'RICH-MCP-ERROR' in terminal.pane()
            if kind == 'claw':
                result = next(message for message in contexts[-1].messages if message.role == 'toolResult')
                assert result.is_error and result.details['structured_content']['retry'] is False
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('rich parent draft')
        terminal.wait_editor('rich parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('multi', [False, True], ids=['scalar', 'multi'])
def test_actual_extension_typed_form_review_cancel_defaults_and_terminal_restore(terminal, tmp_path, kind, multi):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    command = '/ext-dialogue-many' if multi else '/ext-dialogue-form'
    result_tag = 'MANY-RESULT' if multi else 'FORM-RESULT'
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'form-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['dialogue'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'forms.sock'), pid_file=str(tmp_path / 'forms.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('dialogue',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text(command)
        terminal.keys('Enter')
        if multi:
            terminal.wait('values [multi_choice] required')
            terminal.keys('Space')
            terminal.keys('Down')
            terminal.wait('Integer zero')
            terminal.keys('Space')
            terminal.keys('Down')
            terminal.wait('Explicit null')
            terminal.keys('Space')
            terminal.keys('Enter')
        else:
            terminal.wait('Task name [text] required')
            terminal.text('Alice')
            terminal.wait_editor('Alice')
            terminal.keys('Tab')
            terminal.wait('Count [integer]')
            terminal.keys('C-u')
            terminal.text('3')
            terminal.wait_editor('3')
            terminal.keys('Enter')
        terminal.wait('form review')
        terminal.keys('Enter')
        assert result_tag not in terminal.pane()
        terminal.resize(20, 5)
        terminal.keys('Enter')
        assert result_tag not in terminal.pane()
        terminal.resize(80, 24)
        previous = ''
        deadline = time.monotonic() + 10
        import re
        while time.monotonic() < deadline:
            pane = terminal.visible()
            if 'Enter submits reviewed form' in pane:
                break
            match = re.search(r'Review (\d+-\d+/\d+)', pane)
            if match and match[1] != previous:
                previous = match[1]
                terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Typed form did not complete visible review: ' + terminal.pane())
        terminal.keys('Enter')
        terminal.wait(result_tag + '-')
        if multi:
            terminal.wait('[false, 0, null]')
            terminal.wait('"empty": []')
        else:
            terminal.wait('"count": 3')
            terminal.wait('"enabled": false')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.wait_cursor(True)
        terminal.text(command)
        terminal.keys('Enter')
        terminal.wait('values [multi_choice] required' if multi else 'Task name [text] required')
        terminal.keys('Escape')
        terminal.wait(result_tag + '-null')
        terminal.wait_cursor(True)
        terminal.text('form parent draft')
        terminal.wait_editor('form parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('failure', [False, True])
def test_actual_extension_rich_tool_image_json_approval_and_terminal_restore(terminal, tmp_path, kind, failure):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, contexts = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'rich-extension-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['rich'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        if failure:
            terminal.child_env['XDOG_FIXTURE_RICH_ERROR'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'rich-ext.sock'), pid_file=str(tmp_path / 'rich-ext.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('rich',), extension_dir=str(root),
            extension_allow_tools=('rich',))
        def stream(model, context, options):
            contexts.append(context)
            if len(contexts) == 1:
                tool = next(tool for tool in context.tools if tool.name.startswith('ext_rich_'))
                return EventStream.empty(AssistantMessage(content=(ToolCall(id='rich', name=tool.name,
                    arguments={'value': 'error'} if failure else {}),),
                                                          stop_reason='toolUse'))
            return EventStream.empty(AssistantMessage(content=(TextContent(text='CLAW-RICH-EXT-DONE'),)))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('extension-rich')
        terminal.keys('Enter')
        if kind == 'coding':
            terminal.wait('Tool permission required')
            assert 'RICH-EXTENSION-RESULT' not in terminal.pane()
            terminal.keys('Enter')
        terminal.wait('RICH-EXTENSION-ERROR' if failure else 'RICH-EXTENSION-RESULT')
        terminal.wait('image/png')
        terminal.wait('IMAGE-RECEIVED-1' if kind == 'coding' else 'CLAW-RICH-EXT-DONE')
        terminal.keys('C-o')
        terminal.wait('MCP structured output')
        terminal.wait('ERROR-STRUCTURED-TAIL' if failure else '[false,0,null]')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        assert 'POST-MUST-NOT-DROP-IMAGE' not in terminal.pane()
        terminal.text('rich extension parent draft')
        terminal.wait_editor('rich extension parent draft')
        terminal.finish()
        assert b'iVBORw0KGgoAAAANS' not in terminal.raw.read_bytes()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_rich_extension_command_read_only_image_json_and_parent_restore(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'command-rich-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['rich'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'command-rich.sock'), pid_file=str(tmp_path / 'command-rich.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('rich',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-rich-show')
        terminal.keys('Enter')
        terminal.wait('Extension command /ext-rich-show')
        terminal.wait('RICH-COMMAND-OUTPUT')
        terminal.wait('image/png')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.keys('C-o')
        terminal.wait('Extension command structured output')
        terminal.wait('[false,0,null]')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.resize(24, 8)
        terminal.keys('C-l')
        terminal.resize(80, 24)
        terminal.text('command artifact parent draft')
        terminal.wait_editor('command artifact parent draft')
        terminal.finish()
        assert b'iVBORw0KGgoAAAANS' not in terminal.raw.read_bytes()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_lossless_command_artifact_export_review_reload_and_terminal_restore(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.agent.extensions.command_result import read_artifact
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'artifact-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['rich'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'artifact.sock'), pid_file=str(tmp_path / 'artifact.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('rich',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    target = tmp_path / 'artifact.json'
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-rich-show')
        terminal.keys('Enter')
        terminal.wait('RICH-COMMAND-OUTPUT')
        terminal.text('/export ' + str(target) + ' artifact')
        terminal.keys('Enter')
        terminal.wait('Review local export')
        assert not target.exists()
        terminal.resize(20, 5)
        terminal.keys('Enter')
        assert not target.exists()
        terminal.resize(120, 30)
        terminal.wait('Enter saves private export')
        terminal.keys('Enter')
        terminal.wait('Saved')
        assert target.is_file() and target.stat().st_mode & 0o777 == 0o600
        result = read_artifact(target)
        assert result.result['structuredContent'] == [False, 0, None]
        assert result.result['details']['enabled'] is False
        assert result.result['content'][1]['mimeType'] == 'image/png'
        terminal.text('/artifact ' + str(target))
        terminal.keys('Enter')
        terminal.wait('Reading a captured artifact')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if terminal.visible().count('Extension command /ext-rich-show') >= 2:
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Imported binary artifact did not render: ' + terminal.pane())
        terminal.wait('image/png')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.wait_cursor(True)
        terminal.text('artifact parent draft')
        terminal.wait_editor('artifact parent draft')
        terminal.finish()
        assert b'iVBORw0KGgoAAAANS' not in terminal.raw.read_bytes()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_command_editor_compare_swap_context_and_terminal_restore(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'editor-command-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['editor'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'editorcmd.sock'), pid_file=str(tmp_path / 'editorcmd.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('editor',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-editor-compose')
        terminal.keys('Enter')
        terminal.wait('EDITOR-APPLIED-True')
        terminal.wait_editor(' EXTENSION-COMPOSED')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.keys('C-c')
        terminal.text('editor parent draft')
        terminal.wait_editor('editor parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_command_native_session_browser_and_parent_draft_restore(terminal, tmp_path, kind):
    import asyncio
    import re
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'navigator-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'navcmd.sock'), pid_file=str(tmp_path / 'navcmd.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('navigator',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-navigator-inspect')
        terminal.keys('Enter')
        terminal.wait('SESSION-INSPECTION')
        terminal.wait('"source": "unavailable"')
        terminal.wait('"tokens":')
        assert re.search(r'"tokens":\s*null', terminal.visible())
        terminal.wait_cursor(True)
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.text('/ext-navigator-catalog sessions')
        terminal.keys('Enter')
        terminal.wait('CATALOG-PAGE-0-')
        terminal.wait_cursor(True)
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.text('/ext-navigator-catalog tree')
        terminal.keys('Enter')
        terminal.wait('CATALOG-PAGE-0-0')
        terminal.wait_cursor(True)
        terminal.text('/ext-navigator-browse')
        terminal.keys('Enter')
        terminal.wait('NAVIGATION-QUEUED-True')
        terminal.wait('Sessions ·')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('/ext-navigator-browse tree')
        terminal.keys('Enter')
        terminal.wait('Branch/tree ·')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('navigation command parent draft')
        terminal.wait_editor('navigation command parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_reviewed_new_session_navigation_and_terminal_restore(terminal, tmp_path, kind):
    import asyncio
    import re
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'reviewed-navigation-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'navreview.sock'), pid_file=str(tmp_path / 'navreview.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('navigator',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    pattern = r'session:([a-z0-9-]+)' if kind == 'coding' else r'session ([a-z0-9-]+)'
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        before = re.findall(pattern, terminal.visible())[-1]
        terminal.text('/ext-navigator-navigate new')
        terminal.keys('Enter')
        terminal.wait('Queue this exact navigation')
        terminal.keys('Right')
        terminal.keys('Enter')
        terminal.wait('form review')
        terminal.resize(20, 5)
        terminal.keys('Enter')
        assert not calls
        terminal.resize(80, 24)
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pane = terminal.visible()
            if 'Enter submits reviewed form' in pane:
                break
            match = re.search(r'Review (\d+-\d+/\d+)', pane)
            if match and match[1] != previous:
                previous = match[1]
                terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Exact navigation target review incomplete: ' + terminal.pane())
        terminal.keys('Enter')
        terminal.wait_hidden('form review')
        if kind == "coding":
            terminal.wait("New session:")
            pattern = r'New session: ([a-z0-9-]+)'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            matches = re.findall(pattern, terminal.visible())
            if matches and matches[-1] != before:
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Reviewed native session mutation not synchronized: ' + terminal.pane())
        terminal.wait_cursor(True)
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.text('reviewed navigation parent draft')
        terminal.wait_editor('reviewed navigation parent draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('remap', [False, True])
def test_actual_runtime_extension_shortcut_keys_help_reload_and_terminal_restore(terminal, tmp_path, kind, remap):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    home = tmp_path / 'shortcut-home'
    home.mkdir()
    if remap:
        (home / 'keybindings.json').write_text(json.dumps({'ext-keyed-compose': 'alt+x'}))
    thread = None
    if kind == 'coding':
        (home / 'settings.json').write_text(json.dumps({'extensions': ['keyed'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        terminal.child_env['CLAW_DIR'] = str(home)
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'shortcuts.sock'), pid_file=str(tmp_path / 'shortcuts.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('keyed',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-keyed-compose')
        terminal.text('/keys')
        terminal.keys('Enter')
        terminal.wait('Extension shortcuts (idle prompt only')
        terminal.wait('DISABLED')
        terminal.wait('conflicts with finish')
        terminal.wait_cursor(True)
        terminal.text('shortcut parent draft')
        terminal.wait_editor('shortcut parent draft')
        terminal.keys('M-x' if remap else 'M-k')
        terminal.wait('SHORTCUT-COMMAND-True')
        terminal.wait_editor('shortcut parent draft SHORTCUT-DRAFT')
        assert not calls and 'FIXTURE-ANSWER' not in terminal.pane()
        terminal.keys('C-c')
        terminal.text('/extensions unload keyed')
        terminal.keys('Enter')
        terminal.wait('unloaded')
        terminal.text('after unload draft')
        terminal.wait_editor('after unload draft')
        terminal.keys('M-x' if remap else 'M-k')
        terminal.wait_editor('after unload draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_submitted_input_transform_reject_recovery_and_terminal_restore(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, contexts = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'input-hook-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['inputter'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'input-hook.sock'), pid_file=str(tmp_path / 'input-hook.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('inputter',), extension_dir=str(root))
        def stream(model, context, options):
            contexts.append(context)
            assert context.messages[-1].content[0].text.startswith('INPUT-HOOK-')
            return EventStream.empty(AssistantMessage(content=(TextContent(text='INPUT-FILTERED-BY-FIXTURE'),)))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('terminal input')
        terminal.keys('Enter')
        terminal.wait('INPUT-FILTERED-BY-FIXTURE')
        terminal.wait('INPUT-HOOK-terminal input')
        terminal.text('hook-handled submitted request')
        terminal.keys('Enter')
        terminal.wait('INPUT-HANDLER-FEEDBACK')
        terminal.wait_cursor(True)
        assert 'not a model answer' in terminal.pane()
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '1'
        else:
            assert len(contexts) == 1
        terminal.text('hook-reject original input')
        terminal.keys('Enter')
        terminal.wait('Input not admitted')
        terminal.wait_editor('hook-reject original input')
        if kind == 'claw':
            assert len(contexts) == 1
        terminal.resize(36, 8)
        terminal.keys('C-l')
        terminal.wait_editor('hook-reject original input')
        terminal.resize(80, 24)
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('rich', [False, True])
def test_actual_custom_extension_frame_paste_resize_cancel_and_terminal_ownership(
    terminal, tmp_path, kind, child, rich,
):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'custom-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('custom',), extension_dir=str(root),
            extension_allow_tools=('custom',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_custom_'))
                message = AssistantMessage(content=(ToolCall(id='view', name=external.name,
                                                            arguments={'mode': 'rich' if rich else ''}),))
            else:
                message = AssistantMessage(content=(TextContent(text='child done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        command = (('/agents start --inherit child-custom-rich' if rich else '/agents start --inherit child-custom')
                   if child else '/ext-custom-view rich' if rich else '/ext-custom-view')
        for cancel in (False, True):
            terminal.text(command)
            terminal.keys('Enter')
            if child and kind == 'coding':
                terminal.wait('independent approval')
                terminal.keys('Enter')  # Real tool permission, not a custom view answer.
            if rich:
                terminal.wait('Rich introduction')
                terminal.keys('End')
                terminal.wait('INTRO-TAIL')
                terminal.keys('F1')
            terminal.wait('Custom view')
            terminal.wait_cursor(True)
            if rich:
                if not cancel:
                    terminal.keys('C-z')
                    terminal.wait('Stopped')
                    terminal.text('fg')
                    terminal.keys('Enter')
                    terminal.wait('Custom view')
                terminal.text('\x1b[113;1:2u')
                terminal.wait('repeat:q')
                terminal.text('\x1b[113;1:3u')
                terminal.wait('release:q')
            raw = terminal.raw.read_bytes()
            assert b'\x1b]52' not in raw and b'\x1b_pi:c\a' not in raw
            terminal.resize(20, 5)
            terminal.text('hidden-key')
            terminal.keys('Enter')
            terminal.call('set-buffer', 'hidden-paste\n')
            terminal.call('paste-buffer', '-p', '-t', 'test')
            assert '"selected": false' not in terminal.visible()
            terminal.resize(80, 24)
            terminal.wait('Custom view')
            terminal.wait('custom view')  # The complete host header must survive alternate-screen resize.
            terminal.call('set-buffer', 'custom-paste-one\ncustom-paste-two')
            terminal.call('paste-buffer', '-p', '-t', 'test')
            terminal.wait('custom-paste-one')
            assert 'hidden-key' not in terminal.visible() and 'hidden-paste' not in terminal.visible()
            terminal.keys('C-c' if cancel else 'Enter')
            terminal.wait_hidden('custom view')
            if child:
                import re
                terminal.wait('started (inherit)')  # Native return re-anchors before the new transcript paint.
                identifiers = re.findall(r'Child ([a-f0-9]{12}) started', terminal.pane())
                assert identifiers
                terminal.text('/agents inspect ' + identifiers[-1])
                terminal.keys('Enter')
                terminal.wait('"value": null' if cancel else '"selected": false')
                terminal.keys('Escape')
                terminal.wait('Child agents')
                terminal.keys('Escape')
            else:
                terminal.wait('"value": null' if cancel else '"selected": false')
            terminal.wait_cursor(True)
        if kind == 'coding':
            counter = tmp_path / 'fixture-model-count'
            assert counter.read_text() == '4' if child else not counter.exists()
        else:
            assert len(calls) == (4 if child else 0)
        terminal.text('custom parent draft')
        terminal.wait_editor('custom parent draft')
        terminal.finish(allow_fullscreen=rich)
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False, True])
def test_actual_nested_dialog_resumes_same_fullscreen_parent_and_native_terminal(terminal, tmp_path, kind, child):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'nested-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['nested'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'nested.sock'), pid_file=str(tmp_path / 'nested.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('nested',), extension_dir=str(root),
            extension_allow_tools=('nested',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_nested_'))
                message = AssistantMessage(content=(ToolCall(id='nested', name=external.name,
                                                            arguments={'mode': 'full'}),))
            else:
                message = AssistantMessage(content=(TextContent(text='done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/agents start --inherit child-nested' if child else '/ext-nested-view full')
        terminal.keys('Enter')
        if child and kind == 'coding':
            terminal.wait('independent approval')
            terminal.keys('Enter')
        terminal.wait('NESTED-PARENT')
        terminal.text('n')
        terminal.wait('Nested input')
        terminal.keys('C-u')
        terminal.text('nested-answer')
        terminal.wait_editor('nested-answer')
        terminal.resize(20, 5)
        terminal.keys('Enter')
        assert 'Nested confirm' not in terminal.visible()
        terminal.resize(80, 24)
        terminal.wait_editor('nested-answer')
        terminal.keys('Enter')
        terminal.wait('Nested confirm')
        terminal.keys('Down')
        terminal.keys('Enter')
        terminal.wait('NESTED-PARENT')
        terminal.wait('nested-answer')
        terminal.keys('Enter')
        terminal.wait_hidden('NESTED-PARENT')
        terminal.wait_cursor(True)
        if child:
            import re
            terminal.wait('started (inherit)')
            identifiers = re.findall(r'Child ([a-f0-9]{12}) started', terminal.pane())
            assert identifiers
            terminal.text('/agents inspect ' + identifiers[-1])
            terminal.keys('Enter')
            terminal.wait('"calls": 1')
            terminal.wait('nested-answer')
            terminal.keys('Escape')
            terminal.wait_hidden('Child ' + identifiers[-1] + ' · snapshot')
            terminal.wait('Child agents')
            terminal.keys('Escape')
            terminal.wait_hidden('Children focused' if kind == 'coding' else 'Child browser')
        else:
            terminal.wait('"calls": 1')
        if kind == 'coding':
            counter = tmp_path / 'fixture-model-count'
            assert counter.read_text() == '2' if child else not counter.exists()
        else:
            assert len(calls) == (2 if child else 0)
        terminal.text('nested parent draft')
        terminal.wait_editor('nested parent draft')
        terminal.finish(allow_fullscreen=True)
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


def test_actual_coding_terminal_hooks_fifo_atomic_paste_pause_recovery_and_native_restore(terminal, tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    source = root / 'terminal/main.py'
    records_file = tmp_path / 'terminal-hook-records.jsonl'
    source.write_text(source.read_text().replace("return str(calls)", "return 'TERMINAL-COUNT-' + str(calls)")
        .replace('        calls += 1', '        calls += 1\n'
            '        import json\n        from pathlib import Path\n'
            f"        with Path({str(records_file)!r}).open('a') as output:\n"
            "            output.write(json.dumps({'data': data, 'kind': metadata['kind']}) + '\\n')\n"))
    home = tmp_path / 'terminal-hook-home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
    terminal.child_env['CODING_DIR'] = str(home)
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    terminal.start('coding')
    terminal.text('/extensions')
    terminal.keys('Enter')
    terminal.wait('"state": "active"')
    terminal.wait_hidden('Esc cancel command')
    terminal.text('ab')
    terminal.wait_editor('[a][b]')
    terminal.text('\x1b[200~drop\x1b[201~')
    terminal.wait_editor('[a][b]')
    terminal.text('\x1b[200~multi\nline\x1b[201~')
    terminal.wait_wrapped_editor('[a][b][multi')
    terminal.keys('Enter')
    terminal.wait('FIXTURE-ANSWER')
    assert (tmp_path / 'fixture-model-count').read_text() == '1'
    terminal.text('z')
    terminal.wait_editor('[z]')
    terminal.keys('M-i')
    terminal.wait('Terminal input hooks paused')
    terminal.wait('Retained terminal inputs')
    terminal.keys('C-c')
    terminal.text('/ext-terminal-count')
    terminal.keys('Enter')
    terminal.wait_hidden('Esc cancel command')
    records = [json.loads(line) for line in records_file.read_text().splitlines()]
    assert ''.join(row['data'] for row in records if row['kind'] == 'key') == 'abz'
    assert [row['data'] for row in records if row['kind'] == 'paste'] == ['drop', 'multi\nline']
    terminal.wait('TERMINAL-COUNT-' + str(len(records)))
    terminal.keys('M-i')
    terminal.text('\x1b[200~quiet\x1b[201~')
    terminal.text('after normal')
    terminal.wait_editor('after normal')
    terminal.keys('C-c')
    terminal.text('/ext-terminal-enable')
    terminal.keys('Enter')
    terminal.wait('Terminal hook enabled')
    terminal.wait_hidden('Esc cancel command')
    terminal.text('x')
    terminal.wait_editor('[x]')
    records = [json.loads(line) for line in records_file.read_text().splitlines()]
    assert ''.join(row['data'] for row in records if row['kind'] == 'key') == 'abzx'
    assert [row['data'] for row in records if row['kind'] == 'paste'] == ['drop', 'multi\nline', 'quiet']
    terminal.keys('M-i')
    assert (tmp_path / 'fixture-model-count').read_text() == '1'
    terminal.finish()


def test_actual_coding_pending_terminal_hook_resize_pauses_and_recovers_original_draft_only(terminal, tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':", "        if data == '~':\n"
        "            from pathlib import Path\n"
        "            Path('terminal-hook-held').write_text('held')\n"
        "            await asyncio.Event().wait()\n        if data == 'bad':"))
    home = tmp_path / 'terminal-hook-home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
    terminal.child_env['CODING_DIR'] = str(home)
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    terminal.start('coding')
    terminal.text('/extensions')
    terminal.keys('Enter')
    terminal.wait('"state": "active"')
    terminal.wait_hidden('Esc cancel command')
    terminal.text('~')
    deadline = time.monotonic() + 8
    while not (tmp_path / 'terminal-hook-held').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (tmp_path / 'terminal-hook-held').exists()
    terminal.resize(60, 20)
    terminal.wait('hooks paused')
    terminal.resize(80, 24)
    terminal.text('/terminal-input restore 1')
    terminal.keys('Enter')
    terminal.wait('Original terminal text restored to editable draft')
    terminal.wait_editor('~')
    assert not (tmp_path / 'fixture-model-count').exists()
    terminal.finish()


@pytest.mark.parametrize('scenario', ['fifo', 'resize'])
def test_actual_claw_terminal_hooks_gateway_pause_recovery_and_native_restore(terminal, tmp_path, scenario):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    marker = tmp_path / 'terminal-hook-held'
    records_file = tmp_path / 'terminal-hook-records.jsonl'
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace("return str(calls)", "return 'TERMINAL-COUNT-' + str(calls)")
        .replace('        calls += 1', '        calls += 1\n'
            '        import json\n        from pathlib import Path\n'
            f"        with Path({str(records_file)!r}).open('a') as output:\n"
            "            output.write(json.dumps({'data': data, 'kind': metadata['kind']}) + '\\n')\n")
        .replace("        if data == 'bad':", "        if data == '~':\n"
                 "            from pathlib import Path\n"
                 f"            Path({str(marker)!r}).write_text('held')\n"
                 "            await asyncio.Event().wait()\n        if data == 'bad':"))
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'terminal-hook.sock'), pid_file=str(tmp_path / 'terminal-hook.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('terminal',), extension_dir=str(root))
    calls, errors, owned, ready = [], [], {}, threading.Event()
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='FIXTURE-ANSWER'),)))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned['loop'] = loop
        server = GatewayServer(config)
        try:
            with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
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
    assert ready.wait(8) and not errors
    try:
        terminal.start('claw', config.socket_path)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('"state": "active"')
        terminal.wait_hidden('Esc cancel command')
        if scenario == 'fifo':
            terminal.text('ab')
            terminal.wait_editor('[a][b]')
            terminal.text('\x1b[200~drop\x1b[201~')
            terminal.wait_editor('[a][b]')
            terminal.text('\x1b[200~multi\nline\x1b[201~')
            terminal.wait_wrapped_editor('[a][b][multi')
            terminal.keys('Enter')
            terminal.wait('FIXTURE-ANSWER')
            assert len(calls) == 1 and calls[0].messages[-1].content[0].text == '[a][b][multi\nline]'
            terminal.text('z')
            terminal.wait_editor('[z]')
            terminal.keys('M-i')
            terminal.wait('Terminal input hooks paused')
            terminal.keys('C-c')
            terminal.text('/ext-terminal-count')
            terminal.keys('Enter')
            records = [json.loads(line) for line in records_file.read_text().splitlines()]
            assert ''.join(row['data'] for row in records if row['kind'] == 'key') == 'abz'
            assert [row['data'] for row in records if row['kind'] == 'paste'] == ['drop', 'multi\nline']
            terminal.wait('TERMINAL-COUNT-' + str(len(records)))
            terminal.keys('M-i')
            terminal.text('\x1b[200~quiet\x1b[201~')
            terminal.text('after normal')
            terminal.wait_editor('after normal')
            terminal.keys('C-c')
            terminal.text('/ext-terminal-enable')
            terminal.keys('Enter')
            terminal.wait('Terminal hook enabled')
            terminal.wait_hidden('Esc cancel command')
            terminal.text('x')
            terminal.wait_editor('[x]')
            records = [json.loads(line) for line in records_file.read_text().splitlines()]
            assert ''.join(row['data'] for row in records if row['kind'] == 'key') == 'abzx'
            assert [row['data'] for row in records if row['kind'] == 'paste'] == ['drop', 'multi\nline', 'quiet']
            terminal.keys('M-i')
            assert len(calls) == 1
        else:
            terminal.text('~')
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists()
            terminal.resize(60, 20)
            terminal.wait('hooks paused')
            terminal.resize(80, 24)
            terminal.text('/terminal-input restore 1')
            terminal.keys('Enter')
            terminal.wait('Original terminal text restored to editable draft')
            terminal.wait_editor('~')
            assert not calls
        terminal.finish()
    finally:
        terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
        owned['loop'].call_soon_threadsafe(owned['loop'].stop)
        thread.join(6)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_owned_child_terminal_editor_review_cancel_and_parent_native_restore(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, StartEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    records = tmp_path / 'child-terminal-records.jsonl'
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace('        calls += 1', '        calls += 1\n'
        '        import json\n        from pathlib import Path\n'
        f"        with Path({str(records)!r}).open('a') as output:\n"
        "            output.write(json.dumps({'data': data, 'kind': metadata['kind'], "
        "'child': context.get('child_id', '')}) + '\\n')\n"))
    calls, errors, owned, ready = [], [], {}, threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'child-terminal-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'child-terminal.sock'), pid_file=str(tmp_path / 'child-terminal.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('terminal',), extension_dir=str(root))
        def stream(model, context, options):
            calls.append(context)
            async def generate():
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            return EventStream(generate())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('"state": "active"')
        terminal.wait_hidden('Esc cancel command')
        terminal.keys('M-i')  # Parent hook cannot rewrite the owned start command.
        terminal.text('/agents start child-hold')
        terminal.keys('Enter')
        terminal.wait('started')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if (calls if kind == 'claw' else (tmp_path / 'fixture-model-count').exists()):
                break
            time.sleep(0.02)
        terminal.text('/agents')
        terminal.keys('Enter')
        terminal.wait('Child agents')
        terminal.wait('child-hold')
        terminal.text('s')
        terminal.wait('Child steer')
        terminal.keys('M-i')
        terminal.text('ab')
        terminal.wait_editor('[a][b]')
        terminal.text('\x1b[200~drop\x1b[201~')
        terminal.text('\x1b[200~multi\nline\x1b[201~')
        terminal.wait_wrapped_editor('[a][b][multi')
        terminal.keys('Enter')
        terminal.wait('Confirm child action')
        logged = [json.loads(line) for line in records.read_text().splitlines()]
        assert ''.join(row['data'] for row in logged if row['kind'] == 'key') == 'ab'
        assert [row['data'] for row in logged if row['kind'] == 'paste'] == ['drop', 'multi\nline']
        child_ids = {row['child'] for row in logged}
        assert len(child_ids) == 1 and '' not in child_ids
        terminal.keys('Escape')
        terminal.wait('Child steer')
        terminal.wait_wrapped_editor('[a][b][multi')  # Canceling review preserves the child draft.
        terminal.keys('C-c')
        terminal.wait('Child agents')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.keys('M-i')
        terminal.text('parent draft after child')
        terminal.wait_editor('parent draft after child')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '1'
        else:
            assert len(calls) == 1
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('presentation', ['inline', 'fullscreen', 'overlay'])
@pytest.mark.parametrize('scroll', [False, True], ids=['static', 'scroll'])
def test_actual_structured_layout_edit_nested_resize_and_terminal_owner(terminal, tmp_path, kind, presentation, scroll):
    import asyncio
    import re
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fullscreen = presentation == 'fullscreen'
    overlay = presentation == 'overlay'
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    errors, owned, calls, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'layout-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['layout'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'layout.sock'), pid_file=str(tmp_path / 'layout.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('layout',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-layout-view' + (' full' if fullscreen else '') + (' scroll' if scroll else '')
                      + (' overlay' if overlay else ''))
        terminal.keys('Enter')
        terminal.wait('STRUCTURED-LAYOUT')
        terminal.wait('Draft · 编辑')
        terminal.wait_cursor(True)
        terminal.text('界e\u0301')
        terminal.wait('界e\u0301')
        if scroll:
            terminal.wait('ROW-0000')
            terminal.keys('NPage')
            terminal.wait('ROW-0020')
            terminal.keys('F3')
            terminal.wait('PAN-0020')
        terminal.keys('F5')
        terminal.wait('LAYER-POPUP')
        terminal.wait_cursor(False)
        before_frame = int(re.search(r'LAYER-POPUP W\d+ R(\d+)', terminal.visible())[1])
        terminal.resize(50, 14)
        terminal.wait('LAYER-POPUP W' + str(37 if overlay else 50))
        terminal.resize(80, 24)
        wanted = 60 if overlay else 80
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            match = re.search(r'LAYER-POPUP W' + str(wanted) + r' R(\d+)', terminal.visible())
            if match and int(match[1]) > before_frame:
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Fresh post-resize popup frame was not painted:\n' + terminal.visible())
        terminal.keys('F5')
        terminal.wait_hidden('LAYER-POPUP')
        terminal.wait_cursor(True)
        terminal.wait('界e\u0301')
        terminal.keys('F2')
        terminal.wait('Nested layout question')
        terminal.keys('C-u')
        terminal.text('nested answer')
        terminal.keys('Enter')
        terminal.wait('STRUCTURED-LAYOUT')
        terminal.wait('界e\u0301')
        terminal.resize(50, 14)
        terminal.wait('STRUCTURED-LAYOUT')
        terminal.wait('界e\u0301')
        terminal.resize(80, 24)
        terminal.wait('STRUCTURED-LAYOUT')
        terminal.wait('界e\u0301')
        if scroll:
            terminal.wait('PAN-0020')
            terminal.keys('PPage')
            terminal.wait('PAN-0000')
            terminal.keys('F4')
            terminal.wait('ROW-0000')
        terminal.keys('Enter')
        terminal.wait('nested answer')
        terminal.wait('"calls": 1')
        terminal.wait_hidden('Layout fixture')
        terminal.text('restored parent draft')
        terminal.wait_editor('restored parent draft')
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        else:
            assert not calls
        assert b'\x1b]52;c;unsafe' not in terminal.raw.read_bytes()
        terminal.finish(allow_fullscreen=fullscreen)
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('mode', ['modern', 'legacy', 'modern-end', 'official-sdk'])
def test_actual_mcp_resource_watch_notice_unwatch_no_read_or_model_and_native_restore(terminal, tmp_path, kind, mode):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    if mode == 'official-sdk' and (not os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT') or not shutil.which('node')):
        pytest.skip('requires independent pinned MCP SDK 2.3.1')
    source = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'
    peer = tmp_path / 'subscription-peer.py'
    text = source.read_text()
    text = text.replace("    elif method == 'resources/subscribe':",
        "        write({'method': 'notifications/resources/updated', 'params': "
        "{'uri': uris[0], '_meta': {'io.modelcontextprotocol/subscriptionId': identifier}}})\n"
        "    elif method == 'resources/subscribe':")
    text = text.replace("    elif method == 'resources/unsubscribe':",
        "        write({'method': 'notifications/resources/updated', "
        "'params': {'uri': params['uri']}})\n    elif method == 'resources/unsubscribe':")
    text = text.replace("    elif method == 'resources/read':",
        "        if mode == 'modern-end':\n"
        "            for sid in list(subscriptions):\n"
        "                write({'id': sid, 'result': {'resultType': 'complete'}})\n"
        "            subscriptions.clear()\n"
        "    elif method == 'resources/read':")
    peer.write_text(text)
    log, calls, errors, owned, ready = tmp_path / 'watch-requests.jsonl', [], [], {}, threading.Event()
    if mode == 'official-sdk':
        peer = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_official_sdk/peer.mjs'
        mcp = {'peer': {'command': shutil.which('node'), 'args': [str(peer), 'stdio', str(log)],
                        'env': {'XDOG_MCP_SDK_AUTO_NOTICE': '1'}}}
    else:
        mcp = {'peer': {'command': sys.executable, 'args': [str(peer), mode, str(log)]}}
    thread = None
    if kind == 'coding':
        home = tmp_path / 'resource-watch-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': mcp}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'watch.sock'), pid_file=str(tmp_path / 'watch.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=mcp)
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        if kind == 'claw':
            # Full capability diagnostics otherwise push the connected row into
            # preserved scrollback; this gate deliberately checks visible status.
            terminal.resize(100, 40)
        terminal.text('/mcp connect peer')
        terminal.keys('Enter')
        terminal.wait('connect completed' if kind == 'coding' else '"state": "connected"')
        if kind == 'claw':
            terminal.resize(80, 24)  # Watch/form/completion checks keep their original geometry.
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('MCP servers')
        terminal.wait('▶ peer · connected')
        terminal.text('u')
        terminal.wait('test://watched')
        terminal.text('w')
        terminal.wait('1 change notice')
        if mode == 'modern-end':
            terminal.keys('Escape')
            terminal.wait('▶ peer · connected')
            terminal.text('u')  # The peer ends the live watch during a read-only catalog refresh.
            terminal.wait('watch ended')
            assert log.read_text().count('subscriptions/listen') == 1
            terminal.text('w')  # Re-opening is explicit, not a background replay.
            terminal.wait('watch active')
        terminal.text('w')
        terminal.wait('Watch stopped')
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.wait('▶ peer · connected')
        if mode == 'official-sdk':
            terminal.text('p')
            terminal.wait('▶ review · review')
            terminal.keys('Enter')
            terminal.wait('Named string arguments')
            terminal.keys('C-Space')
            terminal.wait('MCP completion · prompt · topic')
            terminal.keys('Tab')
            terminal.wait('SDK-TOPIC')
            before = [json.loads(line) for line in log.read_text().splitlines()]
            assert not any(item['type'] in {'prompt-get', 'template-read', 'resource-read'} for item in before)
            terminal.keys('Enter')  # Inserts a chosen value into the argument form, not a prompt read.
            terminal.wait('Enter separately retrieves preview')
            terminal.keys('Enter')  # This distinct gesture retrieves the prompt/link metadata.
            terminal.wait('Resource link metadata; NOT fetched')
            terminal.text('/')
            terminal.text('SDK-RESOURCE-LINK')  # Search full raw metadata, beyond the 80-column preview slice.
            terminal.keys('Enter')
            terminal.wait('1/1')
            assert 'SDK-RESOURCE-LINK' in terminal.visible()
            terminal.text('i')  # Explicitly insert untrusted metadata into an editable draft, not a model turn.
            terminal.wait_hidden('MCP focused')
            terminal.wait_editor('metadata"}}')
            after = [json.loads(line) for line in log.read_text().splitlines()]
            assert sum(item['type'] == 'prompt-get' for item in after) == 1
            assert not any(item['type'] in {'template-read', 'resource-read'} for item in after)
            terminal.keys('C-c')  # Clear this explicitly inserted, unsubmitted draft before the recovery assertion.
        else:
            terminal.keys('Escape')
            terminal.wait_hidden('MCP focused')
        terminal.text('parent draft after watch')
        terminal.wait_editor('parent draft after watch')
        if mode == 'official-sdk':
            messages = [json.loads(line) for line in log.read_text().splitlines()]
            assert not any(message['type'] == 'resource-read' for message in messages)
            acknowledgements = [message for message in messages if message['type'] == 'outbound'
                                and message['message'].get('method') == 'notifications/subscriptions/acknowledged']
            assert len(acknowledgements) == 1
        else:
            messages = [json.loads(line) for line in log.read_text().splitlines()]
            methods = [message['method'] for message in messages]
            assert 'resources/read' not in methods
            assert methods.count('subscriptions/listen') == (2 if mode == 'modern-end' else int(mode == 'modern'))
            assert (methods.count('resources/subscribe') == methods.count('resources/unsubscribe')
                    == int(mode == 'legacy'))
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_private_credential_save_restore_erase_consent_and_terminal_recovery(terminal, tmp_path, kind):
    import asyncio
    import importlib.util
    from types import SimpleNamespace
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlencode, urlsplit
    from urllib.request import urlopen

    import httpx
    import xdog.agent.mcp.http as mcp_http
    from xdog.agent.mcp.oauth import OAuthOwner
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    spec = importlib.util.spec_from_file_location('credential_peer', Path(__file__).parents[2]
        / 'agent/tests/fixtures/oauth_ui_peer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    peer = module.Peer()
    factory = httpx.AsyncClient
    namespace = SimpleNamespace(AsyncClient=lambda **kwargs: factory(transport=peer.transport, **kwargs),
        Limits=httpx.Limits, HTTPError=httpx.HTTPError, Headers=httpx.Headers)
    ready, errors, calls, owned = threading.Event(), [], [], {}
    thread = None
    mcp = {'peer': {'transport': 'http', 'url': module.RESOURCE, 'oauth': module.PROFILE}}
    if kind == 'coding':
        home = tmp_path / 'credential-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': mcp}))
        directory = home / 'mcp-credentials'
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_OAUTH='1', XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'credential.sock'), pid_file=str(tmp_path / 'credential.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=mcp)
        directory = tmp_path / 'data/groups/main/mcp-credentials'
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
                    loop.run_until_complete(server.start())
                owned['manager'] = server._orchestrator._runtimes['main'].mcp
                ready.set()
                loop.run_forever()
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                loop.run_until_complete(server.stop())
                loop.close()
        thread = threading.Thread(target=serve, daemon=True)
    with (patch.object(OAuthOwner, 'client', lambda self: factory(transport=peer.transport)),
          patch.object(mcp_http, 'httpx', namespace)):
        if thread is not None:
            thread.start()
            assert ready.wait(8) and not errors
        try:
            terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
            terminal.text('/mcp authorize peer')
            terminal.keys('Enter')
            terminal.wait('MCP OAuth · review')
            terminal.review_oauth()
            terminal.keys('Enter')
            terminal.wait('MCP OAuth · waiting')
            launch = (json.loads((tmp_path / 'oauth-launch.json').read_text())['authorization_url']
                      if kind == 'coding' else owned['manager']._oauth['peer'].flow.url)
            if kind == 'claw':
                peer.launch(launch)
            args = parse_qs(urlsplit(launch).query)
            callback = args['redirect_uri'][0] + '?' + urlencode({'state': args['state'][0],
                'code': 'fixture-code', 'iss': module.ISSUER})
            assert urlopen(callback, timeout=5).status == 200
            terminal.wait('MCP OAuth · authorized')
            terminal.text('p')
            terminal.wait('MCP OAuth · credentials_save')
            terminal.keys('Enter')
            assert not directory.exists()
            terminal.review_oauth()
            terminal.keys('Enter')
            terminal.wait('MCP OAuth · authorized')
            file = next(directory.glob('*.json'))
            assert file.stat().st_mode & 0o777 == 0o600 and directory.stat().st_mode & 0o777 == 0o700
            terminal.keys('Escape')
            terminal.wait('MCP servers')
            terminal.wait('explicit external connections/data')
            terminal.wait('▶ peer')
            terminal.text('l')
            terminal.wait('MCP OAuth · credentials_restore')
            terminal.review_oauth()
            terminal.keys('Enter')
            terminal.wait('MCP OAuth · authorized')
            terminal.keys('Escape')
            terminal.wait('MCP servers')
            terminal.wait('explicit external connections/data')
            terminal.wait('▶ peer')
            terminal.text('x')
            terminal.wait('MCP OAuth · credentials_forget')
            assert file.exists()
            terminal.review_oauth()
            terminal.keys('Enter')
            terminal.wait('MCP OAuth · authorized')
            assert not file.exists()
            terminal.keys('Escape')
            terminal.wait('MCP servers')
            terminal.keys('Escape')
            terminal.wait_hidden('MCP focused')
            terminal.text('parent draft after private storage')
            terminal.wait_editor('parent draft after private storage')
            assert not calls and not (tmp_path / 'fixture-model-count').exists()
            pane, raw = terminal.pane(), terminal.raw.read_bytes()
            assert 'UI-RESOURCE-BOUND-SECRET' not in pane and b'UI-RESOURCE-BOUND-SECRET' not in raw
            terminal.finish()
        finally:
            if thread is not None:
                terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
                owned['loop'].call_soon_threadsafe(owned['loop'].stop)
                thread.join(6)
                assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('model_id', ['echo', 'failure', 'aborted', 'dynamic', 'caps'])
def test_actual_session_extension_provider_catalog_selection_stream_and_terminal_recovery(
        terminal, tmp_path, kind, model_id):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    extension = ('providercaps' if model_id == 'caps' else
                 'providerdynamic' if model_id == 'dynamic' else 'providermock')
    alias = ('ext-providercaps-local/gpt-5.1/code:preview' if model_id == 'caps' else
             'ext-providerdynamic-live/echo' if model_id == 'dynamic' else 'ext-providermock-local/' + model_id)
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, errors, owned, ready = [], [], {}, threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'provider-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': [extension], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='base/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'provider.sock'), pid_file=str(tmp_path / 'provider.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=(extension,), extension_dir=str(root))
        def base(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('base/model', base)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        if model_id == 'dynamic':
            terminal.resize(80, 32)  # Default width; allow space for the longer registration catalog.
        terminal.text('/extensions')
        terminal.keys('Enter')
        if model_id == 'dynamic':
            terminal.wait_json_field('terminal_generation', 1)
        else:
            terminal.wait_json_field('state', 'active')
        terminal.wait_hidden('Esc cancel command')
        if model_id == 'dynamic':
            terminal.text('/ext-providerdynamic-enable')
            terminal.keys('Enter')
            terminal.wait('DYNAMIC-ENABLED')
            terminal.wait_hidden('Esc cancel command')
        terminal.text('/model')
        terminal.keys('Enter')
        terminal.wait(alias if model_id in {'dynamic', 'caps'} else 'ext-providermock-local/echo')
        terminal.wait_hidden('Esc cancel command')
        terminal.text('/model ' + alias)
        terminal.keys('Enter')
        terminal.wait('Switched to model' if kind == 'coding' else 'Extension model: ' + alias)
        terminal.wait_hidden('Esc cancel command')
        if model_id == 'caps':
            terminal.wait('30k' if kind == 'coding' else '48k')
            if kind == 'coding':
                terminal.text('/thinking high')
                terminal.keys('Enter')
                terminal.wait('Thinking level set to: high')
                terminal.wait_hidden('Esc cancel command')
            else:
                terminal.text('/thinking')
                terminal.keys('Enter')
                terminal.wait('available: off, low, high')
                terminal.text('/thinking high')
                terminal.keys('Enter')
                terminal.wait('Thinking level: high')
                terminal.wait('thinking:high')
        terminal.text('use session provider')
        terminal.keys('Enter')
        terminal.wait('CAPABILITY-PROVIDER-ANSWER' if model_id == 'caps' else
                      'DYNAMIC-PROVIDER-ANSWER' if model_id == 'dynamic' else
                      'EXT-PROVIDER-ANSWER' if model_id == 'echo' else 'EXT-PROVIDER-PARTIAL')
        if model_id in {'failure', 'aborted'}:
            terminal.wait('Provider canceled' if model_id == 'aborted' else 'Provider unavailable')
            assert 'EXT-PROVIDER-PARTIAL' in terminal.visible()  # Failure cannot erase already emitted content.
        terminal.wait('ready' if kind == 'coding' else 'idle')
        if kind == 'claw' and model_id in {'failure', 'aborted'}:
            terminal.keys('C-c')  # Clear the intentionally restored failed input; never replay it.
        if model_id == 'caps':
            terminal.text('/ext-providercaps-count')
            terminal.keys('Enter')
            terminal.wait('CALLS=1')
            terminal.wait_hidden('Esc cancel command')
        elif model_id == 'dynamic':
            terminal.text('/ext-providerdynamic-replace')
            terminal.keys('Enter')
            terminal.wait('DYNAMIC-REPLACED')
            terminal.wait('16k')  # Current metadata, not the original selected model's 48k limit.
            terminal.wait_hidden('Esc cancel command')
            terminal.text('use replaced text-only provider')
            terminal.keys('Enter')
            terminal.wait('TEXT-ONLY-NO-API-TOOLS')
            terminal.wait('DYNAMIC-PROVIDER-ANSWER')
            terminal.wait('ready' if kind == 'coding' else 'idle')
            terminal.wait_cursor(True)
            terminal.text('/ext-providerdynamic-disable')
            terminal.keys('Enter')
            terminal.wait('DYNAMIC-DISABLED')
            terminal.wait_hidden('Esc cancel command')
            terminal.text('/ext-providerdynamic-count')
            terminal.keys('Enter')
            terminal.wait('CALLS=2;CLOSED=2')
        else:
            terminal.text('/ext-providermock-count')
            terminal.keys('Enter')
            deadline = time.monotonic() + 8
            while (time.monotonic() < deadline
                   and not any(line.strip() == '1' for line in terminal.visible().splitlines())):
                time.sleep(0.02)
            assert any(line.strip() == '1' for line in terminal.pane().splitlines())
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.text('parent provider draft')
        terminal.wait_editor('parent provider draft')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_alias_startup_and_saved_restart_restore_without_primary_fallback(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    alias = 'ext-providermock-local/echo'
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, errors, owned, ready = [], [], {}, threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'startup-provider-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'default_model': 'terminal-fixture',
            'extensions': ['providermock'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model=alias, data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'startup-provider.sock'), pid_file=str(tmp_path / 'startup-provider.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('providermock',), extension_dir=str(root))
        def base(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(alias, base)):
                    loop.run_until_complete(server.start())
                owned['server'] = server
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *(['--model', alias] if kind == 'coding' else [config.socket_path]),
                       expected='coding | ext-providermock' if kind == 'coding' else 'connected')
        terminal.text('startup alias prompt')
        terminal.keys('Enter')
        terminal.wait('EXT-PROVIDER-ANSWER')
        terminal.wait('ready' if kind == 'coding' else 'idle')
        terminal.finish()
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
            sessions = list((home / 'sessions').rglob('*.json'))
            metadata = next(json.loads(path.read_text()) for path in sessions
                            if json.loads(path.read_text()).get('model') == alias)
            resume_key = metadata.get('session_id', metadata.get('id'))
            offset = len(terminal.raw.read_bytes())
            terminal.start('coding', '--resume-id', resume_key, expected='coding | ext-providermock')
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                new_output = terminal.raw.read_bytes()[offset:]
                if b'Enter send' in new_output and b'thinking:' in new_output:
                    break
                time.sleep(0.02)
            assert b'Enter send' in terminal.raw.read_bytes()[offset:]
            terminal.text('resumed alias prompt')
            terminal.keys('Enter')
            terminal.wait('EXT-PROVIDER-ANSWER')
            terminal.wait('ready')
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.text('provider startup parent draft')
            terminal.wait_editor('provider startup parent draft')
            terminal.finish()
        else:
            assert not calls
            # The saved-selection daemon restart is verified with its real socket
            # in the dedicated startup test; here assert readiness/model and main
            # terminal ownership for the actual shipped process.
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


def test_actual_claw_native_model_catalog_selection_thinking_and_parent_terminal_recovery(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.ai.types import AssistantMessage, Model, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    calls, owned, errors, ready = [], {}, [], threading.Event()
    class Provider:
        def models(self):
            return (Model(id='native/first', context_window=48000, max_tokens=2048),
                    Model(id='native/second', context_window=64000, max_prompt_tokens=40000, max_tokens=1024,
                          reasoning=True, supported_efforts=('low', 'high'), supports_tool_calls=False,
                          headers={'Authorization': 'SECRET-MUST-NOT-LEAK'}))
        def model(self, name):
            return next((row for row in self.models() if row.id == name or row.id.split('/')[1] == name), None)
        def stream(self, model, context, options):
            calls.append((model, context, options))
            return EventStream.empty(AssistantMessage(content=(TextContent(text='NATIVE-MODEL-ANSWER'),)))
    provider = Provider()
    config = ClawConfig(model='native/first', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'native.sock'), pid_file=str(tmp_path / 'native.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned['loop'] = loop
        server = GatewayServer(config)
        try:
            with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                       return_value=('native/first', stream_fn_from_provider(provider))):
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
    assert ready.wait(8) and not errors
    try:
        terminal.start('claw', config.socket_path)
        terminal.text('/model')
        terminal.keys('Enter')
        terminal.wait('native/second')
        assert not calls
        terminal.text('/model second')
        terminal.keys('Enter')
        terminal.wait('Model: native/second')
        terminal.wait('64k')
        terminal.text('/thinking high')
        terminal.keys('Enter')
        terminal.wait('thinking:high')
        assert not calls
        terminal.text('use selected native model')
        terminal.keys('Enter')
        terminal.wait('NATIVE-MODEL-ANSWER')
        assert len(calls) == 1 and calls[0][0] == 'native/second'
        assert calls[0][2].thinking == 'high' and not calls[0][1].tools
        terminal.text('native parent draft')
        terminal.wait_editor('native parent draft')
        terminal.finish()
        assert b'SECRET-MUST-NOT-LEAK' not in terminal.raw.read_bytes()
    finally:
        terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
        owned['loop'].call_soon_threadsafe(owned['loop'].stop)
        thread.join(6)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False, True])
def test_actual_callback_inspection_tool_child_source_and_terminal_recovery(terminal, tmp_path, kind, child):
    import asyncio
    import re
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, Usage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'inspection-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['inspector'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        class Adapter:
            def resolve_model(self, name):
                return Model(id=name, context_window=64000, max_prompt_tokens=48000,
                    reasoning=name == 'fixture/reviewer', supported_efforts=('low', 'medium'))
            def __call__(self, model, context, options):
                calls.append((model, context))
                if context.messages[-1].role == 'user':
                    tool = next(tool for tool in context.tools if tool.name.startswith('ext_inspector_'))
                    message = AssistantMessage(model=model, content=(ToolCall(id='inspect', name=tool.name,
                        arguments={}),), usage=Usage(input=11, output=2), stop_reason='toolUse')
                else:
                    message = AssistantMessage(model=model, content=(TextContent(text='INSPECTION-TURN-DONE'),))
                return EventStream.empty(message)
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'callback.sock'), pid_file=str(tmp_path / 'callback.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('inspector',), extension_dir=str(root),
            extension_allow_tools=('inspector',))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', Adapter())):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/agents start --inherit --model fixture/reviewer --thinking low callback-inspection'
                      if child else 'callback-inspection')
        terminal.keys('Enter')
        if kind == 'coding':
            terminal.wait('Tool permission required')
            terminal.keys('Enter')  # Actual original parent/child approval; inspection is not approval.
        if child:
            # Inspect the actual completed child record, not a client-only clone.
            deadline = time.monotonic() + 10
            identifier = ''
            while time.monotonic() < deadline:
                match = re.search(r'([a-f0-9]{12})\s+(?:started|running|completed)', terminal.pane())
                if match:
                    identifier = match[1]
                    break
                time.sleep(0.03)
            assert identifier, terminal.pane()
            terminal.text('/agents inspect ' + identifier)
            terminal.keys('Enter')
            terminal.wait('Child snapshot')
            terminal.text('l')  # A queued snapshot stays inert until explicit live refresh is requested.
            terminal.wait('· LIVE ·')
        else:
            terminal.wait('INSPECTION-TURN-DONE')
            terminal.keys('C-o')
        terminal.wait('CALLBACK-INSPECTION')
        if child:
            terminal.text('/')
            terminal.text('last-response')
            terminal.keys('Enter')
            terminal.wait('1/1 matches')
            terminal.keys('S-Left', 'S-Left')  # Reveal the adjacent field key as well as its matched value.
        else:
            terminal.keys('End')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            painted = ''.join(terminal.visible().split())
            if '"source":"last-response"' in painted and re.search(r'"tokens":13[,}]', painted):
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Measured callback source/count was not visibly navigable:\n' + terminal.visible())
        terminal.resize(50, 14)
        terminal.resize(80, 24)
        terminal.keys('Escape')
        if child:
            terminal.wait_hidden('Transcript (inline)')
            terminal.keys('Escape')
            terminal.wait_hidden('Children focused')
        else:
            terminal.wait_hidden('Details focused')
        terminal.wait_cursor(True)
        terminal.text('callback inspection parent draft')
        terminal.wait_editor('callback inspection parent draft')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '2'
        else:
            assert len(calls) == 2
            assert {model for model, _ctx in calls} == {'fixture/reviewer' if child else 'test/model'}
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('paged', [False, True], ids=['wide', 'paged'])
def test_actual_empty_binary_mcp_preview_typed_private_export_and_parent_recovery(terminal, tmp_path, kind, paged):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'
    log = tmp_path / 'empty-requests'
    servers = {'fixture': {'command': sys.executable, 'args': [str(fixture), 'empty-content', str(log)]}}
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'empty-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'empty.sock'), pid_file=str(tmp_path / 'empty.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),), mcp_servers=servers)
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    target = tmp_path / (('界e\u0301-' * 18 + 'last.json') if paged else 'empty-export.json')
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('fixture · disconnected')
        terminal.text('c')
        terminal.wait('fixture · connected')
        terminal.text('u')
        terminal.wait('fixture://empty')
        terminal.keys('Enter')
        terminal.wait('0 bytes')
        terminal.text('i')
        terminal.wait('Binary MCP content cannot')
        terminal.wait('MCP preview')
        terminal.text('e')
        terminal.wait('Review local export')
        terminal.resize(120, 30)
        terminal.wait('Enter reviews path')
        terminal.keys('C-u')
        terminal.text(str(target))
        terminal.wait_editor("last.json" if paged else str(target))
        terminal.keys('Enter')
        if paged:
            terminal.resize(36, 8)
            terminal.wait('Review output')
            terminal.keys('Enter')
            assert not target.exists()
            terminal.review_output()
            terminal.text('v')
            terminal.wait('no save in preview')
            terminal.keys('Enter')
            assert not target.exists()
            terminal.text('v')
            terminal.review_output()
        else:
            terminal.wait('Enter saves private export')
        assert not target.exists()
        terminal.keys('Enter')
        if paged:
            deadline = time.monotonic() + 10
            while not target.exists() and time.monotonic() < deadline:
                time.sleep(0.03)
            assert target.exists(), terminal.pane()
            terminal.resize(80, 24)
            assert 'Saved' in terminal.pane()
        else:
            terminal.wait('Saved')
        saved = json.loads(json.loads(target.read_text())['records'][0]['body'])
        assert saved['blocks'][0]['content_type'] == 'resource_blob' and saved['blocks'][0]['data'] == ''
        assert target.stat().st_mode & 0o777 == 0o600
        terminal.wait('MCP preview')
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP focused')
        terminal.wait_cursor(True)
        terminal.text('empty binary parent draft')
        terminal.wait_editor('empty binary parent draft')
        frames = [json.loads(line)['method'] for line in log.read_text().splitlines()]
        assert frames.count('resources/read') == 1 and 'tools/call' not in frames
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_live_hook_disposal_metadata_input_and_native_parent_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'hook-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['hookmock'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'hooks.sock'), pid_file=str(tmp_path / 'hooks.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('hookmock',), extension_dir=str(root))
        def stream(model, context, options):
            calls.append(context)
            return EventStream.empty(AssistantMessage(content=(TextContent(text=f'HOOK-TURN-DONE-{len(calls)}'),)))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    def command(name, marker):
        terminal.text('/ext-hookmock-' + name)
        terminal.keys('Enter')
        terminal.wait(marker)
        terminal.wait_cursor(True)
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        command('enable', 'HOOKS-ENABLED')
        terminal.text('first-live-hook')
        terminal.keys('Enter')
        terminal.wait('LISTENER-first-live-hook')
        terminal.wait('FIXTURE-ANSWER' if kind == 'coding' else 'HOOK-TURN-DONE-1')
        terminal.wait_cursor(True)
        command('disable', 'HOOKS-DISABLED')
        terminal.text('second-live-hook')
        terminal.keys('Enter')
        terminal.wait('second-live-hook')
        assert 'LISTENER-second-live-hook' not in terminal.pane()
        # The status command executes after the model worker settles, not at the
        # first cursor paint while a queued callback still owns the turn.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if ((tmp_path / 'fixture-model-count').exists()
                    and (tmp_path / 'fixture-model-count').read_text() == '2') if kind == 'coding' else len(calls) == 2:
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Second explicit model turn did not settle')
        if kind == 'claw':
            terminal.wait('HOOK-TURN-DONE-2')
            terminal.wait('connected | idle')
        terminal.wait_cursor(True)
        command('status', '"event": 1')
        terminal.wait('"input": 1')
        terminal.resize(44, 10)
        terminal.resize(80, 24)
        terminal.text('hook lifecycle parent draft')
        terminal.wait_editor('hook lifecycle parent draft')
        if kind == 'claw':
            text = calls[-1].messages[-1].content
            assert 'LISTENER-' not in str(text)
        assert not errors
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_status_widget_clear_and_native_editing_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'ui-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['uiclear'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'ui.sock'), pid_file=str(tmp_path / 'ui.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('uiclear',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-uiclear-show')
        terminal.keys('Enter')
        terminal.wait('UI-SHOWN')
        terminal.wait('STATUS-LIVE')
        terminal.wait('WIDGET-LIVE')
        terminal.wait_cursor(True)
        terminal.text('/ext-uiclear-hide')
        terminal.keys('Enter')
        terminal.wait('UI-CLEARED')
        terminal.wait_hidden('STATUS-LIVE')
        terminal.wait_hidden('WIDGET-LIVE')
        terminal.wait_cursor(True)
        terminal.resize(40, 8)
        terminal.resize(80, 24)
        terminal.text('cleared widget parent draft')
        terminal.wait_editor('cleared widget parent draft')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_dynamic_command_shortcut_replace_dispose_and_parent_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'command-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['commandmock'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'commands.sock'), pid_file=str(tmp_path / 'commands.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandmock',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    def command(name, marker):
        terminal.text('/ext-commandmock-' + name)
        terminal.keys('Enter')
        terminal.wait(marker)
        terminal.wait_cursor(True)
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        command('enable', 'COMMAND-ENABLED')
        terminal.keys('F6')
        terminal.wait('LIVE-COMMAND-ORIGINAL-1')
        terminal.wait_cursor(True)
        command('replace', 'COMMAND-REPLACED')
        terminal.keys('F6')
        terminal.wait('LIVE-COMMAND-REPLACED-2')
        terminal.wait_cursor(True)
        command('disable', 'COMMAND-DISPOSED')
        terminal.text('kept dynamic catalog draft')
        terminal.wait_editor('kept dynamic catalog draft')
        terminal.keys('F6')
        terminal.wait_editor('kept dynamic catalog draft')
        assert 'LIVE-COMMAND-REPLACED-3' not in terminal.pane()
        terminal.resize(44, 10)
        terminal.resize(80, 24)
        terminal.wait_editor('kept dynamic catalog draft')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_live_tool_replace_schema_permissions_and_native_parent_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'tool-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['toolmock'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'tools.sock'), pid_file=str(tmp_path / 'tools.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('toolmock',), extension_dir=str(root),
            extension_allow_tools=('toolmock',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                tool = next(tool for tool in context.tools if tool.name.startswith('ext_toolmock_target_'))
                value = 7 if tool.parameters['properties']['value']['type'] == 'integer' else 'first'
                message = AssistantMessage(content=(ToolCall(id='dynamic', name=tool.name,
                                                            arguments={'value': value}),))
            else:
                message = AssistantMessage(content=(TextContent(text='TOOL-TURN-DONE-' + str(len(calls))),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    def command(name, marker):
        terminal.text('/ext-toolmock-' + name)
        terminal.keys('Enter')
        terminal.wait(marker)
        terminal.wait_cursor(True)
    def prompt(marker, count):
        terminal.text('live-dynamic-tool')
        terminal.keys('Enter')
        if kind == 'coding':
            terminal.wait('Tool permission required')
            terminal.keys('Enter')
        terminal.wait(marker)
        if kind == 'claw':
            terminal.wait('TOOL-TURN-DONE-' + str(count))
            terminal.wait('connected | idle')
        else:
            terminal.wait('FIXTURE-ANSWER')
        terminal.wait_cursor(True)
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        command('enable', 'TOOL-ENABLED')
        prompt('LIVE-TOOL-ORIGINAL-first', 2)
        command('replace', 'TOOL-REPLACED')
        prompt('LIVE-TOOL-REPLACED-7', 4)
        command('disable', 'TOOL-DISPOSED')
        terminal.resize(44, 10)
        terminal.resize(80, 24)
        terminal.text('disposed live tool parent draft')
        terminal.wait_editor('disposed live tool parent draft')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '4'
        else:
            assert len(calls) == 4
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_mcp_catalog_watch_notice_toggle_no_read_and_native_parent_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'
    log = tmp_path / 'catalog-watch-requests'
    servers = {'peer': {'command': sys.executable, 'args': [str(fixture), 'catalog-auto', str(log)]}}
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'watch-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'catalog.sock'), pid_file=str(tmp_path / 'catalog.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=servers)
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('peer · disconnected')
        terminal.text('c')
        terminal.wait('peer · connected')
        before = log.read_text().splitlines()
        terminal.keys('M-t')
        terminal.wait('tools catalog')
        terminal.wait('1 change notice')
        terminal.keys('M-t')
        terminal.wait('Watch stopped')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP focused')
        terminal.wait_cursor(True)
        terminal.resize(44, 10)
        terminal.resize(80, 24)
        terminal.text('catalog watch parent draft')
        terminal.wait_editor('catalog watch parent draft')
        frames = [json.loads(line)['method'] for line in log.read_text().splitlines()[len(before):]]
        assert frames.count('subscriptions/listen') == 1
        assert not any(method in ('tools/list', 'resources/list', 'prompts/list', 'resources/read', 'tools/call')
                       for method in frames)
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_live_custom_background_repaint_without_keys_and_native_recovery(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'custom-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['liveview'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('liveview',), extension_dir=str(root),
            extension_allow_tools=('liveview',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_liveview_'))
                message = AssistantMessage(content=(ToolCall(id='view', name=external.name,
                                                            arguments={'mode': ''}),))
            else:
                message = AssistantMessage(content=(TextContent(text='child done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-liveview-view')
        terminal.keys('Enter')
        terminal.wait('LIVE-STATE=2;INPUTS=0')  # No key/paste/resize drives these worker updates.
        terminal.resize(60, 18)
        terminal.wait('LIVE-STATE=2;INPUTS=0')
        terminal.keys('Enter')
        terminal.wait('"state": 2, "inputs": 1')
        terminal.wait_hidden('custom view')
        assert not calls
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('live parent recovered')
        terminal.wait_editor('live parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_mcp_combined_watch_edit_notice_toggle_no_read_and_native_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'
    log = tmp_path / 'catalog-watch-requests'
    servers = {'peer': {'command': sys.executable, 'args': [str(fixture), 'catalog-auto', str(log)]}}
    calls, owned, errors, ready = [], {}, [], threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'watch-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'catalog.sock'), pid_file=str(tmp_path / 'catalog.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=servers)
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('peer · disconnected')
        terminal.text('c')
        terminal.wait('peer · connected')
        before = log.read_text().splitlines()
        terminal.keys('M-w')
        terminal.wait('MCP subscriptions')
        terminal.keys('Enter')
        terminal.wait('combined notification filters')
        terminal.wait('3 change notice')
        terminal.keys('Enter')
        terminal.wait('Watch stopped')
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP focused')
        terminal.wait_cursor(True)
        terminal.resize(44, 10)
        terminal.resize(80, 24)
        terminal.text('catalog watch parent draft')
        terminal.wait_editor('catalog watch parent draft')
        frames = [json.loads(line)['method'] for line in log.read_text().splitlines()[len(before):]]
        assert frames.count('subscriptions/listen') == 1
        assert not any(method in ('tools/list', 'resources/list', 'prompts/list', 'resources/read', 'tools/call')
                       for method in frames)
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_live_overlay_parent_edit_background_controls_and_native_recovery(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'custom-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['overlaymock'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('overlaymock',), extension_dir=str(root),
            extension_allow_tools=('overlaymock',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_overlaymock_'))
                message = AssistantMessage(content=(ToolCall(id='view', name=external.name,
                                                            arguments={'mode': ''}),))
            else:
                message = AssistantMessage(content=(TextContent(text='child done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/ext-overlaymock-view auto')
        terminal.keys('Enter')
        terminal.wait('LIVE-OVERLAY initial')
        terminal.wait_cursor(True)
        terminal.text('edited under overlay')
        terminal.wait_editor('edited under overlay')
        terminal.wait('LIVE-OVERLAY show')
        terminal.wait_editor('edited under overlay')
        terminal.wait('LIVE-OVERLAY focus')
        terminal.wait('INPUTS=0')
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.wait('LIVE-OVERLAY focus')
        terminal.keys('Enter')
        terminal.wait('"inputs": ["enter"]')
        terminal.wait_hidden('custom view')
        assert not calls
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.wait_editor('edited under overlay')
        terminal.text(' recovered')
        terminal.wait_editor('edited under overlay recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_tool_inventory_read_only_command_native_recovery(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'custom-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['toolinspector'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('toolinspector',), extension_dir=str(root),
            extension_allow_tools=('toolinspector',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_toolinspector_'))
                message = AssistantMessage(content=(ToolCall(id='view', name=external.name,
                                                            arguments={'mode': ''}),))
            else:
                message = AssistantMessage(content=(TextContent(text='child done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/ext-toolinspector-inspect count')
        terminal.keys('Enter')
        terminal.wait('TOOL-INVENTORY')
        terminal.wait('"api_enabled": true')
        terminal.wait('invocation-configured-tools')
        terminal.wait('ext_toolinspector_inspect_')
        terminal.wait('"command_count": 1')
        terminal.text('/ext-toolinspector-inspect count')
        terminal.keys('Enter')
        terminal.wait('"command_count": 2')
        assert 'Tool permission required' not in terminal.visible() and not calls
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('inventory parent recovered')
        terminal.wait_editor('inventory parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_tools_schema_browser_search_refresh_fullscreen_and_native_recovery(terminal, tmp_path, kind):
    import asyncio
    import shutil
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    if kind == 'coding':
        home = tmp_path / 'custom-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['toolinspector'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('toolinspector',), extension_dir=str(root),
            extension_allow_tools=('toolinspector',))
        def stream(model, context, options):
            calls.append(context)
            if context.messages[-1].role == 'user':
                external = next(tool for tool in context.tools if tool.name.startswith('ext_toolinspector_'))
                message = AssistantMessage(content=(ToolCall(id='view', name=external.name,
                                                            arguments={'mode': ''}),))
            else:
                message = AssistantMessage(content=(TextContent(text='child done'),))
            return EventStream.empty(message)
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/tools')
        terminal.keys('Enter')
        terminal.wait('Tools (inline)')
        terminal.wait('NOT execution approval')
        terminal.text('/')
        terminal.text('filesystem')
        terminal.keys('Enter')
        terminal.wait('tool: filesystem')
        terminal.keys('f')
        terminal.wait('Tools (fullscreen)')
        terminal.keys('r')
        terminal.wait('Configured snapshot')
        terminal.keys('f')
        terminal.wait('Tools (inline)')
        terminal.keys('Escape')
        terminal.wait_hidden('Tools (inline)')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('tools browser parent recovered')
        terminal.wait_editor('tools browser parent recovered')
        terminal.finish(allow_fullscreen=True)
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_skill_selection_no_model_and_native_parent_recovery(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    calls, errors, owned = [], [], {}
    ready = threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'skill-home'
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        directory = home / 'skills'
    else:
        directory = tmp_path / 'data' / 'skills'
    skill = directory / 'guard'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: guard\ndescription: TERMINAL-SKILL-CATALOG\n---\nSKILL-BODY')
    if kind == 'claw':
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'skills.sock'), pid_file=str(tmp_path / 'skills.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/guard' if kind == 'coding' else '/skills load guard')
        terminal.keys('Enter')
        terminal.wait('Activated skill: guard' if kind == 'coding' else 'Skills · active: guard')
        terminal.text('/unload guard' if kind == 'coding' else '/skills unload guard')
        terminal.keys('Enter')
        terminal.wait('Unloaded skill: guard' if kind == 'coding'
                      else 'Selection only; no model/tool/permission change')
        terminal.text('/skills')
        terminal.keys('Enter')
        terminal.wait('TERMINAL-SKILL-CATALOG')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('skill parent recovered')
        terminal.wait_editor('skill parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('compactor', [False, True])
def test_actual_manual_compaction_selected_adapter_and_native_parent_restore(terminal, tmp_path, kind, compactor):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
    from xdog.claw.core.runtime.gateway import GatewayServer
    from xdog.coding.core.session_manager import SessionManager

    root = tmp_path / 'extensions'
    if compactor:
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    messages = []
    for index in range(4):
        messages.extend([UserMessage(content=f'old user {index} ' + 'x' * 3000),
                         AssistantMessage(content=(TextContent(text=f'old answer {index}'),))])
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'compact-home'
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        store = SessionManager(home / 'sessions')
        data = store.create_session(working_dir=tmp_path, model='terminal-fixture')
        data.messages = messages
        store.save_session(data)
        if compactor:
            (home / 'settings.json').write_text(json.dumps({'extensions': ['compactor'], 'extension_dir': str(root)}))
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'compact.sock'), pid_file=str(tmp_path / 'compact.pid'),
            groups=(GroupDef(id='main', is_main=True),),
            extensions=('compactor',) if compactor else (), extension_dir=str(root) if compactor else '')
        def stream(model, context, options):
            calls.append((model, context))
            return EventStream.empty(AssistantMessage(content=(TextContent(text='NATIVE-COMPACT-SUMMARY'),)))
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
                    loop.run_until_complete(server.start())
                session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
                session._runtime.flush_runner = None
                session.agent.replace_messages(messages)
                session._restored = True
                session._persisted_count = len(messages)
                session._store.replace_transcript(session.meta.session_id, messages_to_transcript(messages))
                owned['session'] = session
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
    try:
        if kind == 'coding':
            terminal.start(kind, '--resume-id', data.session_id, '--model', 'terminal-fixture',
                           '--permission-mode', 'deny')
        else:
            terminal.start(kind, config.socket_path)
        terminal.resize(120, 30)
        terminal.text('/compact Keep decisions and tool evidence')
        terminal.keys('Enter')
        terminal.wait('Compacted: 8 →')
        if kind == 'coding':
            assert len(store.load_session(data.session_id).messages) < 8
            if compactor:
                saved = store.load_session(data.session_id)
                assert saved.compaction['source'] == 'compactor'
                assert 'CUSTOM-COMPACTION-SUMMARY' in str(saved.messages)
                assert not (tmp_path / 'fixture-model-count').exists()
            else:
                assert (tmp_path / 'fixture-model-count').read_text() == '1'
        else:
            assert len(owned['session'].agent.state.messages) < 8
            if compactor:
                saved = owned['session']._store.load_transcript(owned['session'].meta.session_id)
                assert saved[0]['compaction']['source'] == 'compactor'
                assert 'CUSTOM-COMPACTION-SUMMARY' in str(saved)
                assert not calls
            else:
                assert len(calls) == 1 and calls[0][0] == 'test/model' and not calls[0][1].tools
                assert 'Keep decisions' in str(calls[0][1].messages)
        terminal.text('/tree')
        terminal.keys('Enter')
        terminal.wait('Before compaction #1')
        terminal.keys('v')
        terminal.wait('Saved context')
        terminal.keys('/')
        terminal.text('old user 0')
        terminal.keys('Enter')
        terminal.wait('old user 0')
        if kind == 'coding':
            assert len(store.load_session(data.session_id).messages) < 8
        else:
            assert len(owned['session']._store.load_transcript(owned['session'].meta.session_id)) < 8
        terminal.keys('C-c')  # Close regardless of query/selection state; never activate history.
        terminal.wait_hidden('Saved context')
        terminal.wait_cursor(True)  # Confirm the actual source viewer closed, not only an older visible label.
        terminal.text('/tree')
        terminal.keys('Enter')
        terminal.wait_cursor(False)  # New native navigation surface has actually painted.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            rows = terminal.visible().splitlines()
            headers = [index for index, row in enumerate(rows) if 'Branch/tree ·' in row]
            current = rows[headers[-1]:] if headers else []
            if (current and '6 entries' in current[0] and 'loading' not in current[0]
                    and any('→ [branch] Before compaction #1' in row for row in current)):
                break
            time.sleep(0.03)
        else:
            raise AssertionError('Recovery tree did not settle: ' + terminal.visible())
        terminal.keys('Down', 'Up')
        terminal.text('\x1b[13;1:1u')  # Explicit physical press through the production enhanced-key parser.
        if kind == 'coding':
            terminal.wait('Restored branch:')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            count = (len(store.load_session(data.session_id).messages) if kind == 'coding'
                     else len(owned['session']._store.load_transcript(owned['session'].meta.session_id)))
            if count == 8:
                break
            time.sleep(0.03)
        assert count == 8, terminal.visible()
        terminal.wait('old answer 3')
        if kind == 'coding':
            assert len(store.load_session(data.session_id).messages) == 8
            recovered_calls = int((tmp_path / 'fixture-model-count').read_text()) if not compactor else 0
            assert recovered_calls == (0 if compactor else 1)
        else:
            recovered = owned['session']._store.load_transcript(owned['session'].meta.session_id)
            assert len(recovered) == 8 and 'old user 0' in str(recovered)
            assert len(calls) == (0 if compactor else 1)
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('compaction parent recovered')
        terminal.wait_editor('compaction parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


def test_actual_coding_native_adapter_selection_rejection_and_terminal_draft_recovery(terminal, tmp_path):
    from xdog.coding.core.session_manager import SessionManager
    home = tmp_path / 'owned-native-home'
    terminal.child_env['CODING_DIR'] = str(home)
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    terminal.start('coding')
    terminal.resize(120, 30)
    terminal.text('/model fixture/reviewer')
    terminal.keys('Enter')
    terminal.wait('Switched to model: fixture/reviewer')
    saved = SessionManager(home / 'sessions').get_most_recent()
    assert saved.model == 'fixture/reviewer' and saved.settings['model'] == 'fixture/reviewer'
    terminal.text('/model foreign/unavailable')
    terminal.keys('Enter')
    terminal.wait('Model unavailable on the configured adapter')
    assert SessionManager(home / 'sessions').get_most_recent().model == 'fixture/reviewer'
    terminal.text('/thinking high')
    terminal.keys('Enter')
    terminal.wait('Thinking level not advertised by selected model')
    assert SessionManager(home / 'sessions').get_most_recent().settings['thinking_level'] == 'off'
    assert not (tmp_path / 'fixture-model-count').exists()
    terminal.resize(60, 18)
    terminal.resize(80, 24)
    terminal.text('native adapter parent recovered')
    terminal.wait_editor('native adapter parent recovered')
    terminal.finish()


@pytest.mark.parametrize('available', [False, True])
def test_actual_coding_staged_saved_provider_restore_preserves_failed_owner_and_native_terminal(
    terminal, tmp_path, available,
):
    from xdog.ai.types import AssistantMessage, UserMessage
    from xdog.coding.core.session_manager import SessionManager
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    home = tmp_path / 'staged-home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['stagedprovider'], 'extension_dir': str(root)}))
    terminal.child_env['CODING_DIR'] = str(home)
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    manager = SessionManager(home / 'sessions')
    target = manager.create_session(model='ext-stagedprovider-local/choice', working_dir=tmp_path)
    target.messages = [UserMessage(content='TARGET-STAGING-CONTEXT'), AssistantMessage()]
    target.settings = {'thinking_level': 'low'}
    target.extension_entries = {'stagedprovider': [{'id': 'flags', 'kind': 'note', 'data': {'allow': available}}]}
    manager.save_session(target)
    original_target = manager._session_path(target.session_id).read_bytes()
    terminal.start('coding')
    terminal.resize(120, 30)
    original = next(row.session_id for row in manager.list_sessions() if row.session_id != target.session_id)
    terminal.text('/resume ' + target.session_id)
    terminal.keys('Enter')
    if available:
        terminal.wait('TARGET-STAGING-CONTEXT')
        saved = manager.load_session(target.session_id)
        assert sum('prepared' in row['data'] for row in saved.extension_entries['stagedprovider']) == 1
        terminal.text('/ext-stagedprovider-status')
        terminal.keys('Enter')
        terminal.wait('"starts": 1')
        terminal.wait(target.session_id)
    else:
        terminal.wait('Saved extension model unavailable in target runtime')
        assert manager._session_path(target.session_id).read_bytes() == original_target
        assert manager.load_session(original).model == 'terminal-fixture'
        terminal.text('/session')  # Exact command must not resolve to the longer /sessions neighbor.
        terminal.keys('Enter')
        terminal.wait('Session ID: ' + original)
    assert not (tmp_path / 'fixture-model-count').exists()
    terminal.resize(60, 18)
    terminal.resize(80, 24)
    terminal.text('staged restoration parent recovered')
    terminal.wait_editor('staged restoration parent recovered')
    terminal.finish()


@pytest.mark.parametrize('available', [False, True])
def test_actual_claw_staged_saved_provider_restore_preserves_failed_owner_and_native_terminal(
    terminal, tmp_path, available,
):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    from xdog.claw.core.runtime.gateway import GatewayServer
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    data = tmp_path / 'data'
    store = TranscriptStore(data / 'groups/main/sessions', owner_group='main')
    original = store.create_session('main')
    store.save_extension_entries(original.session_id, {
        'stagedprovider': [{'id': 'old', 'kind': 'note', 'data': {'allow': True}}]})
    store.replace_transcript(original.session_id, [{'role': 'user', 'content': 'ORIGINAL-STAGED-GROUP'}])
    target = store.create_session('main')
    store.save_extension_entries(target.session_id, {
        'stagedprovider': [{'id': 'target', 'kind': 'note', 'data': {'allow': available}}]})
    store.replace_transcript(target.session_id, [{'role': 'user', 'content': 'TARGET-STAGED-GROUP'}])
    store.activate_session('main', original.session_id)
    config = ClawConfig(model='ext-stagedprovider-local/choice', data_dir=str(data),
        socket_path=str(tmp_path / 'stage.sock'), pid_file=str(tmp_path / 'stage.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('stagedprovider',), extension_dir=str(root))
    ready, errors, owned, calls = threading.Event(), [], {}, []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned['loop'] = loop
        server = GatewayServer(config)
        try:
            with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                       return_value=(config.model, base)):
                loop.run_until_complete(server.start())
            runtime = server._orchestrator._runtimes['main']
            owned['runtime'] = runtime
            owned['original'] = runtime._active_session
            owned['store'] = runtime.transcript_store
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
    original_target = owned['store']._path(target.session_id).read_bytes()
    try:
        terminal.start('claw', config.socket_path)
        terminal.resize(120, 30)
        terminal.text('/resume ' + target.session_id)
        terminal.keys('Enter')
        if available:
            terminal.wait('TARGET-STAGED-GROUP')
            assert owned['runtime']._active_session is not owned['original']
            assert owned['store'].get_active_session('main').session_id == target.session_id
            prepared = owned['store'].get_extension_entries(target.session_id)['stagedprovider']
            assert sum('prepared' in row['data'] for row in prepared) == 1
        else:
            terminal.wait('Target preparation/navigation failed')
            assert owned['runtime']._active_session is owned['original']
            assert owned['store'].get_active_session('main').session_id == original.session_id
            assert owned['store']._path(target.session_id).read_bytes() == original_target
        assert not calls
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('claw staged parent recovered')
        terminal.wait_editor('claw staged parent recovered')
        terminal.finish()
    finally:
        terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
        owned['loop'].call_soon_threadsafe(owned['loop'].stop)
        thread.join(6)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_exact_slash_command_and_explicit_neighbor_do_not_reinterpret_typed_action(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'exact-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['commandcollision'],
                                                      'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'exact.sock'), pid_file=str(tmp_path / 'exact.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandcollision',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-commandcollision-show')
        terminal.text('/ext-commandcollision-show')
        terminal.keys('Enter')
        terminal.wait('EXACT-SHOW-ONLY')
        assert 'EXPLICIT-SHOWS-ONLY' not in terminal.visible()
        terminal.text('/ext-commandcollision-show')
        terminal.keys('Down', 'Enter')  # Explicitly choose the similarly named neighbor.
        terminal.wait('EXPLICIT-SHOWS-ONLY')
        terminal.text('/ext-commandcollision-counts')
        terminal.keys('Enter')
        terminal.wait('"show": 1')
        terminal.wait('"shows": 1')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('exact command parent recovered')
        terminal.wait_editor('exact command parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_readonly_session_timeline_search_preserves_head_and_native_parent(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.agent.messages import messages_to_dicts
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
    from xdog.claw.core.runtime.gateway import GatewayServer
    from xdog.coding.core.session_manager import SessionManager

    history = [UserMessage(content='OLD-TIMELINE-USER ' + 'x' * 5000),
               AssistantMessage(content=(TextContent(text='OLD-TIMELINE-ANSWER'),)),
               UserMessage(content='retained user'), AssistantMessage(content=(TextContent(text='retained answer'),))]
    head = [UserMessage(content='CURRENT-TIMELINE-SUMMARY'), *history[2:]]
    fork = [*history, UserMessage(content='FORK-ONLY-TIMELINE-TAIL')]
    metadata = {'source': 'builtin', 'original_count': 4, 'first_kept_index': 2,
                'details': None, 'recovery_branch_id': 'before'}
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'timeline-home'
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        manager = SessionManager(home / 'sessions')
        data = manager.create_session(model='terminal-fixture', working_dir=tmp_path)
        data.messages, data.compaction = head, metadata
        data.branches = [
            {'branch_id': 'before', 'label': 'Before compaction', 'messages': messages_to_dicts(history)},
            {'branch_id': 'fork', 'label': 'Divergent fork', 'parent_branch': 'before',
             'messages': messages_to_dicts(fork)}]
        manager.save_session(data)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'timeline.sock'), pid_file=str(tmp_path / 'timeline.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    loop.run_until_complete(server.start())
                session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
                stored_head = messages_to_transcript(head)
                stored_head[0].update(is_compaction=True, compaction=metadata)
                session._store.replace_transcript(session.meta.session_id, stored_head)
                session._store.save_branch(session.meta.session_id, 'before', messages_to_transcript(history),
                                           metadata={'label': 'Before compaction'})
                session._store.save_branch(session.meta.session_id, 'fork', messages_to_transcript(fork),
                                           metadata={'label': 'Divergent fork', 'parent_branch': 'before'})
                owned['session'] = session
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
    try:
        terminal.start(kind, *(['--resume-id', data.session_id, '--model', 'terminal-fixture'] if kind == 'coding'
                               else [config.socket_path]))
        terminal.resize(120, 30)
        terminal.text('/transcript timeline')
        terminal.keys('Enter')
        terminal.wait('Session timeline')
        terminal.keys('/')
        terminal.text('FORK-ONLY-TIMELINE-TAIL')
        terminal.keys('Enter')
        terminal.wait('FORK-ONLY-TIMELINE-TAIL')
        if kind == 'coding':
            assert len(manager.load_session(data.session_id).messages) == 3
        else:
            session = owned['session']
            assert len(session._store.load_transcript(session.meta.session_id)) == 3
            assert not session._store.active_branch(session.meta.session_id)
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.keys('C-c')
        terminal.wait_hidden('Session timeline')
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.text('timeline parent recovered')
        terminal.wait_editor('timeline parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_session_naming_browser_and_native_parent_recovery_without_model_work(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    from xdog.claw.core.runtime.gateway import GatewayServer
    from xdog.coding.core.session_manager import SessionManager

    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'named-home'
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        manager = SessionManager(home / 'sessions')
        terminal.start('coding')
    else:
        data = tmp_path / 'name-data'
        store = TranscriptStore(data / 'groups/main/sessions', owner_group='main')
        original = store.create_session('main')
        store.replace_transcript(original.session_id, [{'role': 'user', 'content': 'NAME-ORIGINAL-HISTORY'}])
        config = ClawConfig(model='offline/model', data_dir=str(data),
            socket_path=str(tmp_path / 'name.sock'), pid_file=str(tmp_path / 'name.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        def base(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=(config.model, base)):
                    loop.run_until_complete(server.start())
                runtime = server._orchestrator._runtimes['main']
                owned['runtime'], owned['store'] = runtime, runtime.transcript_store
                owned['original'] = runtime.get_or_create_session(allow_reset=False)
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
        terminal.start('claw', config.socket_path)
    try:
        terminal.resize(120, 30)
        terminal.text('/name NAME-REVIEW-开发')
        terminal.keys('Enter')
        terminal.wait('Session name: NAME-REVIEW-开发')
        if kind == 'coding':
            session_id = manager.list_sessions()[0].session_id
            saved = manager.load_session(session_id)
            assert saved.name == 'NAME-REVIEW-开发' and not saved.messages
        else:
            assert owned['runtime']._active_session is owned['original']
            assert owned['store'].get_active_session('main').label == 'NAME-REVIEW-开发'
            assert owned['store'].load_transcript(original.session_id)[0]['content'] == 'NAME-ORIGINAL-HISTORY'
        terminal.text('/sessions')
        terminal.keys('Enter')
        terminal.wait('Sessions ·')
        terminal.text('/NAME-REVIEW')
        terminal.wait('/ NAME-REVIEW')
        terminal.wait('[session] NAME-REVIEW-开发')
        terminal.resize(60, 18)
        terminal.resize(80, 24)
        terminal.keys('Escape')
        terminal.wait_hidden('/ NAME-REVIEW')
        terminal.keys('Escape')
        terminal.wait_hidden('Sessions ·')
        terminal.text('name focus recovered')
        terminal.wait_editor('name focus recovered')
        terminal.keys('C-c')
        terminal.text('/name -')
        terminal.keys('Enter')
        terminal.wait('Session name: (unnamed)')
        if kind == 'coding':
            assert not manager.load_session(session_id).name
            assert not (tmp_path / 'fixture-model-count').exists()
        else:
            assert not owned['store'].get_active_session('main').label and not calls
        terminal.text('named session parent recovered')
        terminal.wait_editor('named session parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('fail', [False, True])
def test_actual_extension_session_name_review_deferred_save_failure_and_native_parent(terminal, tmp_path, kind, fail):
    import asyncio
    import re
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    from xdog.claw.core.runtime.gateway import GatewayServer
    from xdog.coding.core.session_manager import SessionManager

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/namer', root / 'namer')
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'name-extension-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['namer'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        manager = SessionManager(home / 'sessions')
    else:
        data = tmp_path / 'name-extension-data'
        seeded = TranscriptStore(data / 'groups/main/sessions', owner_group='main')
        seeded.create_session('main')
        config = ClawConfig(model='offline/model', data_dir=str(data),
            socket_path=str(tmp_path / 'named.sock'), pid_file=str(tmp_path / 'named.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('namer',), extension_dir=str(root))
        def base(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=(config.model, base)):
                    loop.run_until_complete(server.start())
                owned['store'] = server._orchestrator._runtimes['main'].transcript_store
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
    def named():
        return (manager.get_most_recent().name if kind == 'coding'
                else owned['store'].get_active_session('main').label)
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/name Existing-Name')
        terminal.keys('Enter')
        terminal.wait('Session name: Existing-Name')
        terminal.text('/ext-namer-rename ' + json.dumps({'name': 'Reviewed-Name', 'fail': fail}))
        terminal.keys('Enter')
        terminal.wait('Queue this exact name change')
        assert named() == 'Existing-Name'
        terminal.keys('Right', 'Enter')
        terminal.wait('form review')
        terminal.resize(20, 5)
        terminal.keys('Enter')
        assert named() == 'Existing-Name' and not calls
        terminal.resize(80, 24)
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pane = terminal.visible()
            if 'Enter submits reviewed form' in pane:
                break
            match = re.search(r'Review (\d+-\d+/\d+)', pane)
            if match and match[1] != previous:
                previous = match[1]
                terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Exact metadata name review incomplete:\n' + terminal.pane())
        terminal.keys('Enter')
        terminal.wait_hidden('form review')
        terminal.wait('Extension callback failed' if fail else 'Session name: Reviewed-Name')
        assert named() == ('Existing-Name' if fail else 'Reviewed-Name')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('name extension parent recovered')
        terminal.wait_editor('name extension parent recovered')
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


def test_actual_claw_native_name_read_refreshes_remote_metadata_and_cancels_without_cached_fallback(terminal, tmp_path):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    from xdog.claw.core.runtime.gateway import GatewayServer

    data = tmp_path / 'fresh-name-data'
    seeded = TranscriptStore(data / 'groups/main/sessions', owner_group='main')
    original = seeded.create_session('main')
    seeded.rename_session('main', original.session_id, 'ORIGINAL-CACHED-NAME')
    config = ClawConfig(model='offline/model', data_dir=str(data),
        socket_path=str(tmp_path / 'fresh.sock'), pid_file=str(tmp_path / 'fresh.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    ready, release, errors, owned, calls = threading.Event(), threading.Event(), [], {}, []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owned['loop'] = loop
        server = GatewayServer(config)
        try:
            with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
                loop.run_until_complete(server.start())
            owned['orchestrator'] = server._orchestrator
            owned['store'] = server._orchestrator._runtimes['main'].transcript_store
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
    try:
        terminal.start('claw', config.socket_path)
        # This update has no UI receipt/push to the launched process. Its prior
        # pong still contains ORIGINAL-CACHED-NAME until /name actually reads.
        asyncio.run_coroutine_threadsafe(owned['orchestrator'].rename_session(
            'main', original.session_id, 'REMOTE-COMMITTED-NAME'), owned['loop']).result(6)
        terminal.text('/name')
        terminal.keys('Enter')
        terminal.wait('Session name: REMOTE-COMMITTED-NAME')
        assert 'Session name: ORIGINAL-CACHED-NAME' not in terminal.pane()
        read = owned['store'].read_session_name
        owned['store'].read_session_name = lambda *args: read(*args) if release.is_set() else None
        terminal.text('/name')
        terminal.keys('Enter')
        terminal.wait('Session name read pending')
        terminal.resize(42, 10)
        terminal.resize(80, 24)
        terminal.keys('Escape')
        terminal.wait_hidden('Session name read pending')
        terminal.wait('Session name read canceled')
        release.set()
        terminal.text('fresh name reader parent recovered')
        terminal.wait_editor('fresh name reader parent recovered')
        assert owned['store'].get_active_session('main').label == 'REMOTE-COMMITTED-NAME' and not calls
        terminal.finish()
    finally:
        release.set()
        terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
        owned['loop'].call_soon_threadsafe(owned['loop'].stop)
        thread.join(6)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('cancel', [False, True])
def test_actual_failed_extension_tool_withdrawal_cancel_and_native_parent_recovery(terminal, tmp_path, kind, cancel):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    for name in ('faultytools', 'toolinspector'):
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / name, root / name)
    marker = tmp_path / 'fault-entered'
    flags = {'stage': 'command', 'hold': cancel, 'marker': str(marker)}
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'failure-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['faultytools', 'toolinspector'],
            'extension_dir': str(root), 'extension_settings': {'faultytools': flags}}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        data = tmp_path / 'failure-data'
        TranscriptStore(data / 'groups/main/sessions', owner_group='main').create_session('main')
        config = ClawConfig(model='offline/model', data_dir=str(data),
            socket_path=str(tmp_path / 'fault.sock'), pid_file=str(tmp_path / 'fault.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('faultytools', 'toolinspector'),
            extension_dir=str(root), extension_settings={'faultytools': flags})
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-faultytools-fail')
        terminal.text('/ext-faultytools-fail')
        terminal.keys('Enter')
        if cancel:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.02)
            assert marker.exists()
            terminal.keys('Escape')
            terminal.wait('effects uncertain' if kind == 'claw' else 'Cancelled /ext-faultytools-fail')
        else:
            terminal.wait('Extension callback failed')
        terminal.text('/tools faultytools')
        terminal.keys('Enter')
        terminal.wait('0 matches')
        terminal.keys('Escape')
        terminal.wait_hidden('Tools (inline)')
        terminal.wait_cursor(True)
        terminal.text('failed worker parent recovered')
        terminal.wait_editor('failed worker parent recovered')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_native_help_filters_live_menu_without_model_work_and_restores_parent(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandcollision',
                    root / 'commandcollision')
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'help-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps(
            {'extensions': ['commandcollision'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandcollision',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-commandcollision-show')
        terminal.text('/help commandcollision')
        terminal.keys('Enter')
        terminal.wait('Terminal command menu')
        terminal.wait('/ext-commandcollision-counts')
        terminal.text('/ext-commandcollision-counts')
        terminal.keys('Enter')
        terminal.wait('"show": 0')
        terminal.wait('"shows": 0')
        terminal.text('/help copy')
        terminal.keys('Enter')
        terminal.wait('/copy —')
        terminal.text('/help no-such-menu-command')
        terminal.keys('Enter')
        terminal.wait('No matching commands')
        terminal.resize(42, 10)
        terminal.resize(80, 24)
        terminal.text('help menu parent recovered')
        terminal.wait_editor('help menu parent recovered')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_extension_command_menu_pages_no_execution_and_native_parent_restore(terminal, tmp_path, kind):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandreader',
                    root / 'commandreader')
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'catalog-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps(
            {'extensions': ['commandreader'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandreader',), extension_dir=str(root))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-commandreader-inspect')
        terminal.text('/ext-commandreader-inspect')
        terminal.keys('Enter')
        terminal.wait('COMMAND-MENU')
        terminal.wait('"executed": 0')
        terminal.wait('/copy')
        terminal.text('/ext-commandreader-inspect copy')
        terminal.keys('Enter')
        terminal.wait('"count": 1')
        terminal.wait('"names": ["/copy"]')
        terminal.resize(42, 10)
        terminal.resize(80, 24)
        terminal.text('command menu API parent recovered')
        terminal.wait_editor('command menu API parent recovered')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            terminal.call('respawn-pane', '-k', '-t', 'test', 'bash --noprofile --norc')
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


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
    if kind == 'coding':
        home = tmp_path / 'arguments-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps(
            {'extensions': ['argcomplete'], 'extension_dir': str(root)}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        argument_state = tmp_path / 'argument-state.json'
        terminal.child_env['XDOG_FIXTURE_ARGUMENT_STATE'] = str(argument_state)
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
        if kind != 'claw':
            return
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
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy b')
        terminal.wait('beta')
        terminal.keys('Enter')
        terminal.wait_editor('/ext-argcomplete-deploy beta')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy label')
        terminal.wait('🚀 发布候选 [release-1]')
        terminal.wait('🚀 发布候选 [release-2]')
        terminal.keys('Down', 'Enter')
        terminal.wait_editor('/ext-argcomplete-deploy release-2')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy production en')
        terminal.wait('Endpoint for production')
        terminal.keys('Tab')
        terminal.wait_editor('/ext-argcomplete-deploy production endpoint-production')
        terminal.keys('C-c')
        terminal.text('/ext-argcomplete-deploy staging en')
        terminal.wait('Endpoint for staging')
        terminal.keys('Enter')
        terminal.wait_editor('/ext-argcomplete-deploy staging endpoint-staging')
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


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('stop', ['escape', 'exit'])
def test_actual_argument_cancel_settles_worker_and_recovers_native_parent(terminal, tmp_path, kind, stop):
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete',
                    root / 'argcomplete')
    marker, canceled = tmp_path / 'entered', tmp_path / 'canceled'
    flags = {'marker': str(marker)}
    ready, errors, owned, calls = threading.Event(), [], {}, []
    thread = None
    if kind == 'coding':
        home = tmp_path / 'arguments-home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps(
            {'extensions': ['argcomplete'], 'extension_dir': str(root),
             'extension_settings': {'argcomplete': flags}}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
        terminal.child_env['XDOG_FIXTURE_ARGUMENT_CANCEL_MARKER'] = str(canceled)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('argcomplete',), extension_dir=str(root),
            extension_settings={'argcomplete': flags})
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=(config.model, stream)):
                    loop.run_until_complete(server.start())
                runtime = server._orchestrator._runtimes['main']
                session = runtime.get_or_create_session(allow_reset=False)
                original = session.extensions.complete_command
                async def observed(*args, **kwargs):
                    try:
                        return await original(*args, **{**kwargs, 'timeout': 5})
                    except asyncio.CancelledError:
                        canceled.write_text('explicit-cancel-settled')
                        raise
                session.extensions.complete_command = observed
                owned['runtime'] = runtime
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.resize(120, 30)
        terminal.text('/extensions')
        terminal.keys('Enter')
        terminal.wait('ext-argcomplete-deploy')
        terminal.text('/ext-argcomplete-deploy hold')
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        if stop == 'escape':
            terminal.keys('Escape')
        else:
            terminal.keys('C-c')
            terminal.text('/quit')
            terminal.keys('Enter')
        deadline = time.monotonic() + 4
        while not canceled.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert canceled.read_text() == 'explicit-cancel-settled'
        if stop == 'escape':
            terminal.wait_editor('/ext-argcomplete-deploy hold')
            terminal.keys('C-c')
            terminal.resize(42, 10)
            terminal.resize(80, 24)
            terminal.text('argument cancel parent recovered')
            terminal.wait_editor('argument cancel parent recovered')
            terminal.finish()
        else:
            terminal.wait('TERMIOS-RESTORED=True')
            assert (tmp_path / 'termios-result.txt').read_text() == 'True'
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        if kind == 'claw':
            deadline = time.monotonic() + 4
            while owned['runtime']._active_session.extensions.command_completions() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not owned['runtime']._active_session.extensions.command_completions()

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


@pytest.mark.parametrize('stopped', [False, True])
def test_actual_coding_reviewed_child_queue_return_restores_only_child_form_not_parent_submission(terminal, stopped):
    import re

    terminal.start('coding')
    terminal.text('/agents start --fresh hold')
    terminal.keys('Enter')
    terminal.wait('started (read-only)')
    terminal.text('/agents list')
    terminal.keys('Enter')
    terminal.wait('Child agents')
    terminal.wait('[running]')
    terminal.text('s')
    terminal.wait('Child steer')
    terminal.text('QUEUE-CHILD-DRAFT /quit literal')
    terminal.keys('Enter')
    terminal.wait('Confirm child action')
    terminal.keys('Enter')
    terminal.wait_hidden('Confirm child action')
    if stopped:
        terminal.text('c')
        terminal.wait('Confirm child action')
        terminal.keys('Enter')
        terminal.wait('[canceled]')
    terminal.text('q')
    terminal.wait('Child input queue')
    terminal.wait('QUEUE-CHILD-DRAFT')
    terminal.text('u')
    terminal.wait('NOT tool approval')
    previous = ''
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        pane = terminal.visible()
        match = re.search(r'Review (\d+-\d+/\d+)', pane)
        if match:
            lower, rest = match[1].split('-', 1)
            upper, total = rest.split('/')
            if int(upper) == int(total):
                break
            if match[1] != previous:
                previous = match[1]
                terminal.keys('NPage')
        time.sleep(0.03)
    else:
        raise AssertionError('Full child queue review did not settle: ' + terminal.pane())
    terminal.keys('Enter')
    terminal.wait('Child start' if stopped else 'Child steer')
    terminal.wait_editor('QUEUE-CHILD-DRAFT /quit literal')
    terminal.keys('Escape')
    terminal.wait('Child agents')
    if not stopped:
        terminal.text('c')
        terminal.wait('Confirm child action')
        terminal.keys('Enter')
        terminal.wait('[canceled]')
    terminal.keys('Escape')
    terminal.wait_cursor(True)
    terminal.text('PARENT AFTER QUEUE')
    terminal.wait_editor('PARENT AFTER QUEUE')
    terminal.finish()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_child_image_editor_file_preview_review_cancel_and_terminal_recovery(terminal, tmp_path, kind):
    import asyncio
    import base64
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    image = tmp_path / 'child-image.png'
    image.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='))
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    thread = None
    if kind == 'claw':
        config = ClawConfig(model='offline/test', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'images.sock'), pid_file=str(tmp_path / 'images.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/test', stream)):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/agents list')
        terminal.keys('Enter')
        terminal.wait('Child agents')
        terminal.text('n')
        terminal.wait('Child start')
        terminal.text('REVIEW-ONLY-CHILD-TASK')
        terminal.keys('C-o')
        terminal.wait('literal path')
        terminal.text(str(image))
        terminal.keys('Enter')
        terminal.wait('Images: 1')
        terminal.wait_editor('REVIEW-ONLY-CHILD-TASK')
        terminal.keys('C-b')
        terminal.wait('Child draft images')
        terminal.wait('child-image.png')
        terminal.keys('Enter')
        terminal.wait('Attachment')
        terminal.keys('Escape')
        terminal.wait('Child start')
        terminal.keys('Enter')
        terminal.wait('Confirm child start')
        terminal.review_child_start()
        terminal.keys('Escape')  # Full physical review is preparation, not admission.
        terminal.wait_editor('REVIEW-ONLY-CHILD-TASK')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.text('n')
        terminal.wait_editor('REVIEW-ONLY-CHILD-TASK')
        terminal.wait('Images: 1')
        terminal.keys('C-b')
        terminal.wait('Child draft images')
        terminal.text('x')
        terminal.keys('Escape')
        terminal.wait('Child start')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('parent after child image')
        terminal.wait_editor('parent after child image')
        assert not calls
        terminal.finish()
    finally:
        if thread is not None:
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('initial_task', [False, True, 'returned'])
def test_actual_stopped_child_image_copy_review_fresh_draft_and_native_terminal_restore(
    terminal, tmp_path, kind, initial_task,
):
    import asyncio
    from unittest.mock import patch

    from xdog.agent.supervision_store import ChildStore
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer
    from xdog.coding.core.session_manager import SessionManager

    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
    record = {'id': '0123456789ab', 'task': 'SAVED-CHILD-IMAGE', 'access': 'read-only', 'status': 'canceled',
              'messages': [], 'settling': False, 'undelivered_inputs': [{'id': 'retained-picture',
                'content': 'RECOVER-IMAGE-LITERAL /quit', 'delivery': 'follow_up',
                'images': [{'data': png, 'mime_type': 'image/png'}]}]}
    if initial_task == 'returned':
        record['returned_backups'] = record.pop('undelivered_inputs')
    elif initial_task:
        record['status'] = 'interrupted'
        record['task'] = 'RECOVER-IMAGE-LITERAL /quit'
        record['task_images'] = record.pop('undelivered_inputs')[0]['images']
        record['initial_state'] = 'uncertain'
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    thread = None
    if kind == 'coding':
        home = tmp_path / 'image-recovery-home'
        home.mkdir()
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
        manager = SessionManager(home / 'sessions')
        data = manager.create_session(model='terminal-fixture', working_dir=tmp_path)
        manager.save_session(data)
        store = ChildStore.scoped(manager._dir, data.session_id, 'head')
        store.save(record)
    else:
        local_home = tmp_path / 'saved-draft-local-home'
        local_home.mkdir(mode=0o700)
        terminal.child_env['CLAW_DIR'] = str(local_home)
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'recovery.sock'), pid_file=str(tmp_path / 'recovery.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
        def stream(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    loop.run_until_complete(server.start())
                runtime = server._orchestrator._runtimes['main']
                meta = runtime.transcript_store.get_active_session('main')
                if meta is None:
                    meta = runtime.transcript_store.create_session('main')
                scope = runtime.transcript_store.active_branch(meta.session_id) or 'head'
                owned['store'] = ChildStore.scoped(runtime.transcript_store._sessions_dir, meta.session_id, scope)
                owned['store'].save(record)
                runtime._drop_session()
                runtime.get_or_create_session(allow_reset=False)
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
        store = owned['store']
    before = store.load()
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    parent_root = manager._dir if kind == 'coding' else local_home
    parent_saved = ChildDraftStore(parent_root / 'saved-parent-drafts').save(encode_draft(
        ChildDraft('UNSENT-PARENT-RECOVERY /quit', images=(attachment(base64.b64decode(png), 'parent.png'),)),
        label='original parent composer', scope='parent-old-session', action='parent', slot=''))
    try:
        terminal.start(kind, *(['--resume-id', data.session_id, '--model', 'terminal-fixture'] if kind == 'coding'
                               else [config.socket_path]), expected='ready | ctx:' if kind == 'coding' else 'connected')
        terminal.text('/agents list')
        terminal.keys('Enter')
        terminal.wait('[interrupted]' if initial_task is True else 'SAVED-CHILD-IMAGE')
        terminal.text('q')
        terminal.wait('saved recovery')
        terminal.wait('RECOVER-IMAGE-LITERAL')
        terminal.text('u')
        terminal.wait('NOT tool approval')
        previous = ''
        deadline = time.monotonic() + 10
        import re
        while time.monotonic() < deadline:
            pane = terminal.visible()
            match = re.search(r'Review (\d+-\d+/\d+)', pane)
            if match:
                lower, rest = match[1].split('-', 1)
                upper, total = rest.split('/')
                if int(upper) == int(total):
                    break
                if match[1] != previous:
                    previous = match[1]
                    terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Image copy review did not finish: ' + terminal.visible())
        terminal.keys('Enter')
        terminal.wait('Child start')
        terminal.wait('Access: read-only')
        terminal.wait('Images: 1')
        terminal.wait_editor('RECOVER-IMAGE-LITERAL /quit')
        terminal.keys('C-b')
        terminal.wait('Child draft images')
        terminal.wait('recovered-child-image-1')
        terminal.keys('Enter')
        terminal.wait('Attachment')
        terminal.keys('Escape')
        terminal.wait('Child start')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.text('d')
        terminal.wait('Local child drafts')
        terminal.keys('Enter')
        terminal.wait('Review local child draft copy')
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pane = terminal.visible()
            match = re.search(r'(\d+)-(\d+)/(\d+) · NOT execution', pane)
            if match:
                if int(match[2]) == int(match[3]):
                    break
                if match[0] != previous:
                    previous = match[0]
                    terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Local image draft review did not finish: ' + terminal.visible())
        terminal.keys('Enter')
        terminal.wait('Child start')
        terminal.wait('Access: read-only')
        terminal.wait('Images: 1')
        terminal.wait_editor('RECOVER-IMAGE-LITERAL /quit')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.text('d')
        terminal.wait('Local child drafts')
        terminal.text('s')
        terminal.wait('Review local child draft save')
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            match = re.search(r'(\d+)-(\d+)/(\d+) · NOT execution', terminal.visible())
            if match:
                if int(match[2]) == int(match[3]):
                    break
                if match[0] != previous:
                    previous = match[0]
                    terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Durable save review never finished: ' + terminal.visible())
        terminal.keys('Enter')
        terminal.wait('Child draft saved privately:')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('/agents autosave on')
        terminal.keys('Enter')
        terminal.wait('Child autosave ON')
        terminal.text('/agents autosave off')
        terminal.keys('Enter')
        terminal.wait('Child autosave OFF')
        terminal.text('/agents saved list')
        terminal.keys('Enter')
        terminal.wait('metadata only; no drafts adopted')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('/agents saved')
        terminal.keys('Enter')
        terminal.wait('SAVED PRIVATE')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('/draft saved ' + parent_saved.id)
        terminal.keys('Enter')
        terminal.wait('Saved parent drafts')
        terminal.keys('Enter')
        terminal.wait('Review saved parent draft copy')
        terminal.text('p')
        terminal.wait('Draft image 1/1')
        terminal.wait('Attachment')
        terminal.keys('Escape')
        terminal.wait('Review saved parent draft copy')
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            match = re.search(r'(\d+)-(\d+)/(\d+) · NOT execution', terminal.visible())
            if match:
                if int(match[2]) == int(match[3]):
                    break
                if match[0] != previous:
                    previous = match[0]
                    terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Parent snapshot review never finished: ' + terminal.visible())
        terminal.keys('Enter')
        terminal.wait_editor('UNSENT-PARENT-RECOVERY /quit')
        terminal.wait('Images: 1')
        terminal.wait_cursor(True)
        terminal.keys('M-C-s')
        terminal.wait('Review saved parent draft save')
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            match = re.search(r'(\d+)-(\d+)/(\d+) · NOT execution', terminal.visible())
            if match:
                if int(match[2]) == int(match[3]):
                    break
                if match[0] != previous:
                    previous = match[0]
                    terminal.keys('NPage')
            time.sleep(0.03)
        else:
            raise AssertionError('Explicit parent save review never finished: ' + terminal.visible())
        terminal.keys('Enter')
        terminal.wait('Parent draft saved privately:')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.wait_editor('UNSENT-PARENT-RECOVERY /quit')
        terminal.keys('C-c')
        terminal.text('parent after recovered picture')
        terminal.wait_editor('parent after recovered picture')
        assert store.load() == before and not calls
        assert not (terminal.directory / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        if thread is not None:
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_live_image_return_two_reviews_native_cursor_and_retained_draft(terminal, tmp_path, kind):
    import base64
    import re
    image = tmp_path / 'live-return.png'
    image.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='))
    def review_queue():
        previous = ''
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pane = terminal.visible()
            match = re.search(r'Review (\d+)-(\d+)/(\d+)', pane)
            if match:
                if int(match[2]) == int(match[3]):
                    return
                if match[0] != previous:
                    previous = match[0]
                    terminal.keys('NPage')
            time.sleep(0.03)
        raise AssertionError('Queue review did not finish: ' + terminal.pane())
    import asyncio
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage, Model, StartEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer
    calls, errors, owned = [], [], {}
    ready = threading.Event()
    thread = None
    if kind == 'claw':
        config = ClawConfig(model='offline/vision', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'live-return.sock'), pid_file=str(tmp_path / 'live-return.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
        class Adapter:
            def resolve_model(self, name):
                return Model(id=name, context_window=128000, input=('text', 'image'))
            def __call__(self, model, context, options):
                calls.append(context)
                async def events():
                    yield StartEvent(partial=AssistantMessage())
                    await asyncio.sleep(60)
                return EventStream.from_async_generator(events())
        def serve():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            owned['loop'] = loop
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/vision', Adapter())):
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
    try:
        terminal.start(kind, *([config.socket_path] if kind == 'claw' else []))
        terminal.text('/agents start --fresh hold')
        terminal.keys('Enter')
        terminal.wait('started (read-only)')
        terminal.text('/agents list')
        terminal.keys('Enter')
        terminal.wait('[running]')
        terminal.text('s')
        terminal.wait('Child steer')
        terminal.text('RETURN-CHILD-IMAGE /quit literal')
        terminal.keys('C-o')
        terminal.wait('literal path')
        terminal.text(str(image))
        terminal.keys('Enter')
        terminal.wait('Images: 1')
        terminal.keys('Enter')
        terminal.wait('Confirm child steer')
        terminal.review_child_start()
        terminal.keys('Enter')
        terminal.wait('Child agents')
        terminal.text('q')
        terminal.wait('Child input queue')
        terminal.wait('RETURN-CHILD-IMAGE')
        terminal.text('u')
        terminal.wait('NOT tool approval')
        review_queue()
        terminal.keys('Enter')
        terminal.wait('SECOND removal review')
        review_queue()
        terminal.keys('Enter')
        terminal.wait('Child steer')
        terminal.wait('Images: 1')
        terminal.wait_editor('RETURN-CHILD-IMAGE /quit literal')
        terminal.keys('Escape')
        terminal.wait('Child agents')
        terminal.text('c')
        terminal.wait('Confirm child action')
        terminal.keys('Enter')
        terminal.wait('[canceled]')
        terminal.keys('Escape')
        terminal.wait_cursor(True)
        terminal.text('parent after live picture return')
        terminal.wait_editor('parent after live picture return')
        terminal.finish()
    finally:
        if thread is not None:
            owned['loop'].call_soon_threadsafe(owned['loop'].stop)
            thread.join(6)
            assert not thread.is_alive() and not errors
