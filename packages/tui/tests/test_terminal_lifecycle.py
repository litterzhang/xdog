"""Terminal ownership on exceptional exits, EOF, and missing replies."""
from __future__ import annotations

import io
import os
import signal
from types import SimpleNamespace

import pytest
from xdog.tui.stdin_buffer import StdinBuffer
from xdog.tui.terminal_protocol import TerminalProtocol
from xdog.tui.tui import TUI


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal ownership")
@pytest.mark.parametrize("failure", ["raw", "anchor", "loop", "eof"])
def test_raw_terminal_restored_after_failure_or_eof(monkeypatch, failure) -> None:
    import pty
    import termios

    import xdog.tui.tui as module
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    reader = StdinBuffer()
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(fileno=lambda: slave))
    monkeypatch.setattr(module.sys, "stdout", output)
    monkeypatch.setattr(module, "StdinBuffer", lambda: reader)
    tui = TUI()
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")
    if failure == "raw":
        enter = reader.enter_raw
        def fail_after_raw():
            enter()
            fail()
        monkeypatch.setattr(reader, "enter_raw", fail_after_raw)
    else:
        monkeypatch.setattr(tui, "_anchor_terminal", fail if failure == "anchor" else lambda *a, **k: None)
        if failure == "loop":
            monkeypatch.setattr(tui, "_input_loop", fail)
        else:
            def eof(timeout=0):
                reader.close()
                return b""
            monkeypatch.setattr(reader, "read", eof)
    try:
        if failure == "eof":
            tui.start()
        else:
            with pytest.raises(RuntimeError, match="injected"):
                tui.start()
        assert termios.tcgetattr(slave) == original
        assert not tui.is_running
        if failure != "raw":
            assert "\x1b[?2004l" in output.getvalue()
            assert "\x1b[?25h" in output.getvalue()
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(not hasattr(signal, "SIGTSTP"), reason="POSIX job control")
@pytest.mark.parametrize("raises", [False, True])
def test_suspend_restores_previous_signal_handler(monkeypatch, raises) -> None:
    previous = signal.getsignal(signal.SIGTSTP)
    def custom_handler(signum, frame):
        pass
    def kill(group, sig):
        assert sig == signal.SIGTSTP
        assert signal.getsignal(sig) == signal.SIG_DFL
        if raises:
            raise OSError("injected failure")
    monkeypatch.setattr(os, "killpg", kill)
    signal.signal(signal.SIGTSTP, custom_handler)
    try:
        if raises:
            with pytest.raises(OSError):
                TUI()._suspend_process()
        else:
            TUI()._suspend_process()
        assert signal.getsignal(signal.SIGTSTP) is custom_handler
    finally:
        signal.signal(signal.SIGTSTP, previous)


def test_missing_cursor_report_has_bounded_wait_and_preserves_typing(monkeypatch) -> None:
    import xdog.tui.tui as module
    ticks = iter(i / 100 for i in range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module, "_terminal_height", lambda: 10)
    reader = StdinBuffer()
    reads = []
    def read(timeout=0):
        reads.append(timeout)
        return b"draft" if len(reads) == 1 else b""
    monkeypatch.setattr(reader, "read", read)
    tui = TUI()
    frames = []
    monkeypatch.setattr(tui, "_handle_frame", frames.append)
    protocol = TerminalProtocol()
    protocol.startup()
    tui._anchor_terminal(reader, protocol, resumed=False)
    assert 1 <= len(reads) <= 15
    assert b"".join(frame.data for frame in frames) == b"draft"
    assert tui._painter.state.origin == 9
