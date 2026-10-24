"""The CI runner must not let package-local configs bypass root async error gates."""
from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'test.py'


def test_package_runner_explicitly_selects_root_configuration(monkeypatch):
    run_path = runpy.run_path(str(SCRIPT))['run_path']
    commands = []
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert run_path('packages/coding/tests') == ('packages/coding/tests', 0)
    command, options = commands[0]
    assert command[command.index('-c') + 1] == str(SCRIPT.parents[1] / 'pyproject.toml')
    assert command[command.index('-n') + 1] == '2'
    assert options['cwd'] == SCRIPT.parents[1]
    assert 'stdout' not in options  # Do not hide live test/worker diagnostics.
    assert options['stderr'] == subprocess.STDOUT
    assert '-q' in command
    assert '--timeout=120' in command and '--timeout-method=thread' in command


def test_nested_config_cannot_turn_an_unhandled_worker_exception_into_success(tmp_path, monkeypatch, capfd):
    (tmp_path / 'pyproject.toml').write_text('''[tool.pytest.ini_options]
filterwarnings = ["error::pytest.PytestUnhandledThreadExceptionWarning"]
''')
    nested = tmp_path / 'nested'
    tests = nested / 'tests'
    tests.mkdir(parents=True)
    (nested / 'pyproject.toml').write_text('''[tool.pytest.ini_options]
filterwarnings = ["ignore::pytest.PytestUnhandledThreadExceptionWarning"]
''')
    (tests / 'test_worker.py').write_text('''import threading

def test_worker():
    def fail():
        raise RuntimeError("owned worker did not settle")
    worker = threading.Thread(target=fail)
    worker.start()
    worker.join()
''')
    # Confirm the old invocation silently selected the permissive nested config.
    before = subprocess.run([sys.executable, '-m', 'pytest', 'nested/tests', '-q', '-n', '0'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert before.returncode == 0, before.stdout + before.stderr
    run_path = runpy.run_path(str(SCRIPT))['run_path']
    monkeypatch.setitem(run_path.__globals__, 'ROOT', tmp_path)
    path, code = run_path('nested/tests')
    output = capfd.readouterr().out
    assert path == 'nested/tests' and code != 0, output
    assert 'PytestUnhandledThreadExceptionWarning' in output
    assert 'owned worker did not settle' in output


@pytest.mark.parametrize("cpus,expected", [(1, 1), (2, 1), (4, 2), (8, 4), (64, 4)])
def test_default_jobs_respects_cpu_affinity(monkeypatch, cpus, expected):
    default_jobs = runpy.run_path(str(SCRIPT))["default_jobs"]
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(cpus)), raising=False)
    assert default_jobs() == expected


def test_default_jobs_falls_back_without_affinity_support(monkeypatch):
    default_jobs = runpy.run_path(str(SCRIPT))["default_jobs"]
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert default_jobs() == 1


def test_hung_test_fails_with_its_stack_instead_of_stranding_ci(tmp_path):
    (tmp_path / "test_hung.py").write_text(
        "import threading\ndef test_hung():\n    threading.Event().wait()\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_hung.py", "-q", "-n", "0",
         "--timeout=1", "--timeout-method=thread"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "Timeout" in result.stdout + result.stderr
    assert "test_hung" in result.stdout + result.stderr
