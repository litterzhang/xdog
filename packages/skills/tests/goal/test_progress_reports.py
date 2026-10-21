"""A single bounded Markdown document is the report authority, not a DB mirror."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from xdog.skills.goal import files as storage
from xdog.skills.goal.files import TaskFiles


def report(expected=0, progress='baseline', **changes):
    return dict(expected_version=expected, progress=progress, summary='Assessment',
                completed=[], evidence=['pytest passed'], blockers=[], next_steps=['Inspect artifacts']) | changes


def append_at_version(workspace, expected):
    try:
        TaskFiles(Path(workspace), 'one').report(report(expected, 'unchanged'))
        return 'saved'
    except ValueError:
        return 'conflict'


def test_report_history_restart_and_single_authority(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    assert files.progress_path == files.root / 'progress.md'
    assert files.latest_report() is None
    first = files.report(report(summary='Initial <state>\n```json goal-report-v1'))
    assert first['version'] == 1
    assert 'expected_version' not in first
    assert files.latest_report() == first
    second = files.report(report(1, 'advanced', completed=['Implemented helper']))
    third = TaskFiles(tmp_path, 'one').report(report(2, 'regressed', blockers=['Test failure']))
    assert TaskFiles(tmp_path, 'one').latest_report() == third
    content = files.progress_path.read_text()
    assert '## Report 1: baseline' in content
    assert '## Report 2: advanced' in content
    assert '## Report 3: regressed' in content
    assert 'Implemented helper' in content
    assert second['version'] == 2
    assert {p.name for p in files.root.iterdir()} == {'progress.md', '.progress-write.lock'}
    first['evidence'].append('external mutation')
    assert 'external mutation' not in content


@pytest.mark.parametrize('changes', [
    {'expected_version': True}, {'expected_version': -1}, {'expected_version': 1.0},
    {'expected_version': 2**53}, {'progress': 'advanced'}, {'progress': []},
    {'summary': ''}, {'summary': None}, {'summary': 'x\x00'},
    {'completed': 'not an array'}, {'evidence': [3]}, {'blockers': ['']},
    {'next_steps': ['x'] * 1025}, {'extra': 'no'},
])
def test_invalid_report_does_not_create_document(tmp_path, changes):
    files = TaskFiles(tmp_path, 'one')
    with pytest.raises(ValueError):
        files.report(report(**changes))
    assert not files.progress_path.exists()


def test_cas_baseline_and_no_semantic_judgment(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.report(report())
    original = files.progress_path.read_bytes()
    for data in (report(), report(2, 'unchanged'), report(1, 'baseline')):
        with pytest.raises(ValueError):
            files.report(data)
        assert files.progress_path.read_bytes() == original
    # Neither a threshold nor evidence interpretation belongs to storage.
    for expected in range(1, 8):
        files.report(report(expected, 'unchanged', evidence=[], blockers=[]))
    files.report(report(8, 'advanced', evidence=[], completed=[]))
    assert files.latest_report()['version'] == 9


@pytest.mark.parametrize('damage', ['empty', 'truncated', 'human', 'duplicate', 'foreign', 'sequence', 'garbage'])
def test_recovery_rejects_corruption_and_preserves_original(tmp_path, damage):
    files = TaskFiles(tmp_path, 'one')
    files.report(report())
    payload = files.progress_path.read_text()
    damaged = {
        'empty': '', 'truncated': payload[:-5], 'human': payload.replace('### Evidence', '### Proof'),
        'duplicate': payload.replace('"version": 1', '"version": 1, "version": 1'),
        'foreign': payload.replace('Goal: `one`', 'Goal: `other`'),
        'sequence': payload.replace('"version": 1', '"version": 2'),
        'garbage': payload + 'trailing garbage',
    }[damage]
    files.progress_path.write_text(damaged)
    with pytest.raises(ValueError):
        files.latest_report()
    with pytest.raises(ValueError):
        files.report(report(1, 'advanced'))
    assert files.progress_path.read_text() == damaged


@pytest.mark.parametrize('target', ['workspace', 'root', 'file', 'lock'])
def test_no_follow_boundaries(tmp_path, target):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    files = TaskFiles(workspace, 'one')
    files.initialize()
    outside = tmp_path / 'outside'
    outside.mkdir()
    victim = outside / 'victim'
    victim.write_text('untouched')
    if target == 'workspace':
        workspace.rename(tmp_path / 'old')
        workspace.symlink_to(outside, target_is_directory=True)
    elif target == 'root':
        files.tasks_dir.rmdir()
        files.root.rmdir()
        files.root.symlink_to(outside, target_is_directory=True)
    else:
        (files.progress_path if target == 'file' else files.root / '.progress-write.lock').symlink_to(victim)
    with pytest.raises((ValueError, OSError)):
        files.report(report())
    if target != 'lock':
        with pytest.raises((ValueError, OSError)):
            files.latest_report()
    assert victim.read_text() == 'untouched'
    assert {p.name for p in outside.iterdir()} == {'victim'}


def test_bounded_read_write_and_atomic_failure(tmp_path, monkeypatch):
    files = TaskFiles(tmp_path, 'one')
    files.report(report())
    original = files.progress_path.read_bytes()
    with pytest.raises(ValueError, match='capacity'):
        files.report(report(1, 'advanced', completed=['x' * 60000] * 5))
    assert files.progress_path.read_bytes() == original

    def fail(*args, **kwargs):
        raise OSError('simulated rename failure')

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, 'replace', fail)
        with pytest.raises(OSError, match='rename'):
            files.report(report(1, 'advanced'))
    assert files.progress_path.read_bytes() == original
    assert not list(files.root.glob('.write-*'))
    files.progress_path.write_bytes(b'x' * 262145)
    with pytest.raises(ValueError, match='bounded'):
        files.latest_report()


def test_real_process_lock_and_cas(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.report(report())
    with ProcessPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(append_at_version, [str(tmp_path)] * 8, [1] * 8))
    assert outcomes.count('saved') == 1
    assert outcomes.count('conflict') == 7
    assert files.latest_report()['version'] == 2


@pytest.mark.parametrize('kind', ['report', 'lock'])
def test_nonregular_files_fail_without_blocking(tmp_path, kind):
    import os

    files = TaskFiles(tmp_path, 'one')
    files.initialize()
    path = files.progress_path if kind == 'report' else files.root / '.progress-write.lock'
    os.mkfifo(path)
    with pytest.raises(ValueError, match='regular'):
        files.report(report())
    if kind == 'report':
        with pytest.raises(ValueError, match='regular'):
            files.latest_report()


def test_history_capacity_never_discards_previous_reports(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.report(report(summary='初始评估', evidence=['工件检查']))
    expected = 1
    while True:
        original = files.progress_path.read_bytes()
        try:
            files.report(report(expected, 'unchanged', summary='x' * 20000))
        except ValueError as exc:
            assert 'capacity' in str(exc)
            break
        expected += 1
        assert expected < 20
    assert expected > 1
    assert files.progress_path.read_bytes() == original
    assert files.latest_report()['version'] == expected
    assert '初始评估' in files.progress_path.read_text()


def test_invalid_utf8_and_invalid_saved_schema_refuse_recovery(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.report(report())
    original = files.progress_path.read_bytes()
    for content in [b'\xff', original.replace(b'"progress": "baseline"', b'"progress": "advanced"'),
                    original.replace(b'"version": 1', b'"version": true')]:
        files.progress_path.write_bytes(content)
        with pytest.raises(ValueError):
            files.latest_report()
        with pytest.raises(ValueError):
            files.report(report(1, 'unchanged'))
        assert files.progress_path.read_bytes() == content
