from dataclasses import replace

import pytest
from xdog.eyes.errors import SourceError
from xdog.eyes.loader import parse


def test_content_fingerprint_and_filtering(tmp_path, observer_data):
    from xdog.eyes.sources.file import scan

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_bytes(b"abc")
    (tmp_path / "src" / "ignored.py").write_text("ignore")
    (tmp_path / "other.txt").write_text("ignore")
    source = replace(parse(observer_data, base_dir=tmp_path).sources[0],
                     include=("src/**/*.py",), exclude=("**/ignored.py",))
    snapshot = scan(source)
    assert [path for path, _ in snapshot.entries] == ["src/a.py"]
    state = snapshot.entries[0][1]
    assert state.digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert state.size == 3
    (tmp_path / "src" / "a.py").touch()
    assert scan(source) == snapshot


def test_symlinks_and_nonregular_files_not_followed(tmp_path, observer_data):
    import os

    from xdog.eyes.sources.file import scan

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "directory-link").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "secret")
    os.mkfifo(root / "fifo")
    source = replace(parse(observer_data, base_dir=root).sources[0])
    assert scan(source).entries == ()
    with pytest.raises(SourceError):
        scan(replace(source, root=root / "directory-link"))


@pytest.mark.parametrize("limit", ["file_size", "file_count", "missing_root"])
def test_scan_errors_do_not_produce_partial_snapshots(tmp_path, observer_data, limit):
    from xdog.eyes.sources.file import scan

    source = parse(observer_data, base_dir=tmp_path).sources[0]
    (tmp_path / "a").write_text("abcd")
    if limit == "file_size":
        source = replace(source, max_file_bytes=3)
    elif limit == "file_count":
        (tmp_path / "b").write_text("b")
        source = replace(source, max_files=1)
    else:
        source = replace(source, root=tmp_path / "missing")
    with pytest.raises(SourceError):
        scan(source)


def test_unreadable_or_replaced_file_is_not_deleted(tmp_path, observer_data, monkeypatch):
    import os

    from xdog.eyes.sources.file import scan

    (tmp_path / "a").write_text("abcd")
    source = parse(observer_data, base_dir=tmp_path).sources[0]
    original = os.open

    def deny(path, flags, *args, **kwargs):
        if path == "a":
            raise PermissionError("denied")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny)
    with pytest.raises(SourceError):
        scan(source)


@pytest.mark.parametrize("pattern,path,expected", [
    ("**", "a", True), ("src/**/*.py", "src/a.py", True),
    ("src/**/*.py", "src/nested/a.py", True), ("*.py", "src/a.py", False),
    ("src/?.py", "src/a.py", True), ("src/?.py", "src/ab.py", False),
    ("**/ignored.py", "ignored.py", True), ("**/ignored.py", "src/ignored.py", True),
])
def test_segment_patterns(pattern, path, expected):
    from xdog.eyes.sources.patterns import matches

    assert matches(path, pattern) is expected


def test_replacement_with_symlink_during_open_is_rejected(tmp_path, observer_data, monkeypatch):
    import os

    from xdog.eyes.sources.file import scan

    (tmp_path / "a").write_text("old")
    target = tmp_path / "target"
    target.write_text("secret")
    original = os.open

    def replace_then_open(path, flags, *args, **kwargs):
        if path == "a":
            (tmp_path / "a").unlink()
            (tmp_path / "a").symlink_to(target)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_then_open)
    with pytest.raises(SourceError):
        scan(parse(observer_data, base_dir=tmp_path).sources[0])


async def test_cancelled_scan_waits_for_started_thread(tmp_path, observer_data, monkeypatch):
    import asyncio
    import threading

    from xdog.eyes.sources import file

    entered, release = threading.Event(), threading.Event()

    def blocked_scan(source):
        entered.set()
        assert release.wait(2)
        return file.FileSnapshot(())

    monkeypatch.setattr(file, "scan", blocked_scan)
    task = asyncio.create_task(file.scan_async(parse(observer_data, base_dir=tmp_path).sources[0]))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
