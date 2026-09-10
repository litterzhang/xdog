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


class Terminal:
    def __init__(self, directory: Path):
        self.directory = directory
        self.server = "xdog-test-" + uuid.uuid4().hex
        self.raw = directory / "terminal.raw"
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

    def wait_editor(self, text):
        from wcwidth import wcswidth
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.visible().splitlines()
            x, y = map(int, self.call("display-message", "-p", "-t", "test",
                                     "#{cursor_x} #{cursor_y}").split())
            if y < len(rows) and rows[y].endswith(text) and x == wcswidth(rows[y]):
                with (self.directory / "cursor-observations.txt").open("a") as output:
                    output.write(f"{x},{y}: {rows[y]}\n")
                return
            time.sleep(0.03)
        raise AssertionError(f"cursor did not reach end of {text!r}: {x},{y}\n{self.visible()}")

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

    def start(self, kind, *args):
        fixture = Path(__file__).parent / "fixtures" / "terminal_client.py"
        command = shlex.join([sys.executable, str(fixture), kind, *args])
        self.text("printf 'SHELL-BEFORE\\n'; " + command)
        self.keys("Enter")
        self.wait("terminal-fixture" if kind == "coding" else "connected")

    def finish(self):
        self.keys("C-c")  # clear a draft, if any
        self.text("/quit")
        self.keys("Enter")
        self.wait("TERMIOS-RESTORED=True")
        assert (self.directory / "termios-result.txt").read_text() == "True"
        raw = self.raw.read_bytes()
        assert b"\x1b[2J" not in raw
        assert b"\x1b[3J" not in raw
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
        raise AssertionError(f"{marker!r} stayed visible")


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
    assert "FIXTURE-REASONING-DETAIL" not in terminal.visible()
    terminal.keys("C-o")
    terminal.wait("FIXTURE-REASONING-DETAIL")
    terminal.keys("C-o")
    terminal.wait_hidden("FIXTURE-REASONING-DETAIL")
    terminal.finish()


def test_actual_coding_cli_permission_paste_resize_and_exit(terminal):
    terminal.start("coding")
    terminal.text("permission")
    terminal.keys("Enter")
    terminal.wait("Tool permission required")
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
    pane = terminal.visible()
    assert "queued-message" in pane and "new-draft" in pane
    terminal.finish()


def test_actual_claw_tui_with_isolated_socket(terminal, tmp_path):
    # Controlled gateway responses exercise the production Unix socket client.
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
                            "history_format": 2,
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
        terminal.wait("Thinking")
        assert "CLAW-REASONING-DETAIL" not in terminal.visible()
        terminal.keys("C-o")
        terminal.wait("CLAW-REASONING-DETAIL")
        terminal.keys("C-o")
        terminal.wait_hidden("CLAW-REASONING-DETAIL")
        terminal.text("request")
        terminal.keys("Enter")
        terminal.wait("bash")
        terminal.keys("C-o")
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
        terminal.finish()
    finally:
        server.close()
    thread.join(timeout=2)
    assert not errors
