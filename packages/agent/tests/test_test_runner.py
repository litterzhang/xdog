"""The CI runner must not let package-local configs bypass root async error gates."""
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'test.py'


def test_package_runner_explicitly_selects_root_configuration(monkeypatch):
    run_path = runpy.run_path(str(SCRIPT))['run_path']
    commands = []
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='passed')
    monkeypatch.setattr(subprocess, 'run', run)
    assert run_path('packages/coding/tests') == ('packages/coding/tests', 0, 'passed')
    command, options = commands[0]
    assert command[command.index('-c') + 1] == str(SCRIPT.parents[1] / 'pyproject.toml')
    assert command[command.index('-n') + 1] == '2'
    assert options['cwd'] == SCRIPT.parents[1]


def test_nested_config_cannot_turn_an_unhandled_worker_exception_into_success(tmp_path, monkeypatch):
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
    path, code, output = run_path('nested/tests')
    assert path == 'nested/tests' and code != 0, output
    assert 'PytestUnhandledThreadExceptionWarning' in output
    assert 'owned worker did not settle' in output
