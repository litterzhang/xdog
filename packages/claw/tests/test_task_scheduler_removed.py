"""The retired task scheduler is absent, not a migration input for Eyes."""
from __future__ import annotations

import builtins
import importlib.util
import inspect
import io
import os
import tempfile
from dataclasses import fields
from pathlib import Path

import pytest
import yaml
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core import planning, types
from xdog.claw.core.observers.journal import ObserverJournal
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.observers.models import ObserverRecord
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools.registry import create_tools, registered_names


def test_retired_modules_types_and_tool_are_absent():
    for module in (
        'xdog.claw.core.planning.task_scheduler',
        'xdog.claw.core.tools.tool_task',
        'xdog.claw.core.observers.migration',
    ):
        assert importlib.util.find_spec(module) is None, module
    assert not hasattr(planning, 'TaskScheduler')
    for name in ('TaskSchedule', 'ScheduledTask'):
        assert not hasattr(types, name), name
    assert 'task' not in registered_names()
    with pytest.raises(ValueError, match='Unknown tools'):
        create_tools(('task',))
    assert {'eyes', 'goal'} <= registered_names()
    assert {tool.name for tool in create_tools(('eyes', 'goal'))} == {'goal', 'eyes'}
    # GoalTask/TaskStatus are goal planning, not the retired scheduled-task model.
    assert {'id', 'description', 'status'} <= {field.name for field in fields(types.GoalTask)}
    assert types.TaskStatus.PENDING.value == 'pending'


def test_no_legacy_configuration_or_management_api(tmp_path):
    assert 'tasks_file' not in {field.name for field in fields(ClawConfig)}
    with pytest.raises(TypeError, match='tasks_file'):
        ClawConfig(tasks_file=str(tmp_path / 'scheduled_tasks.json'))
    assert not hasattr(Orchestrator, 'legacy_tasks_file')
    assert tuple(inspect.signature(ManagedObservers.start).parameters) == ('self',)
    assert 'legacy' not in {field.name for field in fields(ObserverRecord)}
    assert 'legacy' not in inspect.signature(ObserverJournal.create).parameters
    assert not hasattr(ObserverJournal, 'migration_marker')
    assert not hasattr(ObserverJournal, 'import_records')
    path = tmp_path / 'claw.yaml'
    path.write_text(yaml.safe_dump({'tasks_file': str(tmp_path / 'scheduled_tasks.json'), 'eyes_enabled': True}))
    config = load_config(path)
    assert config.eyes_enabled and not hasattr(config, 'tasks_file')
    save_config(config, path)
    assert 'tasks_file' not in yaml.safe_load(path.read_text())


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('contents', [
    b'{broken legacy file',
    b'{"tasks":[{"id":"retired","group_id":"main","prompt":"must not execute",'
    b'"schedule":{"run_at":1},"enabled":true,"status":"pending","next_run":1}]}',
])
async def test_gateway_never_reads_imports_or_modifies_old_scheduled_tasks(tmp_path, monkeypatch, enabled, contents):
    calls = []
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options',
                        lambda _: ('test/no-call', lambda *args: calls.append(args)))
    data = tmp_path / 'data'
    data.mkdir()
    old_file = data / 'scheduled_tasks.json'
    old_file.write_bytes(contents)
    before = old_file.stat()
    attempted_opens = []

    def guard(original):
        def open_file(path, *args, **kwargs):
            if not isinstance(path, int) and Path(os.fsdecode(path)).absolute() == old_file:
                attempted_opens.append(path)
                raise AssertionError('Retired scheduled_tasks.json must never be opened')
            return original(path, *args, **kwargs)
        return open_file

    # Exercise actual gateway startup/restart, not only ManagedObservers.start().
    # Guard all standard Python file-open entry points, including descriptor I/O.
    with tempfile.TemporaryDirectory(prefix='eyes-retired-') as short, monkeypatch.context() as guarded:
        guarded.setattr(builtins, 'open', guard(builtins.open))
        guarded.setattr(io, 'open', guard(io.open))
        guarded.setattr(os, 'open', guard(os.open))
        config = ClawConfig(data_dir=str(data), socket_path=str(Path(short) / 'gateway.sock'),
                            pid_file=str(Path(short) / 'gateway.pid'), eyes_enabled=enabled,
                            acp_socket_path=str(Path(short) / 'acp.sock') if enabled else '',
                            enabled_tools=('eyes', 'goal'), groups=(GroupDef(id='main', workspace=str(tmp_path)),))
        for _ in range(2):
            gateway = GatewayServer(config)
            try:
                await gateway.start()
                assert gateway._goal_task is not None and not gateway._goal_task.done()
                assert not hasattr(gateway._orchestrator, 'scheduler')
                if enabled:
                    service = gateway._observers
                    assert service is not None
                    assert await service.list('main') == ()
                    assert not service._tasks and not service._handlers
                else:
                    assert gateway._observers is None and gateway._acp_server is None
                await gateway._orchestrator.tick()
                assert not calls
            finally:
                await gateway.stop()
    assert attempted_opens == []
    assert old_file.read_bytes() == contents
    after = old_file.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert not list(data.rglob('*backup*'))
