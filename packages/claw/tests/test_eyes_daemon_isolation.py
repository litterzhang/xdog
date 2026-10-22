"""Installed Eyes tools and child interpreters stay in test-owned daemon state."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path


async def _use_installed_observer_tool(workspace, data, endpoint, name):
    """Drive the installed tool through Claw capability ports, not private Eyes APIs."""
    from xdog.agent.capabilities.runtime import ApplicationPort
    from xdog.claw.acp.server import ClawAcpServer
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    def forbidden_model(*args, **kwargs):
        raise AssertionError('Tool registration/management must not start a model')

    app = Orchestrator(ClawConfig(data_dir=str(data), enabled_tools=('eyes',)),
                       model='test/dummy', stream_fn=forbidden_model, defer_inputs=True)
    app.register_group(Group(id='main', workspace=str(workspace), enabled_tools=('eyes',)))
    server = None
    try:
        assert app.application_capabilities.tools == ()  # Installed metadata is inert.
        server = await ClawAcpServer.start(app, endpoint)
        await app.application_capabilities.start(ApplicationPort())
        await app.refresh_application_capabilities()
        tool = next(tool for tool in app._runtimes['main'].tools if tool.name == 'eyes')
        assert any(tool is installed for installed in app.application_capabilities.tools)

        async def invoke(action, **params):
            result = await tool.execute('test-' + action, {'action': action, 'owner': 'caller:' + name,
                **params}, ctx={'group_id': 'main'})
            assert not result.is_error
            envelope = json.loads(''.join(part.text for part in result.content))
            assert envelope['exit_code'] == 0, envelope
            return json.loads(envelope['stdout'])

        from test_support.eyes_definition import save
        path = save(workspace, endpoint, 'group:main', name, {
            'type': 'scheduler', 'schedule': {'every_seconds': 3600}}, 'Inspect only when explicitly due')
        created = await invoke('create', definition_path=path)
        identifier = created['registration_id']
        assert any(row['registration_id'] == identifier for row in (await invoke('list'))['registrations'])
        status = await invoke('status', observer_id=identifier)
        assert status['registration_id'] == identifier and status['state'] in {'running', 'watching', 'baselining'}
        await invoke('cancel', observer_id=identifier)
        assert (await invoke('status', observer_id=identifier))['state'] == 'cancelled'
        assert app._runtimes['main']._active_session is None
        return identifier
    finally:
        if server is not None:
            await server.close()
        await app.stop()


async def test_installed_tool_and_child_interpreter_use_fixture_owned_daemon(tmp_path, isolated_eyes_daemon):
    directory = isolated_eyes_daemon
    assert Path(os.environ['XDG_STATE_HOME']) == directory.parent / 'state'
    assert Path(os.environ['XDOG_EYES_RUNTIME_DIR']) == directory
    identifier = await _use_installed_observer_tool(
        tmp_path, tmp_path / 'parent-data', directory.parent / 'parent.sock', 'parent-observer')
    parent = json.loads((directory / 'owner.json').read_text())
    # The child drives the same installed AgentTool interface, not a public Eyes
    # client class; the registered tool owns its public CLI subprocess calls.
    code = 'import asyncio,json,sys\nfrom pathlib import Path\n' + inspect.getsource(_use_installed_observer_tool) + '''
async def main():
    identifier=await _use_installed_observer_tool(
        Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),'child-observer')
    owner=json.loads((Path(sys.argv[4])/'owner.json').read_text())
    print(json.dumps({'observer_id':identifier,'generation':owner['generation'],'pid':owner['pid']}))
asyncio.run(main())
'''
    child = await asyncio.create_subprocess_exec(
        sys.executable, '-c', code, str(tmp_path), str(tmp_path / 'child-data'),
        str(directory.parent / 'child.sock'), str(directory),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(child.communicate(), 20)
    assert child.returncode == 0, stderr.decode()
    observed = json.loads(stdout)
    assert observed['generation'] == parent['generation'] and observed['pid'] == parent['pid']
    assert observed['observer_id'] != identifier
    # Host detachment is not whole-daemon shutdown; only the isolation fixture
    # retires this test-owned instance after verifying both independent owners.
    assert directory.joinpath('eyes.sock').exists()
