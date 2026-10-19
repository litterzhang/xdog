"""Data-only report contract and no-follow/atomic file boundaries."""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from xdog.claw.core.planning import task_reports
from xdog.claw.core.planning.task_reports import (
    MAX_REPORT_BYTES,
    TaskReport,
    TaskReportError,
    atomic_write_report,
    read_report,
)


@pytest.fixture
def report() -> TaskReport:
    return TaskReport("t-123", "a_123", 1, "submitted", "已完成", ("artifact: result.txt",), ())


def test_roundtrip_immutable_data_only(tmp_path, report):
    path = tmp_path / "report.json"
    assert atomic_write_report(path, report) is None
    loaded = read_report(path, "t-123", "a_123")
    assert loaded == report
    assert TaskReport.from_dict(report.to_dict()) == report
    assert json.loads(path.read_bytes()) == report.to_dict()
    assert isinstance(loaded.evidence, tuple)
    assert not hasattr(loaded, "__dict__")
    with pytest.raises(FrozenInstanceError):
        loaded.revision = 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("state", ["running", "submitted", "blocked"])
def test_states_do_not_imply_transitions(tmp_path, report, state):
    path = tmp_path / "report.json"
    atomic_write_report(path, replace(report, state=state))
    assert read_report(path, report.task_id, report.attempt_id).state == state


@pytest.mark.parametrize("field,value", [
    ("revision", True), ("revision", False), ("revision", 0), ("revision", -1),
    ("revision", 1.0), ("revision", "1"), ("revision", None),
    ("state", "complete"), ("state", "accepted"), ("state", []),
    ("summary", None), ("summary", {}), ("summary", 1),
    ("evidence", "output"), ("evidence", {}), ("evidence", [1]), ("evidence", [None]),
    ("blockers", None), ("blockers", [False]), ("blockers", [["nested"]]),
])
def test_strict_fields(tmp_path, report, field, value):
    data = report.to_dict()
    data[field] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)


@pytest.mark.parametrize("field", ["task_id", "attempt_id"])
@pytest.mark.parametrize("value", [
    "", ".", "..", "../task", "a/b", "a\\b", "_a", "-a", "a b", "a\n", "a\0", "任务", "a" * 129,
    10, None,
])
def test_ids_are_safe_and_strict(tmp_path, report, field, value):
    data = report.to_dict()
    data[field] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)
    with pytest.raises(TaskReportError):
        TaskReport.from_dict(data)


def test_boundary_id_and_monotonicity_is_callers_responsibility(tmp_path, report):
    path = tmp_path / "report.json"
    report = replace(report, task_id="A" * 128, attempt_id="0-a_A", revision=10)
    atomic_write_report(path, report)
    assert read_report(path, report.task_id, report.attempt_id) == report
    # No hidden tracker state or CAS: the owning session must fence freshness.
    previous = replace(report, revision=1)
    atomic_write_report(path, previous)
    assert read_report(path, report.task_id, report.attempt_id) == previous


@pytest.mark.parametrize("mutation", ["missing", "extra", "list", "null", "string"])
def test_exact_object_schema(tmp_path, report, mutation):
    data = report.to_dict()
    if mutation == "missing":
        del data["blockers"]
    elif mutation == "extra":
        data["session"] = {"handle": 3}
    else:
        data = {"list": [], "null": None, "string": "report"}[mutation]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)


@pytest.mark.parametrize("raw", [
    b"", b"{", b"{}{}", b"// comment\n{}", b"{\"a\": 1,}", b"\xff", b"\xef\xbb\xbf{}",
    b'{"task_id":"t-123","task_id":"t-123"}',
    b'{"task_id":"t-123","task_\\u0069d":"t-123"}',
    b'{"extra":{"same":1,"same":2}}', b"NaN", b"Infinity", b"-Infinity",
    b'{"revision":NaN}', b"[" * 2000 + b"]" * 2000,
])
def test_invalid_json(tmp_path, report, raw):
    path = tmp_path / "report.json"
    path.write_bytes(raw)
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)


def test_duplicate_schema_key_in_otherwise_valid_report(tmp_path, report):
    path = tmp_path / "report.json"
    payload = json.dumps(report.to_dict())
    path.write_text(payload[:-1] + ', "revision": 2}')
    with pytest.raises(TaskReportError, match="duplicate"):
        read_report(path, report.task_id, report.attempt_id)


