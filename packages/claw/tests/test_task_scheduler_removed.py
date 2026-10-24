"""The retired task scheduler is absent, not a migration input for Eyes."""
from __future__ import annotations

import asyncio
import builtins
import importlib.util
import io
import json
import os
import tempfile
from dataclasses import fields
from pathlib import Path

import pytest
import yaml
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core import planning, types
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools import new_tool_repo


def test_retired_modules_types_and_tool_are_absent():
    for module in (
        'xdog.claw.core.planning.task_scheduler',
        'xdog.claw.core.tools.tool_task',
        'xdog.claw.core.observers',
        'xdog.claw.core.tools.tool_eyes',
    ):
        assert importlib.util.find_spec(module) is None, module
    assert not hasattr(planning, 'TaskScheduler')
    for name in ('TaskSchedule', 'ScheduledTask'):
        assert not hasattr(types, name), name
    assert 'task' not in new_tool_repo().names()
    with pytest.raises(ValueError, match='Unknown tools'):
        new_tool_repo(('task',))
    assert 'eyes' not in new_tool_repo().names() and 'goal' not in new_tool_repo().names()
    with pytest.raises(ValueError, match='Unknown tools'):
        new_tool_repo(('eyes',))
    with pytest.raises(ValueError, match='Unknown tools'):
        new_tool_repo(('goal',))


def test_no_legacy_configuration_or_management_api(tmp_path):
    assert 'tasks_file' not in {field.name for field in fields(ClawConfig)}
    with pytest.raises(TypeError, match='tasks_file'):
        ClawConfig(tasks_file=str(tmp_path / 'scheduled_tasks.json'))
    assert not hasattr(Orchestrator, 'legacy_tasks_file')
    path = tmp_path / 'claw.yaml'
    path.write_text(yaml.safe_dump({'tasks_file': str(tmp_path / 'scheduled_tasks.json'), 'eyes_enabled': True}))
    config = load_config(path)
    assert not hasattr(config, 'eyes_enabled') and not hasattr(config, 'tasks_file')
    save_config(config, path)
    assert 'tasks_file' not in yaml.safe_load(path.read_text())


@pytest.mark.parametrize('installed', [False, True])
@pytest.mark.parametrize('contents', [
    b'{broken legacy file',
    b'{"tasks":[{"id":"retired","group_id":"main","prompt":"must not execute",'
    b'"schedule":{"run_at":1},"enabled":true,"status":"pending","next_run":1}]}',
])
async def test_gateway_never_reads_imports_or_modifies_old_scheduled_tasks(tmp_path, monkeypatch, installed, contents):
    calls = []
    from xdog.agent.capabilities.runtime import discover
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: tuple(p for p in discover(scope) if installed or p.name != 'eyes'))
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

    # Exercise actual gateway startup/restart, not only tool registration.
    # Guard all standard Python file-open entry points, including descriptor I/O.
    with tempfile.TemporaryDirectory(prefix='eyes-retired-') as short, monkeypatch.context() as guarded:
        guarded.setattr(builtins, 'open', guard(builtins.open))
        guarded.setattr(io, 'open', guard(io.open))
        guarded.setattr(os, 'open', guard(os.open))
        config = ClawConfig(data_dir=str(data), socket_path=str(Path(short) / 'gateway.sock'),
                            pid_file=str(Path(short) / 'gateway.pid'),
                            acp_socket_path=str(Path(short) / 'acp.sock') if installed else '',
                            enabled_tools=('eyes',) if installed else (),
                            groups=(GroupDef(id='main', workspace=str(tmp_path)),))
        for _ in range(2):
            gateway = GatewayServer(config)
            try:
                await gateway.start()
                assert await gateway._orchestrator._runtimes['main'].capabilities.get('goal').service.list() == ()
                assert not hasattr(gateway._orchestrator, 'scheduler')
                if installed:
                    service = gateway._application_capabilities.get('eyes')
                    assert service is not None
                    tool = next(tool for tool in gateway._application_capabilities.tools if tool.name == 'eyes')
                    result = await tool.execute('list-empty', {'action': 'list', 'owner': 'gateway-empty'})
                    assert not result.is_error
                    envelope = json.loads(''.join(part.text for part in result.content))
                    assert envelope['exit_code'] == 0, envelope
                    assert json.loads(envelope['stdout'])['registrations'] == []
                    assert not hasattr(service, "_tasks") and not hasattr(service, "_handlers")
                else:
                    assert gateway._application_capabilities.get('eyes') is None
                    # Default Goal requests the same generic private ACP, even without Eyes.
                    assert gateway._acp_server is not None
                await asyncio.sleep(0)
                assert not calls
            finally:
                await gateway.stop()
    assert attempted_opens == []
    assert old_file.read_bytes() == contents
    after = old_file.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert not list(data.rglob('*backup*'))
