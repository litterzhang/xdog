"""Real read-only Git status/diff and disk-paged review behavior."""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest
from xdog.tui.components.review_panel import ReviewPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.repository_review import RepositoryReview
from xdog.tui.utils import visible_width


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is required for repository review")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "xdog-test")
    git(root, "config", "user.email", "xdog-test@example.invalid")
    git(root, "config", "commit.gpgSign", "false")
    return root


def git(root, *args):
    subprocess.run(["git", "--no-pager", "-c", "core.hooksPath=", "-C", str(root), *args],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def commit(root):
    git(root, "add", "-A")
    git(root, "commit", "-qm", "ephemeral fixture")


def wait(backend, predicate=lambda view: not view.busy):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        view = backend.snapshot()
        if predicate(view):
            assert not view.error, view.error
            return view
        time.sleep(0.005)
    raise AssertionError(backend.snapshot())


@pytest.fixture
def review(repo):
    backend = RepositoryReview(repo)
    try:
        yield backend
    finally:
        backend.close()
        if backend._thread:
            backend._thread.join(3)
            assert not backend._thread.is_alive()
        if backend._directory:
            assert not backend._directory.exists()


def refresh(backend):
    backend.refresh()
    return wait(backend)


def page(backend, file, lane="working", start=0, column=0, width=100):
    backend.page(file.key, lane, start, column, width)
    return wait(backend, lambda view: not view.busy and view.key == file.key and view.lane == lane)


def test_repository_wide_staged_unstaged_untracked_and_read_only(repo, review):
    (repo / "file.py").write_text("base\n")
    (repo / "outside.py").write_text("base\n")
    (repo / "sub").mkdir()
    commit(repo)
    (repo / "file.py").write_text("staged\n")
    git(repo, "add", "file.py")
    (repo / "file.py").write_text("working\n")
    (repo / "outside.py").write_text("changed\n")
    (repo / "untracked.py").write_text("untracked content\n")
    index = (repo / ".git" / "index").read_bytes()
    work = (repo / "file.py").read_bytes()
    backend = RepositoryReview(repo / "sub")
    try:
        view = refresh(backend)
        assert view.root == str(repo)
        assert {file.path: file.status for file in view.files} == {
            "file.py": "MM", "outside.py": " M", "untracked.py": "??",
        }
        files = {file.path: file for file in view.files}
        staged = page(backend, files["file.py"], "staged")
        assert "+staged" in staged.rows and "+working" not in staged.rows
        working = page(backend, files["file.py"])
        assert "+working" in working.rows and "-staged" in working.rows
        untracked = page(backend, files["untracked.py"])
        assert "+untracked content" in untracked.rows
        not_staged = page(backend, files["untracked.py"], "staged")
        assert any("not in the index" in row for row in not_staged.rows)
        assert (repo / ".git" / "index").read_bytes() == index
        assert (repo / "file.py").read_bytes() == work
        assert not hasattr(backend, "apply") and not hasattr(backend, "stage")
    finally:
        backend.close()
        backend._thread.join(3)


def test_renames_deletions_literal_pathspecs_and_unsafe_display_names(repo, review):
    (repo / "old name.py").write_text("rename me\n")
    (repo / "deleted.py").write_text("delete me\n")
    commit(repo)
    git(repo, "mv", "old name.py", "new name.py")
    (repo / "deleted.py").unlink()
    (repo / ":(glob)*.py").write_text("literal\n")
    (repo / "newline\nfile").write_text("safe text\n")
    view = refresh(review)
    files = {file.path: file for file in view.files}
    rename = files["new name.py"]
    assert rename.status[0] == "R" and rename.original == "old name.py"
    rows = page(review, rename, "staged").rows
    assert any("rename from old name.py" in row for row in rows)
    assert any("-delete me" in row for row in page(review, files["deleted.py"]).rows)
    assert "+literal" in page(review, files[":(glob)*.py"]).rows
    assert "\n" not in files["newline\nfile"].label


def test_untracked_binary_symlink_and_fifo_are_not_followed_or_blocking(repo, review):
    (repo / "binary").write_bytes(b"a\0b")
    target = repo.parent / "secret"
    target.write_text("do not read secret")
    (repo / "link").symlink_to(target)
    if hasattr(os, "mkfifo"):
        os.mkfifo(repo / "fifo")
    files = {file.path: file for file in refresh(review).files}
    assert "binary" in " ".join(page(review, files["binary"]).rows)
    rows = page(review, files["link"]).rows
    assert any("Symlink target:" in row for row in rows)
    assert not any("do not read secret" in row for row in rows)
    if "fifo" in files:
        assert any("Not a regular file" in row for row in page(review, files["fifo"]).rows)


def test_large_patch_is_paged_and_all_hunks_remain_navigable(repo, review):
    path = repo / "large.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(10_000)) + "\n")
    commit(repo)
    lines = path.read_text().splitlines()
    for offset in range(10, 9990, 100):
        lines[offset] = f"CHANGED-{offset}"
    path.write_text("\n".join(lines) + "\n")
    file = refresh(review).files[0]
    first = page(review, file)
    assert len(first.rows) <= 64 and len(first.hunks) == 100
    last = page(review, file, start=first.hunks[-1])
    assert any("CHANGED-9910" in row for row in last.rows)
    assert last.total > 800


def test_long_unicode_lines_horizontal_pages_and_terminal_controls(repo, review):
    (repo / "long.txt").write_text("prefix-" + "界👩‍💻" * 20_000 + "TAIL\x1b[2J\x1b]52;c;bad\x07\n")
    file = refresh(review).files[0]
    view = page(review, file, width=20)
    assert all(visible_width(row) <= 20 for row in view.rows)
    assert all("\x1b" not in row for row in view.rows)
    last = page(review, file, start=view.total - 1, column=0, width=20)
    assert "�" not in "".join(last.rows)


def test_fsmonitor_external_diff_and_textconv_are_never_executed(repo, review):
    (repo / "file.txt").write_text("base\n")
    (repo / ".gitattributes").write_text("*.txt diff=unsafe\n")
    commit(repo)
    marker = repo.parent / "executed"
    script = repo.parent / "hook"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o700)
    git(repo, "config", "core.fsmonitor", str(script))
    git(repo, "config", "diff.external", str(script))
    git(repo, "config", "diff.unsafe.textconv", str(script))
    (repo / "file.txt").write_text("changed\n")
    files = refresh(review).files
    page(review, next(file for file in files if file.path == "file.txt"))
    assert not marker.exists()


