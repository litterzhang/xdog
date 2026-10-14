"""Reviewed name persistence cannot race the original producer's transcript commit."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent

from packages.coding.tests.test_rpc_extensions import close, session


async def test_before_replace_revocation_refuses_name_save_without_overwriting_transcript(tmp_path, monkeypatch):
    import threading
    owner, _ = session(tmp_path)
    owner.agent.append_message(AssistantMessage(content=(TextContent(text='KEEP FINAL TRANSCRIPT'),)))
    owner._persist()
    before = owner.session_manager.load_session(owner.session_id)
    live, entered, release = [True], threading.Event(), threading.Event()
    save = owner.session_manager.save_session
    def held(data, *, before_replace=None):
        def guard():
            entered.set()
            assert release.wait(5)
            before_replace()
        save(data, before_replace=guard)
    monkeypatch.setattr(owner.session_manager, 'save_session', held)
    task = asyncio.create_task(asyncio.to_thread(owner.rename_session, 'MUST NOT SAVE',
                                                expected_name='', alive=lambda: live[0]))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.003)
        live[0] = False
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        after = owner.session_manager.load_session(owner.session_id)
        assert after.name == before.name == '' and after.messages == before.messages
        assert owner.session_data.name == ''
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(owner)
