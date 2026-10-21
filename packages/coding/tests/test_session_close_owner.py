"""Actual worker cleanup must settle before the public session owner releases."""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest
from xdog.agent.mcp import McpManager
from xdog.coding.core.sessions.ownership import SessionLease, lease_directory

from packages.coding.tests.extension_session_helpers import session as extension_session


@pytest.mark.parametrize('mode', ['runtime', 'sdk'])
@pytest.mark.parametrize('failure_point', ['children', 'extension'])
async def test_cleanup_error_cannot_release_writer_before_remaining_real_worker(
        mode, failure_point, tmp_path, monkeypatch):
    session, calls = extension_session(tmp_path, extra=('contextual',))
    session.mcp.close()
    peer = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'
    session.mcp = McpManager({'fixture': {'command': sys.executable,
        'args': [str(peer), 'modern', str(tmp_path / 'mcp-requests')]}}, tmp_path)
    if mode == 'runtime':
        runtime = session.enable_runtime(bound=False)
        runtime.start(own_thread=True, setup_pending=True)
        controller = runtime.controller
    else:
        controller, _ = await session._acquire_owner()
        runtime = None
    await asyncio.wrap_future(controller.control(session.initialize_extensions, name='initialize'))
    await asyncio.wrap_future(controller.control(lambda: session.manage_mcp('connect', 'fixture'), name='connect-mcp'))
    extension, supervisor = session.extensions, session.children.supervisor
    child_close = supervisor.aclose
    extension_client = extension._manager._clients['contextual']
    mcp_client = session.mcp._clients['fixture']
    worker = extension._manager._thread if failure_point == 'children' else session.mcp._thread
    entered, release = threading.Event(), threading.Event()
    failures = []
    failure = OSError('actual ' + failure_point + ' cleanup failed after reaping')

    async def fail_after_child():
        await child_close()
        failures.append(True)
        raise failure

    actual_extension_close = extension_client.close
    async def fail_after_extension():
        await actual_extension_close()
        failures.append(True)
        raise failure

    target = extension_client if failure_point == 'children' else mcp_client
    actual_tail_close = target.close
    async def delayed_tail_close():
        entered.set()
        assert await asyncio.to_thread(release.wait, 8)
        await actual_tail_close()

    if failure_point == 'children':
        monkeypatch.setattr(supervisor, 'aclose', fail_after_child)
    else:
        monkeypatch.setattr(extension_client, 'close', fail_after_extension)
    monkeypatch.setattr(target, 'close', delayed_tail_close)

    async def begin_close():
        if runtime is not None:
            await asyncio.wrap_future(runtime.close())
        else:
            receipt = session.dispose()
            # Current compatibility path returned None, but both paths must
            # still own the actual controller's close before lease release.
            await asyncio.wrap_future(receipt if receipt is not None else controller.close())

    closing = asyncio.create_task(begin_close())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert not closing.done(), 'Failed close completed before the remaining worker settled'
        assert worker.is_alive()
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        release.set()
        with pytest.raises(OSError) as error:
            await closing
        assert error.value is failure
        assert not worker.is_alive() and session._lease is None and not calls
        assert extension_client.process is None and mcp_client.process is None
        for _ in range(2):
            with pytest.raises(OSError) as repeated:
                await session.aclose()
            assert repeated.value is failure
        assert failures == [True]
        lease = SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        lease.close()
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)
        await asyncio.gather(extension.aclose(), child_close(), session.mcp.aclose(), return_exceptions=True)
        if runtime is not None:
            await asyncio.gather(asyncio.wrap_future(runtime.close()), return_exceptions=True)
        session.dispose()


