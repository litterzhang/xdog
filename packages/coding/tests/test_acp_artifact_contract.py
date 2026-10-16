"""Installed-wheel probes must cover data-only intake and a real independent-message owner turn."""
import runpy
from pathlib import Path

import pytest


def probes():
    return runpy.run_path(str(Path(__file__).parents[3]/'scripts'/'verify_acp_artifacts.py'))


def test_artifact_intake_probe_uses_current_pending_state():
    source = probes()['CODING']
    assert "'pending_review'" not in source and "'pending'" in source
    compile(source,'wheel-intake-probe','exec')


def test_artifact_probe_includes_a_deterministic_real_owner_batch():
    source = probes()['CODING_OWNER']
    assert 'range(5)' in source and 'await session.send_message' in source
    assert 'len(calls)==1' in source and 'UserMessage' in source
    assert 'xdog.coding.core.agent_session' in source and 'sys.prefix' in source
    assert 'SessionLease.acquire' in source
    assert source.index('session.dispose()') < source.index('assert session._lease is None')
    compile(source,'wheel-owner-probe','exec')


async def test_sdk_session_retains_writer_between_calls_and_releases_on_dispose(coding_session):
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory

    directory = runtime_directory(coding_session.session_manager._dir)
    try:
        await coding_session.send_message('one actual turn')
        assert coding_session.runtime is None and coding_session._sdk_controller is None
        assert coding_session._lease is not None and coding_session._lease._fd>=0
        with pytest.raises(RuntimeError,match='owned'):
            SessionLease.acquire(directory,coding_session.session_id)
        coding_session.dispose()
        assert coding_session._lease is None
        another = SessionLease.acquire(directory,coding_session.session_id)
        another.close()
    finally:
        coding_session.dispose()