def test_nested_repository_drilldown_and_back_do_not_change_workspace(repo, review):
    inner = repo / "inner"
    inner.mkdir()
    git(inner, "init", "-q")
    (inner / "nested.py").write_text("nested\n")
    file = refresh(review).files[0]
    assert file.repository
    review.enter(file.key)
    nested = wait(review)
    assert nested.root == str(inner) and nested.nested
    assert [file.path for file in nested.files] == ["nested.py"]
    review.back()
    assert wait(review).root == str(repo)
    assert review.workspace == repo


def test_panel_file_filter_hunks_lanes_tiny_layout_and_focused_paste(repo, review):
    (repo / "a.py").write_text("base\n")
    (repo / "b.py").write_text("base\n")
    commit(repo)
    (repo / "a.py").write_text("changed a\n")
    (repo / "b.py").write_text("changed b\n")
    closed = []
    panel = ReviewPanel(review, lambda: closed.append(True))
    panel.focused = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        panel.poll()
        if panel.view.rows and not panel.view.busy:
            break
        time.sleep(0.005)
    panel.handle_input(KeyEvent("/"))
    panel.handle_paste("b.py")
    panel.handle_input(KeyEvent("enter"))
    while time.monotonic() < deadline:
        panel.poll()
        if panel.view.rows and panel.view.key == panel._selected and not panel.view.busy:
            break
        time.sleep(0.005)
    assert panel._files()[0].path == "b.py"
    for height in (0, 1, 2, 4, 12):
        panel.set_render_budget(height)
        rows = panel.render(12)
        assert len(rows) <= height
        assert all(visible_width(row) <= 12 for row in rows)
    panel.handle_input(KeyEvent("n"))
    assert panel._start in panel.view.hunks
    panel.handle_input(KeyEvent("s"))
    assert panel._lane == "staged"
    panel.handle_input(KeyEvent("escape"))
    assert closed == [True]