async def test_public_close_repeated_cancellation_joins_worker_and_is_idempotent(tmp_path, monkeypatch):
    session, calls = extension_session(tmp_path, extra=('contextual',))
    runtime = session.enable_runtime(bound=False)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller.control(session.initialize_extensions, name='initialize'))
    client = session.extensions._manager._clients['contextual']
    actual = client.close
    entered, release = threading.Event(), threading.Event()
    async def delayed():
        entered.set()
        assert await asyncio.to_thread(release.wait, 8)
        await actual()
    monkeypatch.setattr(client, 'close', delayed)
    closing = None
    try:
        closing = asyncio.create_task(session.aclose())
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and client.process is not None
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await session.aclose()
        assert session.agent.closed and client.process is None and session._lease is None and not calls
        with pytest.raises(RuntimeError, match='clos'):
            await session.send_message('cannot run after close')
        with pytest.raises(RuntimeError, match='clos'):
            session.set_model('other')
        with pytest.raises(RuntimeError, match='clos'):
            session.enable_runtime(bound=True)
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await session.extensions.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_public_close_before_any_owner_does_not_need_reopenable_input_history(tmp_path):
    session, calls = extension_session(tmp_path, extra=('contextual',))
    await session.initialize_extensions()
    worker = session.extensions._manager._thread
    session.session_manager.input_history_path(session.session_id).write_text('{invalid-ledger')
    try:
        await session.aclose()
        await session.aclose()
        assert session.agent.closed and not worker.is_alive() and not calls
        assert session._sdk_controller is None and session.runtime is None
    finally:
        await session.extensions.aclose()
        await session.children.supervisor.aclose()
        session.dispose()


async def test_goal_display_cleanup_error_still_closes_actual_domain_store(tmp_path, monkeypatch):
    from xdog.skills.goal.store import GoalStore

    from packages.coding.tests.test_child_agents import create

    session, _ = create(tmp_path)
    await session.send_message('initialize installed capability')
    controller = session._sdk_controller
    host = session.capabilities.get("goal")
    assert host is not None
    # Bind the actual shared display to the host on its actual owner loop.
    from xdog.skills.goal.ui_binding import GoalDisplay
    async def display():
        host._display = GoalDisplay(host.service, session.extensions.views, valid=lambda: True,
                                    notify=lambda *args: None)
    await asyncio.wrap_future(controller.control(display, name='display-binding'))
    actual_display_close = host._display.aclose
    async def fail_after_display_close():
        await actual_display_close()
        raise OSError('display cleanup failed')
    monkeypatch.setattr(host._display, 'aclose', fail_after_display_close)
    try:
        with pytest.raises(OSError, match='display cleanup failed'):
            await session.aclose()
        # This is actual lease reacquisition, not a fake store-close assertion.
        reopened = await GoalStore.open(host.service.store.path, host.service.store.owner)
        await reopened.close()
        assert session._lease is None and session.capabilities.get("goal") is None
    finally:
        await host.service.close()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize('entry', ['initial-input-failure', 'print'])
def test_cli_awaits_session_cleanup_before_returning_or_exiting(tmp_path, monkeypatch, entry):
    from click.testing import CliRunner
    from xdog.ai.types import Model
    from xdog.coding import main
    from xdog.coding.cli.args import cli
    from xdog.coding.core import sdk

    from packages.coding.tests.test_sdk_session_state import _Provider

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/model', context_window=100_000)))
    construct = sdk.create_agent_session
    owners = []
    def capture(options):
        result = construct(options)
        owners.append(result.session)
        return result
    monkeypatch.setattr(sdk, 'create_agent_session', capture)
    def initial(*args, **kwargs):
        if entry == 'initial-input-failure':
            raise OSError('initial image/input unavailable')
        return 'metadata only'
    async def print_without_model(*args, **kwargs):
        return 0
    monkeypatch.setattr(main, '_build_initial_message', initial)
    monkeypatch.setattr('xdog.coding.modes.print_mode.run_print_mode', print_without_model)
    try:
        result = CliRunner().invoke(cli, ['--print', '--prompt', 'metadata only', '--working-dir', str(tmp_path)])
        assert result.exit_code == (1 if entry == 'initial-input-failure' else 0), result.exception
        assert len(owners) == 1
        assert owners[0]._lease is None and owners[0].agent.closed
    finally:
        # Test utility cleanup also handles the intentionally broken RED path.
        async def cleanup(owner):
            await owner.agent.aclose()
            await owner.children.supervisor.aclose()
            await owner.extensions.aclose()
            if owner.mcp is not None:
                await owner.mcp.aclose()
            owner._dispose_resources()
        for owner in owners:
            asyncio.run(cleanup(owner))


