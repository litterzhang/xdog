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
    assert "FIXTURE-REASONING-DETAIL" in terminal.visible()
    terminal.text("/details")
    terminal.keys("Enter")
    terminal.wait("FIXTURE-REASONING-DETAIL")
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
        assert "CLAW-REASONING-DETAIL" in terminal.visible()
        terminal.keys("C-o")
        terminal.wait("CLAW-REASONING-DETAIL")
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