def test_close_reaps_a_blocked_git_child_and_removes_private_spool(repo, monkeypatch):
    import sys

    real_popen = subprocess.Popen
    processes = []

    def slow(args, **kwargs):
        if args[0] == "git":
            args = [sys.executable, "-c", "import time; time.sleep(60)"]
        process = real_popen(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("xdog.tui.repository_review.subprocess.Popen", slow)
    backend = RepositoryReview(repo)
    backend.refresh()
    deadline = time.monotonic() + 3
    while not processes and time.monotonic() < deadline:
        time.sleep(0.005)
    assert processes
    backend.close()
    backend._thread.join(3)
    assert not backend._thread.is_alive()
    assert processes[0].poll() is not None
    assert not backend._directory.exists()


def test_no_repository_reports_error_without_stdout_corruption(tmp_path):
    backend = RepositoryReview(tmp_path)
    backend.refresh()
    deadline = time.monotonic() + 3
    while backend.snapshot().busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert "not a git repository" in backend.snapshot().error.lower()
    backend.close()
    backend._thread.join(3)


def test_refresh_fences_stale_patch_and_live_changes_are_visible(repo, review):
    path = repo / "file.txt"
    path.write_text("base\n")
    commit(repo)
    path.write_text("before\n")
    file = refresh(review).files[0]
    assert "+before" in page(review, file).rows
    path.write_text("after\n")
    file = refresh(review).files[0]
    assert "+after" in page(review, file).rows
    assert "+before" not in review.snapshot().rows


def test_timeout_is_reported_and_child_reaped(repo, monkeypatch):
    import sys

    original = subprocess.Popen
    processes = []

    def slow(args, **kwargs):
        process = original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("xdog.tui.repository_review.subprocess.Popen", slow)
    backend = RepositoryReview(repo, timeout=0.05)
    try:
        backend.refresh()
        deadline = time.monotonic() + 3
        while backend.snapshot().busy and time.monotonic() < deadline:
            time.sleep(0.005)
        assert "timed out" in backend.snapshot().error
        assert processes[0].poll() is not None
    finally:
        backend.close()
        backend._thread.join(3)


def test_superseded_patch_never_reuses_partial_rows_for_another_file(repo, review, monkeypatch):
    import threading

    for name in ("a.txt", "b.txt"):
        (repo / name).write_text("base\n")
    commit(repo)
    (repo / "a.txt").write_text("FIRST\n")
    (repo / "b.txt").write_text("SECOND\n")
    files = {file.path: file for file in refresh(review).files}
    assert "+SECOND" in page(review, files["b.txt"]).rows
    entered, release = threading.Event(), threading.Event()
    original = review._load_patch

    def blocked(request, file):
        if file.path == "a.txt":
            review._document = None
            review._db.execute("DELETE FROM lines")
            review._total = 0
            entered.set()
            assert release.wait(3)
            review._check(request)
        return original(request, file)

    monkeypatch.setattr(review, "_load_patch", blocked)
    review.page(files["a.txt"].key, "working", 0, 0, 100)
    assert entered.wait(3)
    review.page(files["b.txt"].key, "working", 0, 0, 100)
    release.set()
    view = wait(review, lambda value: not value.busy and value.key == files["b.txt"].key)
    assert "+SECOND" in view.rows
    assert "+FIRST" not in view.rows


def test_private_cache_in_workspace_is_omitted_from_status(repo, monkeypatch):
    import tempfile

    original = tempfile.TemporaryDirectory
    monkeypatch.setattr("xdog.tui.repository_review.tempfile.TemporaryDirectory",
                        lambda **kwargs: original(dir=repo, **kwargs))
    (repo / "real.txt").write_text("real\n")
    backend = RepositoryReview(repo)
    try:
        assert [file.path for file in refresh(backend).files] == ["real.txt"]
    finally:
        backend.close()
        backend._thread.join(3)


def test_patch_display_expands_tabs_preserves_joiners_and_removes_bidi_controls(repo, review):
    (repo / "unicode.txt").write_text("\t👩‍💻\u202e end\n")
    file = refresh(review).files[0]
    view = page(review, file, width=40)
    assert any("👩‍💻" in row for row in view.rows)
    assert all("\t" not in row and "\u202e" not in row for row in view.rows)
    assert all(visible_width(row) <= 40 for row in view.rows)


def test_panel_adopts_clamped_page_offset_so_pageup_is_not_stuck_beyond_eof(repo, review):
    (repo / "small.txt").write_text("content\n")
    panel = ReviewPanel(review, lambda: None)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        panel.poll()
        if panel.view.rows and not panel.view.busy:
            break
        time.sleep(0.005)
    panel._start = 100_000
    panel._request_page()
    while time.monotonic() < deadline:
        panel.poll()
        if not panel.view.busy and panel._start < panel.view.total:
            break
        time.sleep(0.005)
    assert panel._start < panel.view.total
    panel.handle_input(KeyEvent("pageup"))
    assert panel._start == 0
