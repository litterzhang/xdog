"""Real process locks, not atomic JSON writes, protect session ownership."""

import subprocess
import sys

import pytest


def test_second_writer_is_rejected_and_release_is_reusable(tmp_path):
    from xdog.coding.core.sessions.ownership import SessionLease

    first = SessionLease.acquire(tmp_path / "owners", "s1")
    try:
        with pytest.raises(RuntimeError, match="owned"):
            SessionLease.acquire(tmp_path / "owners", "s1")
        code = ("from pathlib import Path; "
                "from xdog.coding.core.sessions.ownership import SessionLease; "
                "SessionLease.acquire(Path(__import__('sys').argv[1]), 's1')")
        child = subprocess.run([sys.executable, "-c", code, str(tmp_path / "owners")], capture_output=True, text=True)
        assert child.returncode != 0 and "owned" in child.stderr
    finally:
        first.close()
    second = SessionLease.acquire(tmp_path / "owners", "s1")
    second.close()


def test_symlink_and_public_directory_are_rejected(tmp_path):
    from xdog.coding.core.sessions.ownership import SessionLease

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="private|symbolic"):
        SessionLease.acquire(link, "s1")
    real.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        SessionLease.acquire(real, "s1")


def test_registry_rejects_address_outside_private_directory(tmp_path):
    from xdog.coding.core.sessions.ownership import OwnerRecord, OwnerRegistry

    registry = OwnerRegistry(tmp_path / "owners")
    with pytest.raises(ValueError, match="address"):
        registry.publish(OwnerRecord("s1", tmp_path, tmp_path / "outside.sock", "g1"))


def test_factory_claims_before_build_and_releases_failed_start(tmp_path, monkeypatch):
    from xdog.coding.core import sdk
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory

    monkeypatch.setattr(sdk, "get_sessions_dir", lambda: tmp_path / "sessions")

    def build(options, *, new_session_id):
        with pytest.raises(RuntimeError, match="owned"):
            SessionLease.acquire(runtime_directory(tmp_path / "sessions"), "s1")
        raise ValueError("bootstrap failed")

    monkeypatch.setattr(sdk, "_build_agent_session", build, raising=False)
    monkeypatch.setattr(
        sdk.GlobalConfig, "load", lambda *args: (_ for _ in ()).throw(AssertionError("unexpected builder"))
    )
    with pytest.raises(ValueError, match="bootstrap failed"):
        sdk.create_agent_session(sdk.CreateSessionOptions(resume_id="s1"))
    lease = SessionLease.acquire(runtime_directory(tmp_path / "sessions"), "s1")
    lease.close()


def test_navigation_cannot_load_another_writers_history(coding_session, monkeypatch):
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory

    other = coding_session.session_manager.create_session(model="test", working_dir=coding_session.working_dir)
    directory = runtime_directory(coding_session.session_manager._dir)
    held = SessionLease.acquire(directory, other.session_id)
    observed = []
    original = coding_session.session_manager.load_session
    monkeypatch.setattr(
        coding_session.session_manager, "load_session", lambda sid: (observed.append(sid), original(sid))[1]
    )
    monkeypatch.setattr(coding_session, "_activate_session", lambda loaded: True)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            coding_session.switch_session(other.session_id)
        assert observed == []
    finally:
        held.close()


def test_process_death_releases_lock_without_pid_assumptions(tmp_path):
    import select
    from pathlib import Path

    from xdog.coding.core.sessions.ownership import SessionLease

    script = Path(__file__).with_name("session_owner_process.py")
    child = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path / "owners"), "s1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="owned"):
            SessionLease.acquire(tmp_path / "owners", "s1")
        child.kill()
        child.wait(timeout=3)
        lease = SessionLease.acquire(tmp_path / "owners", "s1")
        lease.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()
