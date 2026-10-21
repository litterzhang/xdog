"""CLI shares TaskFiles CAS and never infers owner paths or starts a runtime."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
from xdog.skills.goal.files import TaskFiles
from xdog.skills.goal.models import Task
from xdog.skills.goal.task_cli import main


def run_cli(workspace, *arguments, input=None):
    return subprocess.run([sys.executable, '-m', 'xdog.skills.goal.task_cli',
                           '--workspace', str(workspace), '--goal-id', 'one', *arguments],
                          input=input, text=True, capture_output=True, check=False)


def test_module_update_lists_and_preserves_fields(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.write(Task('task', 'Title', description='Keep me', worker='worker-a'), expected_version=0)
    result = run_cli(tmp_path, 'update', '--task-id', 'task', '--expected-version', '1',
                     '--data', '{"status":"completed","evidence":["pytest"],"depends_on":[]}')
    assert result.returncode == 0, result.stderr
    task = json.loads(result.stdout)
    assert task['version'] == 2 and task['status'] == 'completed'
    assert task['worker'] == 'worker-a' and task['description'] == 'Keep me'
    assert json.loads(run_cli(tmp_path, 'list').stdout) == [task]
    original = (files.tasks_dir / 'task.json').read_bytes()
    result = run_cli(tmp_path, 'update', '--task-id', 'task', '--expected-version', '1',
                     '--data', '{"summary":"stale"}')
    assert result.returncode == 1 and 'conflict' in result.stderr
    assert (files.tasks_dir / 'task.json').read_bytes() == original


@pytest.mark.parametrize('changes', [
    '{"id":"different"}', '{"version":9}', '{"status":"invalid"}', '{"evidence":"string"}',
    '{"evidence":[4]}', '{"unknown":1}', '{"summary":"a","summary":"b"}', '[]', '{',
])
def test_invalid_update_fails_without_mutation(tmp_path, changes):
    files = TaskFiles(tmp_path, 'one')
    files.write(Task('task', 'Title'), expected_version=0)
    original = (files.tasks_dir / 'task.json').read_bytes()
    result = run_cli(tmp_path, 'update', '--task-id', 'task', '--expected-version', '1', '--data', changes)
    assert result.returncode == 1 and 'error' in json.loads(result.stderr)
    assert (files.tasks_dir / 'task.json').read_bytes() == original


def test_update_cli_stdin(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.write(Task('task', 'Title'), expected_version=0)
    result = run_cli(tmp_path, 'update', '--task-id', 'task', '--expected-version', '1',
                     input=json.dumps({'summary': 'Checked artifacts', 'evidence': ['pytest']}))
    assert result.returncode == 0, result.stderr
    saved = json.loads(result.stdout)
    assert saved['summary'] == 'Checked artifacts' and saved['version'] == 2
    assert saved['evidence'] == ['pytest']
    assert json.loads(run_cli(tmp_path, 'list').stdout) == [saved]


@pytest.mark.parametrize('command', ['report', 'latest-report'])
def test_report_commands_are_not_exposed_and_do_not_touch_workspace(tmp_path, command):
    result = run_cli(tmp_path, command)
    assert result.returncode == 2
    assert 'invalid choice' in result.stderr
    assert not result.stdout
    assert not (tmp_path / '.xdog').exists()


def test_help_exposes_only_task_commands(tmp_path):
    result = run_cli(tmp_path, '--help')
    assert result.returncode == 0
    assert '{list,update}' in result.stdout
    assert 'report' not in result.stdout


def test_binding_required_and_confined(tmp_path):
    with pytest.raises(SystemExit):
        main(['list'])
    assert run_cli('relative', 'list').returncode == 1
    assert run_cli(tmp_path / '..' / 'escape', 'list').returncode == 1
    with pytest.raises(ValueError):
        TaskFiles(tmp_path, '../escape')
    with pytest.raises(ValueError):
        TaskFiles(tmp_path, '/absolute')
    assert not (tmp_path / '.xdog').exists()


def test_stdin_bounded_and_missing_task_not_created(tmp_path, monkeypatch, capsys):
    files = TaskFiles(tmp_path, 'one')
    task = files.write(Task('task', 'Title'), expected_version=0)
    monkeypatch.setattr(sys, 'stdin', StringIO('x' * 262145))
    assert main(['--workspace', str(tmp_path), '--goal-id', 'one', 'update',
                 '--task-id', 'task', '--expected-version', '1']) == 1
    assert 'capacity' in capsys.readouterr().err
    assert files.list() == (task,)
    result = run_cli(tmp_path, 'update', '--task-id', 'missing', '--expected-version', '1', '--data', '{}')
    assert result.returncode == 1
    assert files.list() == (task,)


def test_cli_parallel_task_writers_share_lock(tmp_path):
    files = TaskFiles(tmp_path, 'one')
    files.write(Task('task', 'Title'), expected_version=0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: run_cli(tmp_path, 'update', '--task-id', 'task',
                            '--expected-version', '1', '--data', json.dumps({'summary': str(i)})), range(8)))
    assert sum(result.returncode == 0 for result in results) == 1
    assert files.list()[0].version == 2
