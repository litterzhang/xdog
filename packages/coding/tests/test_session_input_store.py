"""Task history is durable but cannot replay tasks or contaminate model history."""

import pytest


async def test_request_history_roundtrip_without_model_admission(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.models import InputRecord
    from xdog.coding.core.sessions.store import InputStore

    store = InputStore(coding_session)
    value = SessionInput("r1", "acp", "Eyes", "Review tests", (), {})
    await store.accept(InputRecord(value, "", "g1", "queued"))
    assert coding_session.messages == []
    assert store.records()[0].status == "queued"
    restored = InputStore(coding_session)
    assert restored.records()[0].status == "interrupted"
    await store.transition("r1", "succeeded")
    assert InputStore(coding_session).records()[0].status == "succeeded"


async def test_commit_failure_does_not_mutate_or_advertise(coding_session, monkeypatch):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.models import InputRecord
    from xdog.coding.core.sessions.store import InputStore

    store = InputStore(coding_session)

    def fail(records):
        raise OSError("disk failure")

    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(OSError, match="disk failure"):
        await store.accept(InputRecord(SessionInput("r1", "acp", "Eyes", "Review", (), {}), "", "g1", "queued"))
    assert store.records() == ()
    assert coding_session.messages == []


async def test_deleting_session_removes_only_its_request_history(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.models import InputRecord
    from xdog.coding.core.sessions.store import InputStore

    store = InputStore(coding_session)
    await store.accept(InputRecord(SessionInput("r1", "acp", "peer", "Review", (), {}), "", "g1", "queued"))
    assert store.path.exists()
    assert coding_session.session_manager.delete_session(coding_session.session_id)
    assert not store.path.exists()


async def test_completed_input_cannot_be_reactivated(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.models import InputRecord
    from xdog.coding.core.sessions.store import InputStore

    store = InputStore(coding_session)
    await store.accept(InputRecord(SessionInput("r1", "acp", "peer", "Review", (), {}), "", "g1", "succeeded"))
    with pytest.raises(ValueError, match="terminal"):
        await store.transition("r1", "running")