@pytest.mark.parametrize("field", ["summary", "evidence", "blockers"])
def test_lone_surrogates_rejected(tmp_path, report, field):
    data = report.to_dict()
    data[field] = "\ud800" if field == "summary" else ["\ud800"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)


@pytest.mark.parametrize("expected_task,expected_attempt", [
    ("different", "a_123"), ("t-123", "old-attempt"), ("../bad", "a_123"), ("t-123", ""),
])
def test_expected_identity(tmp_path, report, expected_task, expected_attempt):
    path = tmp_path / "report.json"
    atomic_write_report(path, report)
    with pytest.raises(TaskReportError):
        read_report(path, expected_task, expected_attempt)


def test_limit_on_read_and_write_preserves_previous(tmp_path, report):
    path = tmp_path / "report.json"
    atomic_write_report(path, report)
    with pytest.raises(TaskReportError, match="size limit"):
        atomic_write_report(path, replace(report, summary="x" * MAX_REPORT_BYTES))
    assert read_report(path, report.task_id, report.attempt_id) == report
    path.write_bytes(b" " * (MAX_REPORT_BYTES + 1))
    with pytest.raises(TaskReportError, match="size limit"):
        read_report(path, report.task_id, report.attempt_id)


def test_exact_limit_is_accepted(tmp_path, report):
    path = tmp_path / "report.json"
    empty = replace(report, summary="")
    overhead = len((json.dumps(empty.to_dict(), ensure_ascii=False) + "\n").encode())
    full = replace(empty, summary="x" * (MAX_REPORT_BYTES - overhead))
    atomic_write_report(path, full)
    assert path.stat().st_size == MAX_REPORT_BYTES
    assert read_report(path, full.task_id, full.attempt_id) == full


@pytest.mark.parametrize("kind", ["directory", "fifo", "symlink", "broken_symlink"])
def test_reject_nonregular_leaf_without_following_or_blocking(tmp_path, report, kind):
    path = tmp_path / "report.json"
    victim = tmp_path / "victim.json"
    atomic_write_report(victim, report)
    original = victim.read_bytes()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to(victim if kind == "symlink" else tmp_path / "missing")
    with pytest.raises(TaskReportError):
        read_report(path, report.task_id, report.attempt_id)
    with pytest.raises(TaskReportError):
        atomic_write_report(path, report)
    assert victim.read_bytes() == original
    assert not list(tmp_path.glob(".task-report-*.tmp"))


@pytest.mark.parametrize("intermediate", [False, True])
def test_no_symlink_directory_components(tmp_path, report, monkeypatch, intermediate):
    real = tmp_path / "real"
    (real / "nested").mkdir(parents=True)
    leaf = Path("nested/report.json") if intermediate else Path("report.json")
    atomic_write_report(real / leaf, report)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    for path in (link / leaf, Path("link") / leaf):
        with pytest.raises(TaskReportError):
            read_report(path, report.task_id, report.attempt_id)
        with pytest.raises(TaskReportError):
            atomic_write_report(path, report)
    assert read_report(real / leaf, report.task_id, report.attempt_id) == report


