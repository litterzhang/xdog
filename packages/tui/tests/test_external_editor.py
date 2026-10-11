from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.tui.external_editor import edit_draft


def command(script: str) -> str:
    return shlex.join([sys.executable, "-c", script])


def test_round_trip_uses_private_file_and_removes_it(tmp_path: Path) -> None:
    record = tmp_path / "record.json"
    script = (
        "import pathlib,sys,os,json; p=pathlib.Path(sys.argv[1]); "
        f"pathlib.Path({str(record)!r}).write_text(json.dumps([str(p),os.stat(p).st_mode & 0o777, "
        "p.read_text(),os.getcwd()])); p.write_text('updated\\n多行')"
    )
    result = edit_draft("original\nprivate draft", command=command(script), cwd=tmp_path)
    assert result.text == "updated\n多行"
    assert "not sent" in result.message
    path, mode, original, cwd = json.loads(record.read_text())
    assert mode == 0o600
    assert original == "original\nprivate draft"
    assert cwd == str(tmp_path)
    assert not Path(path).exists()
    assert not Path(path).parent.exists()
    assert original not in result.message


@pytest.mark.parametrize("script", [
    "import sys; sys.exit(3)",
    "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink()",
    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'\\xff')",
    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x' * (1024*1024+1))",
])
def test_editor_failure_keeps_original(script: str) -> None:
    result = edit_draft("preserve me", command=command(script))
    assert result.text is None
    assert "draft preserved" in result.message


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink/FIFO safeguards")
@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_nonregular_replacement_is_rejected(tmp_path: Path, replacement: str) -> None:
    source = tmp_path / "secret"
    source.write_text("private data")
    action = f"p.symlink_to({str(source)!r})" if replacement == "symlink" else "os.mkfifo(p)"
    result = edit_draft("draft", command=command(
        "import pathlib,sys,os; p=pathlib.Path(sys.argv[1]); p.unlink(); " + action,
    ))
    assert result.text is None
    assert "draft preserved" in result.message
    assert "private data" not in result.message


def test_visual_precedence_and_no_shell_interpretation(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "never-created"
    monkeypatch.setenv("VISUAL", command("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('visual')"))
    monkeypatch.setenv("EDITOR", "/nonexistent-program")
    assert edit_draft("draft").text == "visual"
    monkeypatch.delenv("VISUAL")
    monkeypatch.setenv("EDITOR", command("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('editor')"))
    assert edit_draft("draft").text == "editor"
    argv = command("import pathlib,sys; pathlib.Path(sys.argv[-1]).write_text('literal')")
    assert edit_draft("draft", command=argv + f" ; touch {shlex.quote(str(marker))}").text == "literal"
    assert not marker.exists()


@pytest.mark.parametrize("value", ["", "'unterminated", "/nonexistent-xdog-editor"])
def test_invalid_editor_command(value: str) -> None:
    result = edit_draft("draft", command=value)
    assert result.text is None
    assert "draft preserved" in result.message


def test_canceled_editor_process_is_terminated(tmp_path: Path, monkeypatch) -> None:
    processes: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("xdog.tui.external_editor.subprocess.Popen", popen)
    calls = 0

    def stopped() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    result = edit_draft("draft", command=command("import time; time.sleep(60)"), stopped=stopped)
    assert result.text is None
    assert "canceled" in result.message
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_interrupt_terminates_running_child_and_preserves_draft(monkeypatch) -> None:
    processes: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        original_wait = process.wait
        calls = 0

        def wait(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        process.wait = wait
        return process

    monkeypatch.setattr("xdog.tui.external_editor.subprocess.Popen", popen)
    result = edit_draft("draft", command=command("import time; time.sleep(60)"))
    assert result.text is None
    assert "interrupted" in result.message
    assert processes[0].poll() is not None


def test_oversized_original_never_starts_editor() -> None:
    assert "exceeds 1 MiB" in edit_draft("x" * (1024 * 1024 + 1), command="never-executed").message
