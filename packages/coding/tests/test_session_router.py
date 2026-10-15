import pytest


def test_session_router_requires_exact_live_owner_and_workspace(coding_session, tmp_path):
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.ownership import OwnerRecord
    from xdog.coding.core.sessions.router import SessionRouter

    controller = SessionController(coding_session, bound=True)
    record = OwnerRecord(coding_session.session_id, tmp_path, tmp_path / "s.sock", controller.generation)
    router = SessionRouter()
    router.register(controller, record)
    assert router.resolve(record.session_id, tmp_path) is controller
    with pytest.raises(RuntimeError, match="unknown"):
        router.resolve("missing", tmp_path)
    with pytest.raises(RuntimeError, match="workspace"):
        router.resolve(record.session_id, tmp_path / "different")
    router.retire(record.session_id, "wrong-generation")
    assert router.resolve(record.session_id, tmp_path) is controller
    router.retire(record.session_id, record.generation)
    with pytest.raises(RuntimeError, match="inactive"):
        router.resolve(record.session_id, tmp_path)
