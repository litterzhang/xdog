"""Archives are private create-only artifacts, not a second-resolution overwrite target."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from xdog.claw.core.compaction.transcript import archive_transcript


def test_same_second_concurrent_archives_keep_every_original_and_private_mode(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.claw.core.compaction.transcript.time.strftime', lambda _format: 'fixed-time')
    def archive(index):
        return archive_transcript([{'role': 'user', 'content': f'original-{index}'}], tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(archive, range(12)))
    assert len(set(paths)) == 12
    for index, path in enumerate(paths):
        assert f'original-{index}' in path.read_text()
        if os.name == 'posix':
            assert path.stat().st_mode & 0o777 == 0o600


def test_collision_existing_file_or_symlink_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.claw.core.compaction.transcript.time.strftime', lambda _format: 'fixed')
    monkeypatch.setattr('xdog.claw.core.compaction.transcript.uuid.uuid4', lambda: SimpleNamespace(hex='same'))
    first = archive_transcript([{'role': 'user', 'content': 'retained'}], tmp_path)
    with pytest.raises(FileExistsError):
        archive_transcript([{'role': 'user', 'content': 'must not replace'}], tmp_path)
    assert 'retained' in first.read_text()
    first.unlink()
    target = tmp_path / 'target'
    target.write_text('untouched')
    first.symlink_to(target)
    with pytest.raises(FileExistsError):
        archive_transcript([{'role': 'user', 'content': 'must not follow'}], tmp_path)
    assert target.read_text() == 'untouched'


def test_failed_archive_fsync_removes_its_partial_artifact(tmp_path, monkeypatch):
    def fail(_descriptor):
        raise OSError('offline disk failure')
    monkeypatch.setattr('xdog.claw.core.compaction.transcript.os.fsync', fail)
    with pytest.raises(OSError):
        archive_transcript([{'role': 'user', 'content': 'private'}], tmp_path)
    assert not list(tmp_path.glob('*.md'))
