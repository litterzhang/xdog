"""Task file writes are bounded, atomic, versioned and no-follow."""
from dataclasses import replace

import pytest
from xdog.skills.goal.codec import decode_task, encode_task
from xdog.skills.goal.files import TaskFiles
from xdog.skills.goal.models import Task


@pytest.mark.parametrize('kind', ['root', 'tasks', 'file', 'lock'])
def test_task_storage_refuses_symlinks_without_touching_target(tmp_path, kind):
    files = TaskFiles(tmp_path, 'one')
    outside = tmp_path / 'outside'
    outside.mkdir()
    files.initialize()
    target = {'root': files.root, 'tasks': files.tasks_dir,
              'file': files.tasks_dir / 'one.json', 'lock': files.root / '.task-write.lock'}[kind]
    if target.is_dir():
        target.rmdir() if target == files.tasks_dir else None
        if target == files.root:
            files.tasks_dir.rmdir()
            target.rmdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        files.write(Task('one', 'Do not escape'), expected_version=0)
    assert not list(outside.iterdir())


def test_task_update_preserves_original_on_conflict_and_invalid_format(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    task = files.write(Task('one', 'Original'), expected_version=0)
    path = files.tasks_dir / 'one.json'
    original = path.read_bytes()
    with pytest.raises(ValueError):
        files.write(replace(task, version=3), expected_version=2)
    assert path.read_bytes() == original
    path.write_text(encode_task(task).replace('xdog.goal_task.v2', 'unknown'))
    with pytest.raises(ValueError):
        files.list()
    path.write_text(encode_task(task).replace('"id": "one"', '"id": "different"'))
    with pytest.raises(ValueError, match='identity'):
        files.list()
    with pytest.raises(ValueError):
        decode_task('{"format":"xdog.goal_task.v2","format":"xdog.goal_task.v2"}')