async def test_navigation_joins_retired_extension_before_releasing_original_writer(tmp_path, monkeypatch):
    session, calls = extension_session(tmp_path, extra=('contextual',))
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    controller = runtime.controller
    await asyncio.wrap_future(controller.control(session.initialize_extensions, name='initialize'))
    original_id, lease = session.session_id, session._lease
    extension = session.extensions
    client = extension._manager._clients['contextual']
    actual_close = client.close
    entered, release = threading.Event(), threading.Event()
    async def gated():
        entered.set()
        assert await asyncio.to_thread(release.wait, 8)
        await actual_close()
    monkeypatch.setattr(client, 'close', gated)
    rebinding = threading.Event()
    settle_goals = session._settle_retired_capabilities
    async def observe_rebind():
        await settle_goals()
        rebinding.set()
    async def no_endpoint():
        pass
    monkeypatch.setattr(session, '_settle_retired_capabilities', observe_rebind)
    monkeypatch.setattr(runtime, 'open_endpoint', no_endpoint)
    async def navigate():
        return await asyncio.to_thread(session.new_session)
    navigation = controller.control(navigate, name='new')
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert await asyncio.to_thread(rebinding.wait, 3)
        await asyncio.sleep(.1)
        assert not navigation.done(), 'Navigation released the old callback owner before worker exit'
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease.directory, original_id)
        release.set()
        new_id = await asyncio.wrap_future(navigation)
        assert new_id != original_id and not extension._manager._thread.is_alive() and not calls
        reopened = SessionLease.acquire(lease.directory, original_id)
        reopened.close()
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(navigation), return_exceptions=True)
        await session.aclose()
        await extension.aclose()


async def test_control_finalizer_failure_settles_once_and_reaches_caller(coding_session):
    runtime = coding_session.enable_runtime(bound=True)
    runtime.start(setup_pending=True)
    calls = []
    def fail_once():
        calls.append(True)
        raise OSError('finalizer failed')
    runtime.controller.on_control_settled = fail_once
    async def control():
        return 'done'
    receipt = runtime.controller.control(control, name='control')
    try:
        # The producer receipt must not hang after its finalizer throws.
        await asyncio.sleep(.05)
        assert receipt.done(), 'Finalizer failure abandoned the control receipt'
        with pytest.raises(OSError, match='finalizer failed'):
            receipt.result()
        assert calls == [True]
    finally:
        runtime.controller.on_control_settled = coding_session._release_retired_leases
        await coding_session.aclose()


async def test_local_endpoint_close_attempts_all_steps_after_one_transport_error(tmp_path):
    from types import SimpleNamespace

    from xdog.coding.acp.server import LocalServer
    calls = []
    class Writer:
        def __init__(self, fail):
            self.fail = fail
        def close(self):
            calls.append(('writer', self.fail))
            if self.fail:
                raise OSError('transport close failed')
    class Server:
        def close(self):
            calls.append('server-close')
        async def wait_closed(self):
            calls.append('server-joined')
    async def finish_peer():
        await asyncio.sleep(.02)
        calls.append('peer-joined')
    record = SimpleNamespace(address=tmp_path / 'missing.sock')
    registry = SimpleNamespace(remove=lambda value: calls.append('registry-removed'))
    endpoint = LocalServer(SimpleNamespace(record=record, registry=registry))
    endpoint.server = Server()
    endpoint.writers = {Writer(True), Writer(False)}
    peer = asyncio.create_task(finish_peer())
    endpoint.tasks.add(peer)
    try:
        with pytest.raises(OSError, match='transport close failed'):
            await endpoint.close()
        assert ('writer', False) in calls and peer.done()
        assert 'server-joined' in calls and 'registry-removed' in calls
    finally:
        await peer
