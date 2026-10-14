"""Actual Git ignores and complete discovery across small worker batches."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi, visible_width


class Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    if not shutil.which("git"):
        pytest.skip("requires Git for ignore semantics")
    # Keep test repositories independent of user configuration/environment.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    return root


def settled(provider: WorkspaceFileCompletionProvider, prefix: str) -> list[str]:
    provider.get_completions(prefix)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with provider._condition:
            if provider._completed == prefix and not provider._background:
                return [item.text for item in provider.get_completions(prefix)]
        time.sleep(0.005)
    raise AssertionError(f"Index/query did not finish: {provider.get_completion_status(prefix)}")


@pytest.fixture
def make_provider() -> Iterator:
    providers: list[WorkspaceFileCompletionProvider] = []

    def make(root: Path, **kwargs) -> WorkspaceFileCompletionProvider:
        provider = WorkspaceFileCompletionProvider(root, **kwargs)
        providers.append(provider)
        return provider

    yield make
    for provider in providers:
        provider.close()
        if provider._thread is not None:
            provider._thread.join(3)
            assert not provider._thread.is_alive()
        if provider._workspace is not None:
            assert not provider._workspace.directory.exists()


def test_git_nested_negations_global_and_info_excludes_and_tracked_ignored(repo, tmp_path, monkeypatch,
                                                                          make_provider) -> None:
    (repo / ".gitignore").write_text("*.log\n!keep.log\nbuild/\n")
    (repo / "nested").mkdir()
    (repo / "nested" / ".gitignore").write_text("*.tmp\n!allowed.tmp\n")
    (repo / "build").mkdir()
    for name in ("allowed.py", "drop.log", "keep.log", "tracked.log", ".hidden-tracked.py", "info-hidden.py",
                 "global-hidden.py", "nested/drop.tmp", "nested/allowed.tmp", "build/drop.py", "build/tracked.py"):
        (repo / name).touch()
    git(repo, "add", "-f", "tracked.log", ".hidden-tracked.py", "build/tracked.py")
    index = (repo / ".git" / "index").read_bytes()
    (repo / ".git" / "info" / "exclude").write_text("info-hidden.py\n")
    excludes = tmp_path / "global-excludes"
    excludes.write_text("global-hidden.py\n")
    config = tmp_path / "global-config"
    config.write_text(f"[core]\n\texcludesFile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    provider = make_provider(repo, max_entries=2, max_results=50)
    paths = settled(provider, "@")
    assert set(paths) == {"@allowed.py", "@keep.log", "@tracked.log", "@.hidden-tracked.py",
                          "@nested/", "@nested/allowed.tmp", "@build/", "@build/tracked.py"}
    assert (repo / ".git" / "index").read_bytes() == index
    assert "Git ignore rules" in provider.get_completion_status("@")
    # Explicit directory lookup deliberately remains possible for ignored paths.
    assert "@build/drop.py" in settled(provider, "@build/")


def test_non_repository_ignore_rules_without_creating_workspace_metadata(tmp_path, make_provider) -> None:
    if not shutil.which("git"):
        pytest.skip("requires Git for non-repository ignore semantics")
    (tmp_path / ".gitignore").write_text("drop.py\n")
    (tmp_path / "drop.py").touch()
    (tmp_path / "keep.py").touch()
    provider = make_provider(tmp_path, max_entries=1)
    assert settled(provider, "@") == ["@keep.py"]
    assert not (tmp_path / ".git").exists()
    assert provider._workspace is not None
    assert (provider._workspace.directory / "git").exists()
    assert (provider._workspace.directory / "names.sqlite").stat().st_mode & 0o777 == 0o600


def test_workspace_subdirectory_is_scoped_and_reads_parent_ignores(repo, make_provider) -> None:
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "outside.py").touch()
    (repo / "sub").mkdir()
    (repo / "sub" / "inside.py").touch()
    (repo / "sub" / "drop.log").touch()
    provider = make_provider(repo / "sub")
    assert settled(provider, "@") == ["@inside.py"]


def test_git_environment_cannot_redirect_workspace_and_fsmonitor_hook_is_not_run(repo, tmp_path, monkeypatch,
                                                                               make_provider) -> None:
    (repo / "right.py").touch()
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "wrong.py").touch()
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "fsmonitor"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    git(repo, "config", "core.fsmonitor", str(hook))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    provider = make_provider(repo)
    assert settled(provider, "@") == ["@right.py"]
    assert not marker.exists()


def test_missing_git_is_visible_and_walker_resumes_without_a_total_cap(tmp_path, monkeypatch, make_provider) -> None:
    real = subprocess.Popen

    def no_git(args, **kwargs):
        if args[0] == "git":
            raise FileNotFoundError("controlled missing Git")
        return real(args, **kwargs)

    monkeypatch.setattr("xdog.tui.workspace_index.subprocess.Popen", no_git)
    for number in range(30):
        (tmp_path / f"file-{number:02}.py").touch()
    (tmp_path / "last-target.py").touch()
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    provider = make_provider(tmp_path, max_entries=2)
    assert settled(provider, "@last-target") == ["@last-target.py"]
    assert provider._workspace is not None and provider._workspace.count == 32
    assert "Git ignores unavailable" in provider.get_completion_status("@last-target")


def test_refresh_removes_deleted_and_newly_ignored_names(repo, make_provider) -> None:
    (repo / "old.py").touch()
    (repo / "now-ignored.py").touch()
    provider = make_provider(repo, refresh_seconds=0.1)
    assert "@old.py" in settled(provider, "@")
    (repo / "old.py").unlink()
    (repo / "new.py").touch()
    (repo / ".gitignore").write_text("now-ignored.py\n")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        paths = [item.text for item in provider.get_completions("@")]
        if paths == ["@new.py"] and not provider._background:
            break
        time.sleep(0.005)
    else:
        raise AssertionError(f"Refresh retained old/ignored names: {paths}")


def test_qualified_reference_uses_fuzzy_workspace_path_matching(repo, make_provider) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "hello.py").touch()
    provider = make_provider(repo)
    assert settled(provider, "@sr/hp") == ["@src/hello.py"]


def test_git_child_is_reaped_and_private_index_removed_when_closed(repo, monkeypatch, make_provider) -> None:
    import sys

    real = subprocess.Popen
    processes = []

    def slow_git(args, **kwargs):
        if args[0] == "git":
            args = [sys.executable, "-c", "import time; time.sleep(60)"]
        process = real(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("xdog.tui.workspace_index.subprocess.Popen", slow_git)
    provider = make_provider(repo)
    provider.get_completions("@x")
    deadline = time.monotonic() + 3
    while not processes and time.monotonic() < deadline:
        time.sleep(0.005)
    assert processes
    provider.close()
    assert provider._thread is not None
    provider._thread.join(3)
    assert not provider._thread.is_alive()
    assert processes[0].poll() is not None
    assert provider._workspace is not None and not provider._workspace.directory.exists()


def test_index_error_is_ui_feedback_not_an_unhandled_thread_traceback(tmp_path, monkeypatch, make_provider) -> None:
    failures = []
    monkeypatch.setattr(threading, "excepthook", failures.append)

    def fail(*args):
        raise sqlite3.OperationalError("controlled index failure")

    monkeypatch.setattr("xdog.tui.file_completion.WorkspaceIndex", fail)
    provider = make_provider(tmp_path)
    assert settled(provider, "@x") == []
    assert "controlled index failure" in provider.get_completion_status("@x")
    assert failures == []


def test_progress_and_fallback_fit_editor_budget_and_do_not_become_insertions() -> None:
    class Pending:
        def get_completions(self, prefix):
            return ()

        def get_completion_status(self, prefix):
            return "Indexing 20,001 paths · Git ignores unavailable\x1b[2J\nmore"

    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(Pending())
    editor.set_text("Review @late")
    for height in (1, 2, 3, 8):
        editor.set_render_budget(height)
        rows = editor.render(24)
        assert len(rows) <= height
        assert all(visible_width(row) <= 24 for row in rows)
        assert all("\x1b[2J" not in row for row in rows)
    assert "Indexing" in "\n".join(strip_ansi(row) for row in editor.render(80))
    editor.handle_input(KeyEvent("tab"))
    assert editor.get_text() == "Review @late"
    editor.handle_input(KeyEvent("escape"))
    assert "Indexing" not in "\n".join(editor.render(80))


def test_exact_slash_command_does_not_start_a_filesystem_lookup() -> None:
    class Unused:
        def get_completions(self, prefix):
            raise AssertionError("Known commands are not absolute paths")

    editor = InputPanel(Theme(), command_provider=lambda: {"keys": "Show keys"})
    editor.set_autocomplete_provider(Unused())
    editor.set_text("/keys")
    assert not editor.autocomplete_active


def test_progressive_results_preserve_selection_identity() -> None:
    from xdog.tui.autocomplete import CompletionItem, StaticCompletionProvider

    provider = StaticCompletionProvider([CompletionItem("@beta.py"), CompletionItem("@gamma.py")])
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(provider)
    editor.set_text("Read @")
    editor.handle_input(KeyEvent("down"))
    assert editor._select_list is not None and editor._select_list.selected_value == "@gamma.py"
    provider.items.insert(0, CompletionItem("@alpha.py"))
    assert editor.poll_autocomplete()
    assert editor._select_list.selected_value == "@gamma.py"
    editor.handle_input(KeyEvent("tab"))
    assert editor.get_text() == "Read @gamma.py"


def test_fragmented_git_records_use_independent_reader_and_writer_offsets(tmp_path, monkeypatch, make_provider) -> None:
    import sys

    (tmp_path / "file-target.py").touch()
    real = subprocess.Popen

    def fragmented(args, **kwargs):
        if args[0] == "git":
            args = [sys.executable, "-c", (
                "import sys,time; sys.stdout.buffer.write(b'file-'); sys.stdout.flush(); "
                "time.sleep(.05); sys.stdout.buffer.write(b'target.py\\0'); sys.stdout.flush()"
            )]
        return real(args, **kwargs)

    monkeypatch.setattr("xdog.tui.workspace_index.subprocess.Popen", fragmented)
    provider = make_provider(tmp_path, max_entries=1)
    assert settled(provider, "@target") == ["@file-target.py"]


def test_stalled_git_is_reaped_before_explicit_basic_fallback(tmp_path, monkeypatch, make_provider) -> None:
    import sys

    (tmp_path / "target.py").touch()
    processes = []
    real = subprocess.Popen

    def stalled(args, **kwargs):
        if args[0] == "git":
            args = [sys.executable, "-c", "import time; time.sleep(60)"]
        process = real(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("xdog.tui.workspace_index.subprocess.Popen", stalled)
    provider = make_provider(tmp_path)
    provider.get_completions("@target")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (provider._workspace is None or not processes):
        time.sleep(0.005)
    assert provider._workspace is not None and processes
    provider._workspace._started = time.monotonic() - 31
    assert settled(provider, "@target") == ["@target.py"]
    assert "Git ignores unavailable" in provider.get_completion_status("@target")
    assert processes[0].poll() is not None


def test_joined_emoji_names_are_allowed_but_bidi_controls_are_not(tmp_path, make_provider) -> None:
    (tmp_path / "👩‍💻.py").touch()
    (tmp_path / "evil\u202e.py").touch()
    provider = make_provider(tmp_path)
    assert settled(provider, "@") == ["@👩‍💻.py"]


def test_private_index_inside_workspace_does_not_index_itself(tmp_path, monkeypatch, make_provider) -> None:
    import tempfile

    original = tempfile.TemporaryDirectory

    def inside(**kwargs):
        return original(dir=tmp_path, **kwargs)

    monkeypatch.setattr("xdog.tui.workspace_index.tempfile.TemporaryDirectory", inside)
    (tmp_path / "target.py").touch()
    provider = make_provider(tmp_path)
    assert settled(provider, "@") == ["@target.py"]
    assert provider._workspace is not None and provider._workspace.count == 1


def test_nested_repository_contents_are_indexed_with_their_own_ignores(repo, make_provider) -> None:
    inner = repo / "nested-repo"
    inner.mkdir()
    git(inner, "init", "-q")
    (inner / ".gitignore").write_text("drop.py\n")
    (inner / "drop.py").touch()
    (inner / "nested-target.py").touch()
    provider = make_provider(repo, max_entries=1)
    assert settled(provider, "@nested-target") == ["@nested-repo/nested-target.py"]
    assert "nested-repo/drop.py" not in settled(provider, "@drop")


def test_git_worktree_metadata_file_is_supported(repo, tmp_path, make_provider) -> None:
    (repo / "target.py").touch()
    git(repo, "add", "target.py")
    git(repo, "-c", "user.name=xdog-test", "-c", "user.email=xdog-test@example.invalid",
        "-c", "commit.gpgSign=false", "-c", "core.hooksPath=", "commit", "-qm", "ephemeral test fixture")
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    assert (worktree / ".git").is_file()
    provider = make_provider(worktree)
    assert settled(provider, "@target") == ["@target.py"]


def test_initialized_submodule_files_are_fuzzy_searchable(repo, tmp_path, make_provider) -> None:
    source = tmp_path / "submodule-source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "module-target.py").touch()
    (source / ".gitignore").write_text("module-drop.py\n")
    git(source, "add", "module-target.py", ".gitignore")
    git(source, "-c", "user.name=xdog-test", "-c", "user.email=xdog-test@example.invalid",
        "-c", "commit.gpgSign=false", "-c", "core.hooksPath=", "commit", "-qm", "ephemeral submodule fixture")
    git(repo, "-c", "protocol.file.allow=always", "-c", "core.hooksPath=", "submodule", "add", "-q",
        str(source), "module")
    (repo / "module" / "module-drop.py").touch()
    provider = make_provider(repo, max_entries=1)
    assert settled(provider, "@module-target") == ["@module/module-target.py"]
    assert settled(provider, "@module-drop") == []