def test_relative_path_and_missing_parent_and_traversal(tmp_path, report, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("sub").mkdir()
    atomic_write_report("sub/report.json", report)
    assert read_report("sub/report.json", report.task_id, report.attempt_id) == report
    for path in ("sub/../report.json", "missing/report.json", ".", "absent.json"):
        with pytest.raises(TaskReportError):
            read_report(path, report.task_id, report.attempt_id)
    for path in ("sub/../report.json", "missing/report.json", "."):
        with pytest.raises(TaskReportError):
            atomic_write_report(path, report)
    assert not Path("missing").exists()


def test_replacement_has_private_mode_and_does_not_modify_hardlink(tmp_path, report):
    path = tmp_path / "report.json"
    path.write_text("old contents")
    path.chmod(0o666)
    other = tmp_path / "hardlink.json"
    os.link(path, other)
    atomic_write_report(path, report)
    assert other.read_text() == "old contents"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_report(path, report.task_id, report.attempt_id) == report


def test_atomic_visibility_and_cleanup(tmp_path, report, monkeypatch):
    path = tmp_path / "report.json"
    atomic_write_report(path, report)
    newer = replace(report, revision=2)
    original = os.replace

    def observe(source, destination, **kwargs):
        assert read_report(path, report.task_id, report.attempt_id) == report
        temporary = tmp_path / source
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        assert read_report(temporary, report.task_id, report.attempt_id) == newer
        return original(source, destination, **kwargs)

    monkeypatch.setattr(task_reports.os, "replace", observe)
    atomic_write_report(path, newer)
    assert read_report(path, report.task_id, report.attempt_id) == newer
    assert not list(tmp_path.glob(".task-report-*.tmp"))


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failed_write_keeps_old_file_and_cleans_up(tmp_path, report, monkeypatch, failure):
    path = tmp_path / "report.json"
    atomic_write_report(path, report)

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    monkeypatch.setattr(task_reports.os, failure, fail)
    with pytest.raises(TaskReportError, match="injected failure"):
        atomic_write_report(path, replace(report, revision=2))
    assert read_report(path, report.task_id, report.attempt_id) == report
    assert not list(tmp_path.glob(".task-report-*.tmp"))


def test_bounded_read_even_if_stat_is_stale(tmp_path, report, monkeypatch):
    path = tmp_path / "report.json"
    path.write_bytes(b" " * (MAX_REPORT_BYTES + 1))
    original = os.fstat

    def stale(fd):
        info = list(original(fd))
        info[6] = 1  # st_size
        return os.stat_result(info)

    monkeypatch.setattr(task_reports.os, "fstat", stale)
    with pytest.raises(TaskReportError, match="size limit"):
        read_report(path, report.task_id, report.attempt_id)


def test_runtime_objects_and_mutable_containers_never_serialize(tmp_path, report):
    for fields in ({"summary": object()}, {"evidence": (object(),)}, {"blockers": []}, {"evidence": ["x"]}):
        with pytest.raises(TaskReportError):
            replace(report, **fields)
    with pytest.raises(TaskReportError):
        atomic_write_report(tmp_path / "report.json", report.to_dict())
    assert not list(tmp_path.iterdir())


def test_parent_directory_handle_stays_pinned_during_rename(tmp_path, report, monkeypatch):
    directory = tmp_path / "parent"
    directory.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    atomic_write_report(directory / "report.json", report)
    atomic_write_report(other / "report.json", report)
    retired = tmp_path / "retired"
    original_open = os.open

    def swap_parent(path, flags, *args, **kwargs):
        if str(path).startswith(".task-report-"):
            directory.rename(retired)
            directory.symlink_to(other, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(task_reports.os, "open", swap_parent)
    newer = replace(report, revision=2)
    atomic_write_report(directory / "report.json", newer)
    assert read_report(retired / "report.json", report.task_id, report.attempt_id) == newer
    assert read_report(other / "report.json", report.task_id, report.attempt_id) == report
    assert not list(retired.glob(".task-report-*.tmp"))
    with pytest.raises(TaskReportError):
        read_report(directory / "report.json", report.task_id, report.attempt_id)


def test_leaf_symlink_race_is_replaced_not_followed(tmp_path, report, monkeypatch):
    path = tmp_path / "report.json"
    victim = tmp_path / "victim.json"
    atomic_write_report(path, report)
    atomic_write_report(victim, report)
    original_replace = os.replace

    def swap_leaf(source, destination, **kwargs):
        path.unlink()
        path.symlink_to(victim)
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(task_reports.os, "replace", swap_leaf)
    newer = replace(report, revision=2)
    atomic_write_report(path, newer)
    assert not path.is_symlink()
    assert read_report(path, report.task_id, report.attempt_id) == newer
    assert read_report(victim, report.task_id, report.attempt_id) == report


def test_directory_sync_failure_is_explicit_after_atomic_replace(tmp_path, report, monkeypatch):
    path = tmp_path / "report.json"
    atomic_write_report(path, report)
    original_sync = os.fsync

    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync failed")
        original_sync(fd)

    monkeypatch.setattr(task_reports.os, "fsync", fail_directory_sync)
    newer = replace(report, revision=2)
    with pytest.raises(TaskReportError, match="directory sync failed"):
        atomic_write_report(path, newer)
    # Error does not promise rollback if the replace has already succeeded.
    assert read_report(path, report.task_id, report.attempt_id) == newer
    assert not list(tmp_path.glob(".task-report-*.tmp"))


@pytest.mark.skipif(sys.platform != "linux" or not Path("/proc/self/fd").is_dir(),
                    reason="Linux /proc fd accounting required")
@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_repeated_nonregular_report_reads_do_not_leak_fds(tmp_path, report, kind):
    path = tmp_path / "invalid-report"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(32):
        with pytest.raises(TaskReportError):
            read_report(path, report.task_id, report.attempt_id)
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before, f"{kind} reads leaked descriptors: {before} -> {after}"
