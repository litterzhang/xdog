"""Installed-wheel probes must cover data-only intake and a real independent-message owner turn."""
import runpy
from pathlib import Path


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
    compile(source,'wheel-owner-probe','exec')
