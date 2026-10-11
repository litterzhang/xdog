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


def test_leave_sequence_recovers_partial_paint_modes() -> None:
    sequence = TUI()._terminal_leave_sequence()
    assert "\x1b[?2026l" in sequence
    assert "\x1b[0m" in sequence
    assert "\x1b[?25h" in sequence
    assert "\x1b[?7h" in sequence


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal ownership")
@pytest.mark.parametrize("raises", [False, True])
def test_external_handoff_restores_modes_before_callback_and_reenters(monkeypatch, raises) -> None:
    import pty
    import termios

    import xdog.tui.tui as module
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(fileno=lambda: slave))
    monkeypatch.setattr(module.sys, "stdout", output)
    tui = TUI()
    entries = []
    loops = []
    callbacks = []

    def anchor(stdin, protocol, **kwargs):
        assert termios.tcgetattr(slave) != original
        entries.append(kwargs)

    def outside():
        assert termios.tcgetattr(slave) == original
        assert not tui.is_running
        assert "\x1b[?2004l" in output.getvalue()
        callbacks.append(True)
        import tty
        tty.setraw(slave)  # A broken external editor must not redefine shell defaults.
        if raises:
            raise ValueError("external failed")

    def loop(stdin, protocol):
        loops.append(True)
        if len(loops) == 1:
            assert tui.run_outside_terminal(outside)
            assert not tui.run_outside_terminal(outside)
        else:
            tui.stop()

    monkeypatch.setattr(tui, "_anchor_terminal", anchor)
    monkeypatch.setattr(tui, "_input_loop", loop)
    try:
        if raises:
            with pytest.raises(ValueError, match="external failed"):
                tui.start()
        else:
            tui.start()
            assert len(entries) == 2
            assert entries[-1] == {"resumed": True, "external": True}
        assert callbacks == [True]
        assert termios.tcgetattr(slave) == original
        assert not tui.is_running
    finally:
        os.close(master)
        os.close(slave)


def test_external_handoff_discards_coalesced_submission_keys() -> None:
    from xdog.tui.stdin_buffer import KeyBytes

    tui = TUI()
    tui._running = True
    seen = []

    def listener(event):
        seen.append(event.key)
        if event.key == "g":
            assert tui.run_outside_terminal(lambda: None)
        return {"consume": True}

    tui.add_input_listener(listener)
    tui._handle_frame(KeyBytes(b"\x07DRAFT\r"))
    tui._handle_frame(KeyBytes(b"LATER-FRAME\r"))
    assert seen == ["g"]


def test_stopped_tui_does_not_run_pending_external_callback(monkeypatch) -> None:
    import xdog.tui.tui as module
    reader = StdinBuffer()
    monkeypatch.setattr(reader, "enter_raw", lambda: None)
    monkeypatch.setattr(reader, "restore", lambda: None)
    monkeypatch.setattr(module, "StdinBuffer", lambda: reader)
    monkeypatch.setattr(module.sys, "stdout", io.StringIO())
    tui = TUI()
    calls = []
    monkeypatch.setattr(tui, "_anchor_terminal", lambda *a, **k: None)

    def loop(*args):
        assert tui.run_outside_terminal(lambda: calls.append(True))
        tui.stop()

    monkeypatch.setattr(tui, "_input_loop", loop)
    tui.start()
    assert calls == []
    assert not tui.run_outside_terminal(lambda: None)


def test_temporary_fullscreen_preserves_main_painter_and_requests_restore_anchor(monkeypatch):
    import xdog.tui.tui as module

    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", output)
    tui = TUI()
    tui._running = True
    main = tui._painter
    main.render(["retained history", "draft"], 80, 24, transient_start=1)
    anchors = []
    monkeypatch.setattr(tui, "_anchor_terminal", lambda *args, **kwargs: anchors.append(kwargs))
    stdin, protocol = StdinBuffer(), TerminalProtocol()
    assert tui.request_fullscreen(True)
    assert not tui.fullscreen  # Terminal mode changes only at loop boundaries.
    tui._apply_screen_change(stdin, protocol)
    assert tui.fullscreen and tui._painter is not main
    tui._painter.render(["viewer"], 40, 8, transient_start=0)
    assert tui.request_fullscreen(False)
    tui._apply_screen_change(stdin, protocol)
    assert not tui.fullscreen and tui._painter is main
    assert main.state.lines == ("retained history", "draft")
    assert anchors == [{"resumed": True}]
    assert output.getvalue().count("\x1b[?1049h") == output.getvalue().count("\x1b[?1049l") == 1
    assert "\x1b[3J" not in output.getvalue()
    stdin.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal ownership")
@pytest.mark.parametrize("failure", [False, True])
def test_temporary_fullscreen_exception_and_stop_restore_terminal_and_mode(monkeypatch, failure):
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
    monkeypatch.setattr(tui, "_anchor_terminal", lambda *args, **kwargs: None)

    def loop(stdin, protocol):
        assert tui.request_fullscreen(True)
        tui._apply_screen_change(stdin, protocol)
        if failure:
            raise RuntimeError("fullscreen failure")
        tui.stop()

    monkeypatch.setattr(tui, "_input_loop", loop)
    try:
        if failure:
            with pytest.raises(RuntimeError, match="fullscreen failure"):
                tui.start()
        else:
            tui.start()
        assert termios.tcgetattr(slave) == original and not tui.fullscreen
        assert output.getvalue().count("\x1b[?1049h") == output.getvalue().count("\x1b[?1049l") == 1
        assert "\x1b[?25h" in output.getvalue() and "\x1b[?2004l" in output.getvalue()
    finally:
        os.close(master)
        os.close(slave)


def test_resize_between_geometry_check_and_tick_paint_cannot_skip_cursor_reanchor(monkeypatch):
    import xdog.tui.tui as module
    from xdog.tui.components.text import Text
    sizes = [80, 24]
    output = io.StringIO()
    monkeypatch.setattr(module.sys, 'stdout', output)
    monkeypatch.setattr(module, '_terminal_width', lambda: sizes[0])
    monkeypatch.setattr(module, '_terminal_height', lambda: sizes[1])
    tui = TUI()
    tui.add_child(Text('Owned viewport'))
    tui._do_render()
    output.seek(0)
    output.truncate()
    anchors = []
    def anchor(*args, **kwargs):
        anchors.append(tuple(sizes))
        tui._painter.reanchor(0)
    monkeypatch.setattr(tui, '_anchor_terminal', anchor)
    class Reader:
        eof = False
        def read(self, timeout=0):
            return b''
        def flush_expired(self):
            return []
    ticks = []
    def tick():
        ticks.append(tuple(sizes))
        if len(ticks) == 1:
            sizes[:] = [36, 6]  # Exactly after loop size check, before _do_render reads its dimensions.
            tui.request_render()
        elif len(ticks) == 2:
            assert anchors == [(36, 6)] and (tui._previous_width, tui._previous_height) == (80, 24)
            assert '\x1b[6n' in output.getvalue()
        else:
            assert (tui._previous_width, tui._previous_height) == (36, 6)
            tui.stop()
    tui._tick_callbacks.append(tick)
    tui._running = True
    tui._input_loop(Reader(), TerminalProtocol())
    assert len(ticks) == 3 and anchors == [(36, 6)]
    assert '\x1b[3J' not in output.getvalue() and '\x1b[2J' not in output.getvalue()
