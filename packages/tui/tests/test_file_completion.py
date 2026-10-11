"""The live editor's path completion stays bounded and off the UI thread."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from xdog.tui.autocomplete import CompletionItem, StaticCompletionProvider
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi, visible_width


class Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


def wait_for(provider: WorkspaceFileCompletionProvider, prefix: str) -> tuple[CompletionItem, ...]:
    provider.get_completions(prefix)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with provider._condition:
            if provider._completed == prefix and not provider._background:
                return tuple(provider.get_completions(prefix))
        time.sleep(0.005)
    raise AssertionError(f"Completion did not settle: {prefix}")


@pytest.fixture
def workspace(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello.py").write_text("secret file content")
    (tmp_path / "hello world.txt").touch()
    (tmp_path / '.hidden').touch()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.js").touch()
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    provider = WorkspaceFileCompletionProvider(tmp_path)
    try:
        yield provider
    finally:
        provider.close()
        if provider._thread:
            provider._thread.join(3)
            assert not provider._thread.is_alive()


def test_workspace_fuzzy_search_and_exclusions(workspace) -> None:
    assert [item.text for item in wait_for(workspace, "@shp")] == ["@src/hello.py"]
    paths = [item.text for item in wait_for(workspace, "@")]
    assert "@src/" in paths
    assert '@"hello world.txt"' in paths
    assert not any("hidden" in path or "node_modules" in path or "loop/" in path for path in paths)


@pytest.mark.parametrize("prefix, expected", [
    ("@src/h", "@src/hello.py"), ("./src/h", "./src/hello.py"),
    ("@./src/h", "@./src/hello.py"), ('@"hello w', '@"hello world.txt"'),
])
def test_explicit_and_quoted_paths(workspace, prefix: str, expected: str) -> None:
    assert expected in [item.text for item in wait_for(workspace, prefix)]


def test_parent_absolute_and_home_paths(workspace, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for prefix, expected in [
        ("~/src/h", "~/src/hello.py"),
        (str(tmp_path) + "/src/h", str(tmp_path) + "/src/hello.py"),
        ("../" + tmp_path.name + "/src/h", "../" + tmp_path.name + "/src/hello.py"),
        ("./.h", "./.hidden"),
    ]:
        assert expected in [item.text for item in wait_for(workspace, prefix)]


def test_control_char_filenames_are_not_terminal_suggestions(workspace) -> None:
    (workspace.root / "evil\x1b[2J.txt").touch()
    (workspace.root / "bad\nfile").touch()
    assert not any("evil" in item.text or "bad" in item.text for item in wait_for(workspace, "./"))


def test_entry_and_result_budgets(tmp_path: Path) -> None:
    for i in range(40):
        (tmp_path / str(i)).touch()
    provider = WorkspaceFileCompletionProvider(tmp_path, max_entries=5, max_results=2)
    try:
        assert len(wait_for(provider, "@")) == 2
        assert provider._workspace is not None
        assert provider._workspace.count == 40  # Budget bounds batches, not total discovery.
    finally:
        provider.close()


def test_missing_directory_completes_empty(workspace) -> None:
    assert wait_for(workspace, "@missing/") == ()


def test_worker_coalesces_requests_and_fences_stale_results(tmp_path: Path, monkeypatch) -> None:
    provider = WorkspaceFileCompletionProvider(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def blocked(prefix: str):
        calls.append(prefix)
        if prefix == "@old":
            entered.set()
            assert release.wait(3)
        return (CompletionItem(prefix + ".txt"),)

    monkeypatch.setattr(provider, "_complete", blocked)
    try:
        assert provider.get_completions("@old") == ()
        assert entered.wait(3)
        # This returns while the filesystem worker is blocked, never on the UI thread.
        assert provider.get_completions("@discard") == ()
        assert provider.get_completions("@new") == ()
        release.set()
        assert [item.text for item in wait_for(provider, "@new")] == ["@new.txt"]
        assert calls == ["@old", "@new"]
        with provider._condition:
            assert provider._completed == "@new"
    finally:
        release.set()
        provider.close()
        assert provider._thread is not None
        provider._thread.join(3)
        assert not provider._thread.is_alive()
    assert provider.get_completions("@new") == ()


def test_editor_replaces_only_active_reference_and_never_submits_on_selection() -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@src/hello.py")]))
    submitted: list[str] = []
    editor.on_submit = submitted.append
    editor.set_text("Review @src/he then explain")
    editor._cursor = len("Review @src/he")
    assert editor.poll_autocomplete()
    assert editor.handle_input(KeyEvent(key="enter"))
    assert editor.get_text() == "Review @src/hello.py then explain"
    assert not submitted
    assert not editor.poll_autocomplete()  # Acceptance remains dismissed across ticks.
    editor.handle_input(KeyEvent(key="z", alt=True))
    assert editor.get_text() == "Review @src/he then explain"


def test_completion_preserves_cursor_suffix_and_quoted_spaces() -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem('@"hello world.txt"')]))
    editor.set_text('Read @"hello old.txt" please')
    editor._cursor = len('Read @"hello')
    editor.poll_autocomplete()
    editor.handle_input(KeyEvent(key="tab"))
    assert editor.get_text() == 'Read @"hello world.txt" please'
    assert editor._cursor == len('Read @"hello world.txt"')


def test_completion_escape_dismissal_and_tab_reopen() -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.txt")]))
    escaped: list[bool] = []
    editor.on_escape = lambda: escaped.append(True)
    editor.set_text("look @o")
    editor.handle_input(KeyEvent(key="escape"))
    assert not editor.autocomplete_active
    assert not editor.poll_autocomplete()
    editor.handle_input(KeyEvent(key="tab"))
    assert editor.autocomplete_active
    editor.handle_input(KeyEvent(key="escape"))
    editor.handle_input(KeyEvent(key="escape"))
    assert escaped == [True]


def test_escape_during_pending_scan_does_not_cancel_agent(workspace, monkeypatch) -> None:
    editor = InputPanel(Theme())
    monkeypatch.setattr(workspace, "get_completions", lambda prefix: ())
    editor.set_autocomplete_provider(workspace)
    editor.set_text("look @he")
    assert editor.autocomplete_active
    escaped: list[bool] = []
    editor.on_escape = lambda: escaped.append(True)
    editor.handle_input(KeyEvent(key="escape"))
    assert not editor.autocomplete_active
    assert not editor.poll_autocomplete()
    assert escaped == []


def test_slash_commands_keep_existing_submit_semantics() -> None:
    editor = InputPanel(Theme(), command_provider=lambda: {"help": "Help"})
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.txt")]))
    submitted: list[str] = []
    editor.on_submit = submitted.append
    editor.set_text("/he")
    editor.handle_input(KeyEvent(key="enter"))
    assert submitted == ["/help"]


def test_async_results_are_polled_not_applied_by_worker(workspace) -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(workspace)
    editor.set_text("look @shp")
    assert editor._select_list is None
    wait_for(workspace, "@shp")
    assert editor._select_list is None
    assert editor.poll_autocomplete()
    assert not editor.poll_autocomplete()
    editor.handle_input(KeyEvent(key="tab"))
    assert editor.get_text() == "look @src/hello.py"


def test_tiny_editor_and_permission_focus_hide_completions() -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.txt")]))
    editor.set_text("@o")
    for budget in (1, 2, 3, 8):
        editor.set_render_budget(budget)
        lines = editor.render(12)
        assert len(lines) <= budget
        assert all(visible_width(line) <= 12 for line in lines)
    editor.set_autocomplete_visible(False)
    assert "@one.txt" not in "\n".join(strip_ansi(line) for line in editor.render(50))


def test_provider_change_clears_dismissed_or_stale_selection() -> None:
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.txt")]))
    editor.set_text("@o")
    editor.handle_input(KeyEvent(key="escape"))
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@other.txt")]))
    assert editor._select_list is not None
    assert editor._select_list.selected_value == "@other.txt"
    editor.set_autocomplete_provider(None)
    assert not editor.autocomplete_active


def test_unicode_filenames_cannot_crash_completion_worker(workspace) -> None:
    (workspace.root / "İfile.py").touch()
    (workspace.root / "文件.py").touch()
    assert "@İfile.py" in [item.text for item in wait_for(workspace, "@İ")]
    assert "@文件.py" in [item.text for item in wait_for(workspace, "@文件")]


def test_cached_completion_refreshes_new_files(workspace) -> None:
    assert wait_for(workspace, "@new") == ()
    (workspace.root / "new.py").touch()
    with workspace._condition:
        workspace._indexed_at = 0.0
        workspace._requested_at = 0.0
    workspace.get_completions("@new")
    # wait_for must observe the refreshed result, not the old completion stamp.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if [item.text for item in workspace.get_completions("@new")] == ["@new.py"]:
            break
        time.sleep(0.005)
    else:
        raise AssertionError("New file did not appear after cache refresh")


def test_completion_poll_does_not_reparse_unchanged_large_draft(monkeypatch) -> None:
    import xdog.tui.components.input_panel as module

    real_span = module._path_completion_span
    calls: list[str] = []

    def counted(value: str, cursor: int):
        calls.append(value)
        return real_span(value, cursor)

    monkeypatch.setattr(module, "_path_completion_span", counted)
    editor = InputPanel(Theme())
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.txt")]))
    editor.set_text(("large earlier context\n" * 10_000) + "Review @o")
    count = len(calls)
    for _ in range(10):
        assert not editor.poll_autocomplete()
    assert len(calls) == count
